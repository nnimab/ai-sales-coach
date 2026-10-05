"""假的 OpenAI（只用來測試）：照 Sign in with ChatGPT 文件的格式回應
discovery、JWKS、authorize（自動同意）、token（換 code、續期會換新 refresh token）、revoke、/v1/models、/v1/responses（SSE）。
用法：python tools/fakes/fake_openai.py --port 9102
程式裡：srv = fake_openai.start()；srv.url、srv.state（OpenAIState，可改 knobs）、srv.stop()。
子程序測試可以用 POST /__control（改 knobs）、GET /__state、POST /__expire（讓 access token 立刻過期）。"""

import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import testkeys  # noqa: E402
from common import Handler, b64u, jwk, make_jwt, run_forever, sample, serve  # noqa: E402

PLAN_SCOPE = "chatgpt.tokens.use.direct"
RESOURCE = "https://api.openai.com/v1"
UNSUPPORTED = (
    "background", "conversation", "max_output_tokens", "max_tool_calls", "metadata", "moderation", "multi_agent",
    "prompt", "prompt_cache_retention", "safety_identifier", "temperature", "top_logprobs", "top_p", "truncation",
    "user", "previous_response_id",
)
HOST_RE = re.compile(r"^(urn:uuid:[0-9a-fA-F-]{36}|urn:ietf:params:oauth:jwk-thumbprint:.+|did:key:.+)$")
DEFAULT_KNOBS = {
    "consent": "allow",            # deny：使用者按拒絕
    "plan_scope": True,            # False：使用者沒同意用方案
    "omit_client_id": False,       # True：新註冊卻沒回 client_id
    "expires_in": 3600,
    "earliest_refresh": 0,         # 幾秒後才可以續期（0 = 馬上）
    "earliest_refresh_iso": False,  # True：earliest_refresh_at 用 ISO 字串
    "refresh_mode": "ok",          # invalidated：每次續期都失敗（devkit #5）；nested：錯誤放在 {"error":{code}}
    "refresh_delay": 0.0,
    "sign_with_other_key": False,  # True：ID token 用不在 JWKS 的金鑰簽
    "id_token_iss": "",            # 指定 iss（測驗證）
    "reply": "您好，我是客戶。我想先問一下，這個商品的風險大概是多少？",
    "chunk": 4,
    "delay": 0.0,                  # 每段之間等幾秒
    "pre_error": None,             # {"status":429,"code":"…","param":"","times":1}：開始串流前就回錯
    "detail_503": False,           # True：回 503 {"detail": …}
    "fail_after": None,            # {"after":2,"code":"subscription_sharing_usage_limit_exceeded"}：串流中途 response.failed
    "error_event_after": None,     # {"after":0,"code":"…","param":""}：送 type=error 事件
    "incomplete_after": None,      # 送 N 段後 response.incomplete
    "interrupt_after": None,       # 送 N 段後直接斷線（沒有 completed）
    "reject_params": [],           # ["text"] / ["reasoning"]：回 unsupported_capability
    "models_shape": "models",      # data：標準的 data[] 格式
}


def _now():
    return int(time.time())


class OpenAIState:
    def __init__(self):
        self.lock = threading.RLock()
        self.base = ""
        self.user = {"sub": "user-123", "email": "rep@example.test"}
        self.knobs = json.loads(json.dumps(DEFAULT_KNOBS))
        self.clients = {}   # client_id → {"sub","email","path","hosts":[]}
        self.codes = {}
        self.access = {}    # token → {"client_id","sub","exp","scope"}
        self.refresh = {}   # token → {"client_id","sub","scope","status"}
        self.requests = []  # /v1/responses 收到的內容
        self.token_calls = []  # (grant_type, client_id, ok)
        self.revokes = []
        self.authorize_calls = []  # authorize 收到的參數（測試檢查用）
        self.disconnects = 0
        self.pre_error_used = 0

    def reset(self):
        with self.lock:
            base = self.base
            self.__init__()
            self.base = base

    def snapshot(self):
        with self.lock:
            return {
                "requests": self.requests, "token_calls": self.token_calls, "revokes": self.revokes,
                "authorize_calls": self.authorize_calls, "clients": {k: dict(v) for k, v in self.clients.items()},
                "disconnects": self.disconnects, "knobs": self.knobs,
                "active_refresh": sum(1 for r in self.refresh.values() if r["status"] == "active"),
            }

    # ---- 發 token

    def id_token(self, client_id, sub, email, nonce=None):
        now = _now()
        claims = {"iss": self.knobs.get("id_token_iss") or self.base, "aud": client_id, "sub": sub, "email": email,
                  "email_verified": True, "iat": now, "exp": now + 3600, "auth_time": now}
        if nonce is not None:
            claims["nonce"] = nonce
        if self.knobs.get("sign_with_other_key"):
            return make_jwt(claims, testkeys.OTHER_N, testkeys.OTHER_D, testkeys.KID)
        return make_jwt(claims, testkeys.N, testkeys.D, testkeys.KID)

    def issue(self, client_id, sub, email, scope, nonce=None):
        access, refresh = "at_" + secrets.token_hex(12), "rt_" + secrets.token_hex(12)
        exp = _now() + int(self.knobs["expires_in"])
        self.access[access] = {"client_id": client_id, "sub": sub, "exp": exp, "scope": scope}
        self.refresh[refresh] = {"client_id": client_id, "sub": sub, "scope": scope, "status": "active", "email": email}
        early = _now() + int(self.knobs.get("earliest_refresh") or 0)
        if self.knobs.get("earliest_refresh_iso"):
            early = datetime.fromtimestamp(early, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return {
            "access_token": access, "refresh_token": refresh, "id_token": self.id_token(client_id, sub, email, nonce),
            "token_type": "Bearer", "expires_in": int(self.knobs["expires_in"]), "scope": scope,
            "earliest_refresh_at": early,
        }

    def check_bearer(self, header):
        token = header[7:] if header and header.startswith("Bearer ") else ""
        with self.lock:
            info = self.access.get(token)
        if not info or info["exp"] <= _now():
            return None
        return info


def _err(code, message, param=None):
    return {"error": {"message": message, "type": "invalid_request_error", "param": param, "code": code}}


class H(Handler):
    # ---- GET

    def do_GET(self):
        st = self.state
        u = urlparse(self.path)
        if u.path == "/.well-known/openid-configuration":
            b = st.base
            return self.reply(200, {
                "issuer": b, "authorization_endpoint": b + "/api/accounts/authorize",
                "token_endpoint": b + "/api/accounts/oauth/token", "revocation_endpoint": b + "/api/accounts/oauth/revoke",
                "jwks_uri": b + "/.well-known/jwks.json", "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none", "client_secret_basic", "client_secret_post"],
                "id_token_signing_alg_values_supported": ["RS256"],
            })
        if u.path == "/.well-known/jwks.json":
            return self.reply(200, {"keys": [jwk(testkeys.N, testkeys.E, testkeys.KID)]})
        if u.path == "/api/accounts/authorize":
            return self.authorize({k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()})
        if u.path == "/v1/models":
            if not st.check_bearer(self.headers.get("Authorization")):
                return self.reply(401, _err("subscription_sharing_invalid_user", "invalid user"))
            rows = [
                {"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol", "visibility": "list"},
                {"slug": "gpt-5.6-luna", "display_name": "GPT-5.6 Luna", "visibility": "list"},
                {"slug": "internal-hidden", "display_name": "Hidden", "visibility": "hide"},
            ]
            if st.knobs.get("models_shape") == "data":
                return self.reply(200, {"object": "list", "data": [{"id": r["slug"], "object": "model"} for r in rows[:2]]})
            return self.reply(200, {"models": rows})
        if u.path == "/__state":
            return self.reply(200, st.snapshot())
        self.reply(404, {"detail": "Not Found"})

    def authorize(self, q):
        st = self.state
        with st.lock:
            st.authorize_calls.append(dict(q))
        need = ("client_id", "response_type", "redirect_uri", "scope", "resource", "state", "nonce",
                "code_challenge_method", "code_challenge", "ext_agent_host_id")
        missing = [k for k in need if not q.get(k)]
        if missing:
            return self.reply(400, {"error": "invalid_request", "error_description": "missing " + ",".join(missing)})
        r = urlparse(q["redirect_uri"])
        # 文件：http、127.0.0.1（不能用 localhost）、/auth/callback，只有埠號可以變
        if r.scheme != "http" or r.hostname != "127.0.0.1" or not r.port or r.query:
            return self.reply(400, {"error": "invalid_request", "error_description": "redirect_uri must be http://127.0.0.1:PORT/…"})
        if q["response_type"] != "code" or q["code_challenge_method"] != "S256":
            return self.reply(400, {"error": "invalid_request", "error_description": "bad response_type or PKCE method"})
        if not re.match(r"^[A-Za-z0-9_-]{43}$", q["code_challenge"]):
            return self.reply(400, {"error": "invalid_request", "error_description": "bad code_challenge"})
        if q["resource"] != RESOURCE:
            return self.reply(400, {"error": "invalid_target", "error_description": "bad resource"})
        if not HOST_RE.match(q["ext_agent_host_id"]):
            return self.reply(400, {"error": "invalid_request", "error_description": "bad ext_agent_host_id"})
        scopes = q["scope"].split()
        if "openid" not in scopes:
            return self.reply(400, {"error": "invalid_scope", "error_description": "openid required"})
        with st.lock:
            cid = q["client_id"]
            new = cid == "dynamic_agent_client"
            if new:
                if not q.get("agent_name_hint"):
                    return self.reply(400, {"error": "invalid_request", "error_description": "agent_name_hint required"})
                cid = "oaiapp_" + secrets.token_hex(8)
                st.clients[cid] = {"sub": st.user["sub"], "email": st.user["email"], "path": r.path,
                                   "hosts": [q["ext_agent_host_id"]], "name": q["agent_name_hint"]}
            else:
                client = st.clients.get(cid)
                if not client:
                    return self.reply(400, {"error": "invalid_client", "error_description": "unknown client"})
                if q.get("agent_name_hint"):
                    return self.reply(400, {"error": "invalid_request", "error_description": "agent_name_hint only on registration"})
                if r.path != client["path"]:
                    return self.reply(400, {"error": "invalid_request", "error_description": "redirect path changed"})
                if q["ext_agent_host_id"] not in client["hosts"]:
                    client["hosts"].append(q["ext_agent_host_id"])
            if st.knobs.get("consent") == "deny":
                return self.redirect(q["redirect_uri"] + "?" + urlencode({"error": "access_denied", "state": q["state"]}))
            granted = [s for s in scopes if s != PLAN_SCOPE or st.knobs.get("plan_scope")]
            code = "code_" + secrets.token_hex(10)
            st.codes[code] = {
                "client_id": cid, "redirect_uri": q["redirect_uri"], "challenge": q["code_challenge"],
                "nonce": q["nonce"], "scope": " ".join(sorted(granted)), "exp": _now() + 120,
                "sub": st.user["sub"], "email": st.user["email"],
            }
        out = {"code": code, "scope": " ".join(sorted(granted)), "state": q["state"]}
        if new and not st.knobs.get("omit_client_id"):
            out["client_id"] = cid
        self.redirect(q["redirect_uri"] + "?" + urlencode(out))

    # ---- POST

    def do_POST(self):
        u = urlparse(self.path)
        st = self.state
        if u.path == "/api/accounts/oauth/token":
            return self.token(self.body_form())
        if u.path == "/api/accounts/oauth/revoke":
            f = self.body_form()
            with st.lock:
                st.revokes.append({"client_id": f.get("client_id"), "hint": f.get("token_type_hint")})
                r = st.refresh.get(f.get("token", ""))
                if r and r["client_id"] == f.get("client_id"):
                    r["status"] = "revoked"
            return self.reply(200, None)
        if u.path == "/v1/responses":
            return self.handle_responses()
        if u.path == "/__control":
            body = self.body_json() or {}
            with st.lock:
                st.knobs.update(body)
                st.pre_error_used = 0
            return self.reply(200, {"ok": True, "knobs": st.knobs})
        if u.path == "/__reset":
            st.reset()
            return self.reply(200, {"ok": True})
        if u.path == "/__expire":
            with st.lock:
                for info in st.access.values():
                    info["exp"] = 0
            return self.reply(200, {"ok": True})
        self.reply(404, {"detail": "Not Found"})

    def token(self, f):
        st = self.state
        grant = f.get("grant_type")
        if grant == "authorization_code":
            with st.lock:
                c = st.codes.pop(f.get("code", ""), None)
                ok = bool(c) and c["exp"] > _now()
                if not ok:
                    st.token_calls.append((grant, f.get("client_id"), False))
                    return self.reply(400, {"error": "invalid_grant", "error_description": "code invalid or used"})
                if f.get("client_id") != c["client_id"]:
                    st.token_calls.append((grant, f.get("client_id"), False))
                    return self.reply(401, {"error": "invalid_client", "error_description": "client mismatch"})
                verifier = f.get("code_verifier", "")
                if b64u(hashlib.sha256(verifier.encode("ascii")).digest()) != c["challenge"]:
                    st.token_calls.append((grant, f.get("client_id"), False))
                    return self.reply(400, {"error": "invalid_grant", "error_description": "PKCE verification failed"})
                if f.get("redirect_uri") != c["redirect_uri"] or f.get("resource") != RESOURCE:
                    st.token_calls.append((grant, f.get("client_id"), False))
                    return self.reply(400, {"error": "invalid_grant", "error_description": "redirect_uri or resource mismatch"})
                st.token_calls.append((grant, f.get("client_id"), True))
                return self.reply(200, st.issue(c["client_id"], c["sub"], c["email"], c["scope"], c["nonce"]))
        if grant == "refresh_token":
            delay = float(st.knobs.get("refresh_delay") or 0)
            if delay:
                time.sleep(delay)
            with st.lock:
                if "scope" in f:
                    st.token_calls.append((grant, f.get("client_id"), False))
                    return self.reply(400, {"error": "invalid_request", "error_description": "omit scope on refresh"})
                if st.knobs.get("refresh_mode") == "invalidated":
                    st.token_calls.append((grant, f.get("client_id"), False))
                    return self.reply(400, {"error": "invalid_grant", "error_description": "refresh_token_invalidated"})
                r = st.refresh.get(f.get("refresh_token", ""))
                if not r or r["client_id"] != f.get("client_id") or f.get("resource") != RESOURCE:
                    st.token_calls.append((grant, f.get("client_id"), False))
                    if st.knobs.get("refresh_mode") == "nested":
                        return self.reply(400, {"error": {"code": "invalid_refresh_token", "message": "bad token"}})
                    return self.reply(400, {"error": "invalid_grant", "error_description": "invalid_refresh_token"})
                if r["status"] != "active":
                    st.token_calls.append((grant, f.get("client_id"), False))
                    return self.reply(400, {"error": "invalid_grant", "error_description": "refresh_token_reused"})
                r["status"] = "used"
                st.token_calls.append((grant, f.get("client_id"), True))
                out = st.issue(r["client_id"], r["sub"], r["email"], r["scope"])
                return self.reply(200, out)
        self.reply(400, {"error": "unsupported_grant_type"})

    def handle_responses(self):
        st = self.state
        info = st.check_bearer(self.headers.get("Authorization"))
        body = self.body_json()
        if not info:
            return self.reply(401, _err("subscription_sharing_invalid_user", "The subscriber context could not be validated."))
        if PLAN_SCOPE not in info["scope"].split():
            return self.reply(403, _err("chatpass_v2_scope_not_authorized", "scope not authorized"))
        if not isinstance(body, dict):
            return self.reply(400, _err("invalid_request_error", "bad json"))
        with st.lock:
            st.requests.append(body)
            k = dict(st.knobs)
        for field in UNSUPPORTED:
            if field in body:
                return self.reply(400, _err("subscription_sharing_unsupported_capability", f"{field} is not supported", field))
        if body.get("store") is not False or body.get("stream") is not True:
            return self.reply(400, _err("invalid_request_error", "store must be false and stream must be true"))
        if not isinstance(body.get("input"), list):
            return self.reply(400, _err("invalid_request_error", "input must be an array", "input"))
        if any(isinstance(i, dict) and i.get("role") == "system" for i in body["input"]):
            return self.reply(400, _err("invalid_request_error", "system messages are not supported", "input"))
        for field in k.get("reject_params") or []:
            if field in body:
                param = {"text": "text.format", "reasoning": "reasoning.effort"}.get(field, field)
                return self.reply(400, _err("subscription_sharing_unsupported_capability", f"{param} is not supported", param))
        pre = k.get("pre_error")
        if pre:
            with st.lock:
                times = int(pre.get("times", 1))
                use = times < 0 or st.pre_error_used < times
                if use:
                    st.pre_error_used += 1
            if use:
                return self.reply(int(pre.get("status", 400)), _err(pre.get("code", ""), "fake error", pre.get("param")),
                                  {"x-request-id": "req_fake"})
        if k.get("detail_503"):
            return self.reply(503, {"detail": "Direct routing is unavailable."})
        fmt = ((body.get("text") or {}).get("format") or {})
        text = json.dumps(sample(fmt["schema"]), ensure_ascii=False) if fmt.get("type") == "json_schema" else k["reply"]
        n = max(1, int(k.get("chunk") or 4))
        pieces = [text[i:i + n] for i in range(0, len(text), n)]
        self.stream(pieces, k)

    def stream(self, pieces, k):
        st = self.state
        rid = "resp_" + secrets.token_hex(6)

        def ev(obj):
            line = f"event: {obj['type']}\ndata: {json.dumps(obj, ensure_ascii=False)}\n\n"
            self.wfile.write(line.encode("utf-8"))
            self.wfile.flush()

        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            ev({"type": "response.created", "response": {"id": rid, "status": "in_progress"}})
            ev({"type": "response.in_progress", "response": {"id": rid, "status": "in_progress"}})
            ev({"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "role": "assistant"}})
            ev({"type": "response.content_part.added", "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": ""}})
            for i, piece in enumerate(pieces):
                if k.get("fail_after") is not None and i == int(k["fail_after"].get("after", 0)):
                    ev({"type": "response.failed", "response": {"id": rid, "status": "failed", "error": {
                        "code": k["fail_after"].get("code", "server_error"), "message": "fake failure"}}})
                    return
                if k.get("error_event_after") is not None and i == int(k["error_event_after"].get("after", 0)):
                    e = k["error_event_after"]
                    ev({"type": "error", "code": e.get("code", "server_error"), "message": "fake error", "param": e.get("param")})
                    return
                if k.get("incomplete_after") is not None and i == int(k["incomplete_after"]):
                    ev({"type": "response.incomplete", "response": {"id": rid, "status": "incomplete",
                                                                     "incomplete_details": {"reason": "max_output_tokens"}}})
                    return
                if k.get("interrupt_after") is not None and i == int(k["interrupt_after"]):
                    return  # 直接斷線
                ev({"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": piece})
                if k.get("delay"):
                    time.sleep(float(k["delay"]))
            full = "".join(pieces)
            ev({"type": "response.output_text.done", "output_index": 0, "content_index": 0, "text": full})
            ev({"type": "response.completed", "response": {"id": rid, "status": "completed", "output": []}})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            with st.lock:
                st.disconnects += 1


def start(port=None):
    state = OpenAIState()
    srv = serve(H, port, state)
    state.base = srv.url
    return srv


if __name__ == "__main__":
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1]) if "--port" in args else 9102
    run_forever(H, port, OpenAIState(), "fake openai")
