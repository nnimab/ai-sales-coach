"""ChatGPT 訂閱當文字大腦（Sign in with ChatGPT，用自己的 Plus／Pro 方案額度）。
登入、續期、串流回覆都照 OpenAI 文件（developers.openai.com/siwc）；只用 Python 標準函式庫。
任何失敗都丟 BrainError(fallback=True)，讓伺服器改用 Gemini 等備援。"""

import base64
import copy
import hashlib
import hmac
import http.client
import json
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from urllib.parse import quote, quote_plus, urlencode, urlparse

from storage import StoreError

PLAN_SCOPE = "chatgpt.tokens.use.direct"
DYNAMIC_CLIENT = "dynamic_agent_client"
MANAGE_USAGE_URL = "https://chatgpt.com/settings/usage"
APP_ID = "ai-sales-coach"
DEFAULT_SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
DEFAULT_URLS = {
    "discovery_url": "https://auth.openai.com/.well-known/openid-configuration",
    "authorize_url": "https://auth.openai.com/api/accounts/authorize",
    "token_url": "https://auth.openai.com/api/accounts/oauth/token",
    "revoke_url": "https://auth.openai.com/api/accounts/oauth/revoke",
    "jwks_url": "https://auth.openai.com/.well-known/jwks.json",
    "issuer": "https://auth.openai.com",
}
_ENV_URLS = {
    "discovery_url": "CHATGPT_DISCOVERY_URL", "authorize_url": "CHATGPT_AUTHORIZE_URL", "token_url": "CHATGPT_TOKEN_URL",
    "revoke_url": "CHATGPT_REVOKE_URL", "jwks_url": "CHATGPT_JWKS_URL", "issuer": "CHATGPT_ISSUER",
}
_DISCOVERY_FIELDS = {
    "authorize_url": "authorization_endpoint", "token_url": "token_endpoint", "revoke_url": "revocation_endpoint",
    "jwks_url": "jwks_uri", "issuer": "issuer",
}
# 文件列為不支援、一律不送的欄位（送了會被拒）
UNSUPPORTED_FIELDS = (
    "background", "conversation", "max_output_tokens", "max_tool_calls", "metadata", "moderation", "multi_agent",
    "prompt", "prompt_cache_retention", "safety_identifier", "temperature", "top_logprobs", "top_p", "truncation",
    "user", "previous_response_id",
)
# 續期時代表 refresh token 已經不能用、要重新登入的錯誤碼
_REFRESH_DEAD = {
    "invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
    "refresh_token_invalidated", "refresh_token_reused", "invalid_client",
}
_EXPORT_FIELDS = (
    "client_id", "access_token", "refresh_token", "id_token", "expires_at", "earliest_refresh_at",
    "scope", "email", "sub", "ext_agent_host_id",
)
_SENTENCE_END = "。！？!?\n"
_CLIENT_RE = re.compile(r"^[A-Za-z0-9_.:-]{3,200}$")
_STATE_RE = re.compile(r"^[A-Za-z0-9_-]{16,200}$")
_UID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CODE_RE = re.compile(r"^[a-z0-9_.]{1,60}$")
_HOST_RE = re.compile(
    r"^(urn:uuid:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|urn:ietf:params:oauth:jwk-thumbprint:[A-Za-z0-9:_-]{10,200}|did:key:[A-Za-z0-9]{10,200})$"
)
_DIGEST_INFO_SHA256 = bytes.fromhex("3031300d060960864801650304020105000420")


class BrainError(Exception):
    """code：OpenAI 的錯誤碼，或本機的 not_signed_in、needs_login、no_plan_scope、cooldown、timeout、network、
    stream_interrupted、incomplete、blocked、cancelled。started：已經送出部分文字（不能再換備援重來）。"""

    def __init__(self, message, code="", status=0, param="", fallback=True, started=False):
        super().__init__(message)
        self.code = code
        self.status = status
        self.param = param
        self.fallback = fallback
        self.started = started


class _Fail(Exception):
    """內部用：OpenAI 回了錯誤（HTTP 錯誤或串流裡的錯誤事件），交給 _on_error 決定重試或放棄。"""

    def __init__(self, status, err, request_id=""):
        super().__init__(err.get("code") or status)
        self.status = status
        self.err = err
        self.request_id = request_id


def _log(msg):
    # 只記錯誤碼和狀態，絕不記 token
    sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] [ChatGPT] {msg}\n")


def _b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text):
    text = str(text)
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _rand(n=32):
    return _b64u(secrets.token_bytes(n))


def pkce_pair():
    """(verifier, challenge)：verifier 是 32 個亂數位元組的 base64url，challenge = base64url(sha256(verifier))。"""
    verifier = _rand(32)
    return verifier, _b64u(hashlib.sha256(verifier.encode("ascii")).digest())


def _num(value, default):
    try:
        return float(value) if str(value or "").strip() else default
    except ValueError:
        return default


def _epoch(value):
    """earliest_refresh_at 可能是秒數、毫秒或 ISO 時間字串，一律換成秒數；看不懂就 0。"""
    if isinstance(value, bool) or value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        v = float(value)
        return int(v / 1000 if v > 1e12 else v)
    s = str(value).strip()
    if re.match(r"^\d+(\.\d+)?$", s):
        return _epoch(float(s))
    s = s.replace("Z", "+00:00").replace("z", "+00:00")
    s = re.sub(r"(\.\d{1,6})\d*", r"\1", s)  # Python 3.10 只認 6 位以內的小數秒
    m = re.match(r"^(.*\.)(\d{1,5})([+-].*)?$", s)
    if m:  # 3.10 也不認 1～5 位小數：補到 6 位
        s = m.group(1) + m.group(2).ljust(6, "0") + (m.group(3) or "")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _json(raw):
    try:
        data = json.loads(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
    except (ValueError, TypeError, AttributeError):
        return {}
    return data if isinstance(data, dict) else {}


def _opener_for(url, redirects=True):
    host = (urlparse(url).hostname or "").lower()
    handlers = []
    if host in ("127.0.0.1", "localhost", "::1"):
        handlers.append(urllib.request.ProxyHandler({}))  # 本機測試不要走代理
    if not redirects:
        handlers.append(_NoRedirect())
    return urllib.request.build_opener(*handlers)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # 帶同步密碼的請求不跟著轉址走，免得密碼被送到別的網址
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# ---------------------------------------------------------------- RS256（純 Python，不用加密套件）


def rs256_verify(n, e, signing_input, signature):
    """PKCS#1 v1.5 + SHA-256 簽章檢查：自己組出應該長的樣子再整段比對，不去解析，避免偽造。"""
    if not isinstance(n, int) or not isinstance(e, int) or n <= 0 or e <= 0:
        return False
    k = (n.bit_length() + 7) // 8
    if k < 256 or len(signature) != k:  # 至少 2048 位元
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    t = _DIGEST_INFO_SHA256 + hashlib.sha256(signing_input).digest()
    expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(em, expected)


def jwk_numbers(jwk):
    """JWK（kty=RSA）→ (n, e)；格式不對回 None。"""
    try:
        if jwk.get("kty") != "RSA":
            return None
        return int.from_bytes(_b64d(jwk["n"]), "big"), int.from_bytes(_b64d(jwk["e"]), "big")
    except (KeyError, ValueError, TypeError, AttributeError):
        return None


def decode_jwt(token):
    """(header, claims, signing_input, signature)；格式不對丟 ValueError。不檢查簽章。"""
    parts = str(token).split(".")
    if len(parts) != 3:
        raise ValueError("JWT 格式不對")
    header = json.loads(_b64d(parts[0]))
    claims = json.loads(_b64d(parts[1]))
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise ValueError("JWT 格式不對")
    return header, claims, (parts[0] + "." + parts[1]).encode("ascii"), _b64d(parts[2])


# ---------------------------------------------------------------- 小工具


def strict_schema(schema):
    """json_schema strict 模式要求：每個 object 都要 additionalProperties:false，而且列出全部欄位為 required。"""
    out = copy.deepcopy(schema)

    def walk(node):
        if isinstance(node, list):
            for x in node:
                walk(x)
            return
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" or "properties" in node:
            props = node.get("properties") or {}
            node["additionalProperties"] = False
            node["required"] = list(props.keys())
        for key, value in list(node.items()):
            if key in ("properties", "$defs", "definitions") and isinstance(value, dict):
                for sub in value.values():
                    walk(sub)
            elif key in ("items", "anyOf", "oneOf", "allOf", "prefixItems", "not"):
                walk(value)

    walk(out)
    return out


def parse_models(data):
    """GET /v1/models：文件的格式是 models[]（slug、display_name、visibility），只留 visibility=list，照伺服器順序；
    萬一是標準的 data[]，就用 id。"""
    rows = []
    if isinstance(data.get("models"), list):
        for m in data["models"]:
            if isinstance(m, dict) and m.get("visibility") == "list" and isinstance(m.get("slug"), str) and m["slug"]:
                rows.append({"slug": m["slug"], "display_name": str(m.get("display_name") or m["slug"])})
    elif isinstance(data.get("data"), list):
        for m in data["data"]:
            if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"]:
                rows.append({"slug": m["id"], "display_name": str(m.get("display_name") or m["id"])})
    return rows


def parse_sse(lines):
    """把 SSE 一行一行的位元組組成事件：產生 (event 名稱, data 文字)。"""
    event, data = "", []
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.rstrip("\r\n")
        if not line:
            if data:
                yield event, "\n".join(data)
            event, data = "", []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
    if data:
        yield event, "\n".join(data)


def _api_error(raw):
    """HTTP 錯誤內容：{"error":{code,message,param}}、{"error":"..."} 或 {"detail":...} 都接受。"""
    data = _json(raw)
    err = data.get("error")
    if isinstance(err, dict):
        return {"code": str(err.get("code") or err.get("type") or ""), "message": str(err.get("message") or "")[:300],
                "param": str(err.get("param") or "")}
    if isinstance(err, str):
        return {"code": err, "message": str(data.get("error_description") or data.get("message") or "")[:300], "param": ""}
    detail = data.get("detail")
    if detail is not None:
        return {"code": "", "message": (detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False))[:300], "param": ""}
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")
    return {"code": "", "message": text[:200], "param": ""}


def _oauth_codes(data):
    """登入服務的錯誤碼（可能在 error、error.code 或 error_description 裡）。"""
    codes = set()
    err = data.get("error")
    if isinstance(err, dict):
        for k in ("code", "type"):
            if err.get(k):
                codes.add(str(err[k]).lower())
    elif err:
        codes.add(str(err).lower())
    for word in re.findall(r"[a-z_]{6,40}", str(data.get("error_description") or "").lower()):
        if word in _REFRESH_DEAD or word.startswith("refresh_token_"):
            codes.add(word)
    return codes


def normalize_cloud_url(url):
    """雲端網址只收 https://網域；測試時也收 http://127.0.0.1:埠。不能有路徑、帳密或查詢字串。"""
    u = str(url or "").strip().rstrip("/")
    try:
        p = urlparse(u)
        port = p.port
    except ValueError:
        port, p = None, None
    if p and not p.username and not p.password and not p.query and not p.fragment and p.path in ("", "/"):
        if p.scheme == "https" and p.hostname and "." in p.hostname:
            return "https://" + p.netloc.lower()
        if p.scheme == "http" and p.hostname == "127.0.0.1" and port:
            return f"http://127.0.0.1:{port}"
    raise ValueError("雲端網址要像 https://你的專案.vercel.app")


def _top(param):
    return re.split(r"[.\[]", str(param or ""), maxsplit=1)[0]


def _sentence_cut(piece, start):
    """從 start 之後找第一個句尾，回傳切點（含句尾符號）；找不到回 None。"""
    for i in range(max(0, start), len(piece)):
        if piece[i] in _SENTENCE_END:
            return i + 1
    return None


def _completed_text(data):
    # 保險：串流沒給 delta 時，從 response.completed 的 output 補文字
    out = []
    for item in ((data.get("response") or {}).get("output") or []):
        for part in (item.get("content") or []) if isinstance(item, dict) else []:
            if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                out.append(part["text"])
    return "".join(out)


# ---------------------------------------------------------------- 設定


class ChatGPTConfig:
    """從環境變數讀進來的設定。mode：local（本機登入）、hosted（OpenAI 核准的網站）、synced（雲端用本機同步來的憑證）、off。"""

    def __init__(self):
        self.on_cloud = False
        self.mode = "off"
        self.blocked_reason = ""
        self.redirect_path = "/auth/callback"
        self.redirect_uri = ""
        self.scopes = DEFAULT_SCOPES
        self.resource = "https://api.openai.com/v1"
        self.api_base = "https://api.openai.com/v1"
        self.urls = {k: "" for k in _ENV_URLS}  # 只放有特別指定的網址
        self.client_id = ""
        self.client_secret = ""
        self.token_auth = "none"
        self.agent_name = "AI 理專對練教練"
        self.host_id = ""
        self.model = ""
        self.coach_model = ""
        self.json_mode = "schema"
        self.reasoning_effort = ""
        self.coach_reasoning_effort = "low"
        self.verify_id_token = True
        self.cooldown_sec = 900.0
        self.timeout = 60.0
        self.chat_max_chars = 300
        self.sync_key = ""
        self.cloud_url = ""

    @classmethod
    def from_env(cls, env, on_cloud):
        def g(key, default=""):
            return (str(env.get(key) or "")).strip() or default

        c = cls()
        c.on_cloud = bool(on_cloud)
        c.client_id = g("CHATGPT_CLIENT_ID")
        c.client_secret = g("CHATGPT_CLIENT_SECRET")
        c.sync_key = g("CHATGPT_SYNC_KEY")
        c.cloud_url = g("CLOUD_URL")
        want = g("CHATGPT_MODE", "auto").lower()
        if want == "auto":
            if not on_cloud:
                c.mode = "local"
            else:
                c.mode = "synced" if c.sync_key else "hosted" if c.client_id else "off"
        elif want in ("local", "hosted", "synced", "off"):
            c.mode = want
        else:
            c.mode = "off"
        path = g("CHATGPT_REDIRECT_PATH", "/auth/callback")
        c.redirect_path = path if path.startswith("/") else "/" + path
        c.redirect_uri = g("CHATGPT_REDIRECT_URI")
        c.scopes = " ".join(g("CHATGPT_SCOPES", DEFAULT_SCOPES).split())
        c.resource = g("CHATGPT_RESOURCE", "https://api.openai.com/v1")
        c.api_base = g("CHATGPT_API_BASE", "https://api.openai.com/v1").rstrip("/")
        c.urls = {k: g(name) for k, name in _ENV_URLS.items()}
        auth = g("CHATGPT_TOKEN_AUTH").lower()
        # 文件的機密用戶端範例是 client_secret_basic（祕密只放在 Authorization 標頭）
        c.token_auth = auth if auth in ("none", "client_secret_post", "client_secret_basic") else (
            "client_secret_basic" if c.client_secret else "none")
        c.agent_name = g("CHATGPT_AGENT_NAME", "AI 理專對練教練")
        c.host_id = g("CHATGPT_HOST_ID")
        c.model = g("CHATGPT_MODEL")
        c.coach_model = g("CHATGPT_COACH_MODEL")
        c.json_mode = "none" if g("CHATGPT_JSON_MODE", "schema").lower() in ("none", "off", "0") else "schema"
        c.reasoning_effort = _effort(g("CHATGPT_REASONING_EFFORT"))
        c.coach_reasoning_effort = _effort(g("CHATGPT_COACH_REASONING_EFFORT", "low"))
        c.verify_id_token = g("CHATGPT_VERIFY_ID_TOKEN", "1").lower() not in ("0", "false", "no", "off")
        c.cooldown_sec = _num(g("CHATGPT_COOLDOWN_SEC"), 900.0)
        c.timeout = _num(g("CHATGPT_TIMEOUT"), 60.0)
        c.chat_max_chars = int(_num(g("CHATGPT_CHAT_MAX_CHARS"), 300))
        c.blocked_reason = c._blocked(want)
        return c

    def _blocked(self, want):
        if want not in ("auto", "local", "hosted", "synced", "off"):
            return "CHATGPT_MODE 只能是 auto、local、hosted、synced 或 off"
        if self.mode == "off":
            if self.on_cloud:
                return "雲端版還沒開 ChatGPT：在 Vercel 加 CHATGPT_SYNC_KEY 後 Redeploy"
            return "ChatGPT 已關閉（CHATGPT_MODE=off）"
        if self.mode == "local" and self.on_cloud:
            return "雲端不能用本機登入，請改用同步（CHATGPT_SYNC_KEY）"
        if self.mode == "synced":
            if not self.on_cloud:
                return "同步模式只用在雲端版"
            if not self.sync_key:
                return "缺少 CHATGPT_SYNC_KEY"
        if self.mode == "hosted":
            if not self.client_id:
                return "缺少 CHATGPT_CLIENT_ID"
            if not self.redirect_uri:
                return "缺少 CHATGPT_REDIRECT_URI"
        return ""

    @property
    def available(self):
        """這裡能不能用 ChatGPT 當大腦（還要看有沒有登入）。"""
        return self.mode != "off" and not self.blocked_reason

    @property
    def can_sign_in(self):
        """這裡能不能直接按「Continue with ChatGPT」；同步模式要到本機版登入。"""
        return self.available and self.mode in ("local", "hosted")

    @property
    def sync_enabled(self):
        """雲端的 /api/chatgpt/sync/* 要不要開。"""
        return self.on_cloud and self.mode == "synced" and bool(self.sync_key) and not self.blocked_reason


def _effort(value):
    v = (value or "").strip().lower()
    return "" if v in ("", "none", "off", "0") else v


# ---------------------------------------------------------------- 主體


class ChatGPT:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self._lock = threading.Lock()
        self._disc = {"at": 0.0, "ok": False, "data": None}
        self._jwks = {"at": 0.0, "tried": 0.0, "keys": {}}
        self._tokens = {}  # 帳號 → (access_token, expires_at)：快到期前不用每次讀資料庫
        self._rejected = {}  # 模型 → 被拒絕的欄位（text、reasoning），之後不送
        self._cool = {}  # 資料庫不能用時的備用冷卻時間

    # ---- 帳號代號

    def _key(self, uid):
        """同步模式整個網站共用一個帳號（只給本人用）；其他模式每位使用者一份。"""
        if self.cfg.mode == "synced":
            return "owner"
        uid = str(uid or "local")
        return uid if _UID_RE.match(uid) else hashlib.sha256(uid.encode("utf-8")).hexdigest()[:32]

    def _acct(self, uid):
        key = self._key(uid)
        return "chatgpt-owner" if self.cfg.mode == "synced" else f"chatgpt-account-{key}"

    def _get_secret(self, name):
        try:
            return self.store.get_secret(name)
        except StoreError as e:
            raise BrainError(f"讀不到 ChatGPT 登入資料：{e}", code="store") from None

    def _put_secret(self, name, value):
        try:
            self.store.put_secret(name, value)
        except StoreError as e:
            raise BrainError(f"存不了 ChatGPT 登入資料：{e}", code="store") from None

    def host_id(self):
        """這個主機的 ext_agent_host_id：第一次產生後永遠不變（雲端和本機各有自己的）。"""
        if self.cfg.host_id:
            return self.cfg.host_id
        rec = self._get_secret("chatgpt-host")
        if rec and _HOST_RE.match(str(rec.get("host_id") or "")):
            return rec["host_id"]
        try:
            with self.store.lock("cg-host", ttl=10, wait=10):
                rec = self._get_secret("chatgpt-host")
                if rec and _HOST_RE.match(str(rec.get("host_id") or "")):
                    return rec["host_id"]
                hid = "urn:uuid:" + str(uuid.uuid4())
                self._put_secret("chatgpt-host", {"host_id": hid, "created_at": int(time.time())})
                return hid
        except StoreError as e:
            raise BrainError(f"存不了主機代號：{e}", code="store") from None

    # ---- 網址與金鑰

    def endpoints(self):
        """登入相關網址：先用 discovery（快取 1 小時），再套用環境變數指定的網址；都失敗就用預設值。"""
        explicit = self.cfg.urls
        out = dict(DEFAULT_URLS)
        need = any(not explicit.get(k) for k in _DISCOVERY_FIELDS)
        disc_url = explicit.get("discovery_url") or DEFAULT_URLS["discovery_url"]
        if need and disc_url.lower() not in ("off", "none"):
            now = time.time()
            with self._lock:
                d = dict(self._disc)
            if d["data"] is None or now - d["at"] > (3600 if d["ok"] else 300):
                data, ok = {}, False
                try:
                    req = urllib.request.Request(disc_url, headers={"Accept": "application/json"})
                    with _opener_for(disc_url).open(req, timeout=5) as r:
                        data = _json(r.read(200000))
                    ok = bool(data)
                except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
                    _log("讀不到 discovery，先用預設網址")
                with self._lock:
                    self._disc = {"at": now, "ok": ok, "data": data}
                d = self._disc
            for field, name in _DISCOVERY_FIELDS.items():
                v = (d["data"] or {}).get(name)
                if isinstance(v, str) and v.startswith(("https://", "http://")):
                    out[field] = v
        for field, v in explicit.items():
            if v:
                out[field] = v
        return out

    def _jwk(self, kid, jwks_url):
        now = time.time()
        with self._lock:
            keys, at, tried = dict(self._jwks["keys"]), self._jwks["at"], self._jwks["tried"]
        stale = now - at > 3600
        if (kid not in keys and now - tried > 30) or stale or not keys:
            # 不認得的 kid 才重抓（最多 30 秒一次），避免被亂塞的 kid 一直打
            fresh = {}
            try:
                req = urllib.request.Request(jwks_url, headers={"Accept": "application/json"})
                with _opener_for(jwks_url).open(req, timeout=10) as r:
                    data = _json(r.read(500000))
                for k in data.get("keys") or []:
                    if not isinstance(k, dict) or k.get("use", "sig") != "sig" or k.get("alg", "RS256") != "RS256":
                        continue
                    nums = jwk_numbers(k)
                    if nums:
                        fresh[str(k.get("kid") or "")] = nums
            except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
                _log("讀不到 JWKS")
            with self._lock:
                self._jwks["tried"] = now
                if fresh:
                    self._jwks.update(at=now, keys=fresh)
                    keys = fresh
        if kid in keys:
            return keys[kid]
        if not kid and len(keys) == 1:
            return next(iter(keys.values()))
        return None

    def check_id_token(self, token, client_id, nonce=None, check_exp=True):
        """RS256 簽章（JWKS 依 kid 找金鑰）、iss、aud、exp（容許 5 秒）、nonce。成功回傳 claims。"""
        bad = BrainError("ChatGPT 登入憑證驗證失敗", code="bad_id_token", fallback=False)
        try:
            header, claims, signing_input, sig = decode_jwt(token)
        except (ValueError, TypeError, UnicodeError):
            raise bad from None
        ep = self.endpoints()
        if self.cfg.verify_id_token:
            if header.get("alg") != "RS256":
                raise bad
            key = self._jwk(str(header.get("kid") or ""), ep["jwks_url"])
            if not key or not rs256_verify(key[0], key[1], signing_input, sig):
                raise bad
        if claims.get("iss") != ep["issuer"]:
            raise bad
        aud = claims.get("aud")
        if not (aud == client_id or (isinstance(aud, list) and client_id in aud)):
            raise bad
        exp = claims.get("exp")
        if check_exp and (not isinstance(exp, (int, float)) or isinstance(exp, bool) or exp + 5 < time.time()):
            raise bad
        if nonce is not None and not hmac.compare_digest(str(claims.get("nonce") or ""), str(nonce)):
            raise bad
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise bad
        return claims

    verify_id_token = check_id_token

    def _client_auth(self, fields):
        """OpenAI 核准的網站（hosted）才有 client secret；本機和同步都是公開用戶端，不送祕密。"""
        headers = {}
        if self.cfg.mode == "hosted" and self.cfg.client_secret:
            if self.cfg.token_auth == "client_secret_post":
                fields["client_secret"] = self.cfg.client_secret
            elif self.cfg.token_auth == "client_secret_basic":
                pair = quote_plus(fields["client_id"], safe="*") + ":" + quote_plus(self.cfg.client_secret, safe="*")
                headers["Authorization"] = "Basic " + base64.b64encode(pair.encode("utf-8")).decode("ascii")
        return headers

    def _form_post(self, url, fields, headers=None, timeout=15):
        data = urlencode(fields).encode("ascii")
        h = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        h.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=h, method="POST")
        try:
            with _opener_for(url).open(req, timeout=timeout) as r:
                return r.status, _json(r.read(200000))
        except urllib.error.HTTPError as e:
            try:
                body = e.read(20000)
            except OSError:
                body = b""
            return e.code, _json(body)
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            raise BrainError("連不到 ChatGPT 登入服務", code="network") from None

    # ---- 狀態（不連網路）

    def redirect_uri(self, local_port):
        if self.cfg.mode == "local":
            if not local_port:
                raise BrainError("找不到這台電腦的連接埠", code="no_port", fallback=False)
            return f"http://127.0.0.1:{int(local_port)}{self.cfg.redirect_path}"
        if self.cfg.mode == "hosted":
            return self.cfg.redirect_uri
        return ""

    def _needs_login_msg(self):
        if self.cfg.mode == "synced":
            return "ChatGPT 登入過期：請在本機版按『登入並同步到雲端』"
        return "ChatGPT 登入過期，請重新登入"

    def account_status(self, uid):
        """給 /api/status 用：只讀資料庫，不連 OpenAI，不會丟例外。不含任何 token。"""
        cfg = self.cfg
        out = {
            "mode": cfg.mode, "available": cfg.available, "can_sign_in": cfg.can_sign_in,
            "blocked_reason": cfg.blocked_reason, "signed_in": False, "plan_usage": False, "needs_login": False,
            "configured": False, "email": "", "expires_at": 0, "cooldown_until": 0, "message": "",
            "manage_usage_url": MANAGE_USAGE_URL,
        }
        if not cfg.available:
            out["message"] = cfg.blocked_reason
            return out
        try:
            rec = self.store.get_secret(self._acct(uid)) or {}
        except (StoreError, ValueError):
            out["message"] = "讀不到 ChatGPT 登入資料"
            return out
        now = time.time()
        has_access = bool(rec.get("access_token"))
        has_refresh = bool(rec.get("refresh_token"))
        expires_at = _epoch(rec.get("expires_at"))
        needs_login = bool(rec.get("needs_login")) or (has_access and not has_refresh and expires_at <= now)
        signed_in = (has_access or has_refresh) and not needs_login
        plan = signed_in and rec.get("plan_usage") is True
        until = self.cooldown_until(uid)
        out.update(
            signed_in=signed_in, plan_usage=plan, needs_login=needs_login, configured=plan,
            email=str(rec.get("email") or ""), expires_at=expires_at, cooldown_until=until,
        )
        if needs_login:
            out["message"] = self._needs_login_msg()
        elif not signed_in:
            out["message"] = "還沒同步：請在本機版按『登入並同步到雲端』" if cfg.mode == "synced" else "還沒登入 ChatGPT"
        elif not plan:
            out["message"] = "這個帳號沒有開放用 ChatGPT 方案"
        elif until > now:
            out["message"] = "用量已達上限，暫用 Gemini"
        else:
            out["message"] = "使用 ChatGPT 方案"
        return out

    # ---- 冷卻（用量到上限時先不用 ChatGPT）

    def cooldown(self, uid, seconds):
        key = self._key(uid)
        until = int(time.time() + float(seconds))
        self._cool[key] = until
        try:
            self.store.eph_set("cg-cool:" + key, until, max(1.0, float(seconds)))
        except StoreError:
            pass

    def cooldown_until(self, uid):
        key = self._key(uid)
        until = 0
        try:
            v = self.store.eph_get("cg-cool:" + key)
            until = int(v) if isinstance(v, (int, float)) else 0
        except (StoreError, ValueError):
            until = self._cool.get(key, 0)
        return until if until > time.time() else 0

    def _clear_cooldown(self, key):
        self._cool.pop(key, None)
        try:
            self.store.eph_pop("cg-cool:" + key)
        except StoreError:
            pass

    def _forget(self, acct, key):
        self._tokens.pop(acct, None)
        self._clear_cooldown(key)
        try:
            self.store.eph_pop("cg-models:" + key)
        except StoreError:
            pass

    # ---- 登入

    def begin_login(self, uid, redirect_uri, return_to, host_id=None, target="local"):
        """回傳 OpenAI 授權網址。target=cloud：在本機幫雲端登入，ext_agent_host_id 用雲端的主機代號。"""
        cfg = self.cfg
        if target not in ("local", "cloud"):
            raise BrainError("登入目標不對", code="bad_target", fallback=False)
        if not cfg.can_sign_in:
            raise BrainError(cfg.blocked_reason or "這裡不能登入 ChatGPT", code="blocked", fallback=False)
        if target == "cloud" and cfg.mode != "local":
            raise BrainError("只有本機版可以幫雲端登入", code="blocked", fallback=False)
        if not self._redirect_ok(redirect_uri):
            raise BrainError("登入回來的網址不對", code="bad_redirect", fallback=False)
        if host_id is not None and not _HOST_RE.match(str(host_id)):
            raise BrainError("主機代號格式不對", code="bad_host_id", fallback=False)
        if target == "cloud" and not host_id:
            raise BrainError("缺少雲端的主機代號", code="no_host_id", fallback=False)
        own = self.host_id()
        hid = host_id or own
        if target == "cloud" and hid == own:
            raise BrainError("雲端的主機代號不能和這台電腦一樣", code="bad_host_id", fallback=False)
        reg_name = "chatgpt-cloud-client" if target == "cloud" else self._acct(uid)
        reg = self._get_secret(reg_name) or {}
        if cfg.mode == "hosted":
            client_id, first = cfg.client_id, False
        else:
            saved = str(reg.get("client_id") or "")
            first = not (_CLIENT_RE.match(saved) and saved != DYNAMIC_CLIENT)
            client_id = DYNAMIC_CLIENT if first else saved
        state, nonce = _rand(), _rand()
        verifier, challenge = pkce_pair()
        params = {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": cfg.scopes,
            "resource": cfg.resource,
            "state": state,
            "nonce": nonce,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "ext_agent_host_id": hid,
        }
        if first:
            params["agent_name_hint"] = cfg.agent_name  # 只在第一次註冊時送
        elif reg.get("email"):
            params["login_hint"] = str(reg["email"])  # 不送 id_token_hint：token 不放進網址
        if not first and (reg.get("plan_usage") is False or reg.get("reconsent")):
            params["prompt"] = "consent"  # 之前沒同意用方案：這次要再問一次
        pending = {
            "uid": str(uid), "state": state, "nonce": nonce, "verifier": verifier, "redirect_uri": redirect_uri,
            "return_to": str(return_to or ""), "client_id": client_id, "first": first, "host_id": hid,
            "target": target, "reg": reg_name, "at": int(time.time()),
        }
        try:
            self.store.eph_set("cg-login:" + state, pending, 600)
        except StoreError as e:
            raise BrainError(f"存不了登入狀態：{e}", code="store", fallback=False) from None
        return self.endpoints()["authorize_url"] + "?" + urlencode(params, quote_via=quote)

    def _redirect_ok(self, uri):
        try:
            p = urlparse(str(uri or ""))
            port = p.port
        except ValueError:
            return False
        if p.query or p.fragment or p.username:
            return False
        if self.cfg.mode == "local":
            # 文件規定：http、127.0.0.1（不能用 localhost）、固定路徑，只有埠號可以變
            return p.scheme == "http" and p.hostname == "127.0.0.1" and bool(port) and p.path == self.cfg.redirect_path
        return bool(self.cfg.redirect_uri) and uri == self.cfg.redirect_uri

    def finish_login(self, uid, query):
        """處理 OpenAI 導回來的網址參數。回傳 {ok, reason, return_to, plan_usage, target, email}；使用者造成的錯誤不丟例外。"""
        q = {}
        for k, v in (query or {}).items():
            if isinstance(v, (list, tuple)):
                v = v[0] if v else ""
            if v is not None:
                q[str(k)] = str(v)
        res = {"ok": False, "reason": "", "return_to": "", "plan_usage": False, "target": "local", "email": ""}
        state = q.get("state", "")
        if not _STATE_RE.match(state):
            res["reason"] = "bad_state"
            return res
        try:
            pending = self.store.eph_pop("cg-login:" + state)  # 只能用一次
        except StoreError:
            res["reason"] = "store"
            return res
        if not isinstance(pending, dict):
            res["reason"] = "expired"
            return res
        res["return_to"] = str(pending.get("return_to") or "")
        res["target"] = pending.get("target") if pending.get("target") in ("local", "cloud") else "local"
        if not (hmac.compare_digest(str(pending.get("state") or ""), state)
                and hmac.compare_digest(str(pending.get("uid") or ""), str(uid))):
            res["reason"] = "bad_state"
            return res
        err = q.get("error", "")
        if err:
            res["reason"] = "denied" if err == "access_denied" else (err.lower() if _CODE_RE.match(err.lower()) else "error")
            return res
        code = q.get("code", "")
        if not code:
            res["reason"] = "no_code"
            return res
        got = q.get("client_id", "")
        if pending.get("first"):
            if not got or got == DYNAMIC_CLIENT:
                res["reason"] = "no_client_id"  # 第一次註冊一定要拿到新的 client_id
                return res
            client_id = got
        else:
            client_id = str(pending.get("client_id") or "")
            if got and got != client_id:
                res["reason"] = "client_mismatch"
                return res
        if not _CLIENT_RE.match(client_id):
            res["reason"] = "no_client_id"
            return res
        try:
            tok = self._exchange(code, pending, client_id)
            claims = self.check_id_token(tok["id_token"], client_id, nonce=pending.get("nonce") or "")
            reg_name = str(pending.get("reg") or self._acct(uid))
            reg = self._get_secret(reg_name) or {}
            if reg.get("sub") and reg.get("client_id") == client_id and reg["sub"] != claims["sub"]:
                res["reason"] = "account_mismatch"
                return res
            scope = " ".join(str(tok.get("scope") or q.get("scope") or "").split())
            plan = PLAN_SCOPE in scope.split()
            rec = self._record(tok, claims, client_id, pending.get("host_id") or "", scope, plan)
            for k in ("last_sync_at", "last_sync_email", "last_error"):
                if k in reg:
                    rec[k] = reg[k]
            self._put_secret(reg_name, rec)
        except BrainError as e:
            _log(f"登入沒有完成：{e.code or 'error'}")
            res["reason"] = e.code or "error"
            return res
        if reg_name != "chatgpt-cloud-client":
            self._forget(reg_name, self._key(uid))
        res.update(ok=True, reason="" if plan else "no_plan", plan_usage=plan, email=rec["email"])
        return res

    def _exchange(self, code, pending, client_id):
        fields = {
            "grant_type": "authorization_code", "client_id": client_id, "code": code,
            "code_verifier": str(pending.get("verifier") or ""), "redirect_uri": str(pending.get("redirect_uri") or ""),
            "resource": self.cfg.resource,
        }
        headers = self._client_auth(fields)
        status, data = self._form_post(self.endpoints()["token_url"], fields, headers)
        if status != 200 or not isinstance(data.get("access_token"), str) or not data["access_token"]:
            _log(f"換 token 失敗 HTTP {status} {','.join(sorted(_oauth_codes(data))) or '-'}")
            raise BrainError("ChatGPT 登入沒有完成", code="exchange_failed", status=status, fallback=False)
        if not isinstance(data.get("id_token"), str) or not data["id_token"]:
            raise BrainError("ChatGPT 沒有回傳登入憑證", code="no_id_token", fallback=False)
        return data

    @staticmethod
    def _record(tok, claims, client_id, host_id, scope, plan):
        now = int(time.time())
        return {
            "client_id": client_id,
            "access_token": tok["access_token"],
            "refresh_token": str(tok.get("refresh_token") or ""),
            "id_token": str(tok.get("id_token") or ""),
            "token_type": str(tok.get("token_type") or "Bearer"),
            "expires_at": now + int(_num(tok.get("expires_in"), 3600)),
            "earliest_refresh_at": _epoch(tok.get("earliest_refresh_at")),
            "scope": scope,
            "plan_usage": plan,
            "needs_login": False,
            "email": str(claims.get("email") or ""),
            "sub": str(claims.get("sub") or ""),
            "issuer": str(claims.get("iss") or ""),
            "ext_agent_host_id": host_id,
            "saved_at": now,
        }

    def logout(self, uid):
        """撤銷續期權（盡量）並清掉 token；留下 client_id 和 email，下次登入沿用。同步模式整份刪掉。回傳遠端有沒有確認撤銷。"""
        acct, key = self._acct(uid), self._key(uid)
        rec = self._get_secret(acct)
        revoked = self._revoke(rec) if rec and rec.get("refresh_token") and rec.get("client_id") else False
        try:
            if self.cfg.mode == "synced":
                self.store.delete_secret(acct)
            elif rec:
                keep = {k: rec[k] for k in ("client_id", "email", "sub", "issuer", "ext_agent_host_id", "plan_usage") if k in rec}
                keep["signed_out_at"] = int(time.time())
                self.store.put_secret(acct, keep)
        except StoreError as e:
            raise BrainError(f"清不掉 ChatGPT 登入資料：{e}", code="store", fallback=False) from None
        self._forget(acct, key)
        return revoked

    def clear_owner(self):
        """雲端 /api/chatgpt/sync/clear：刪掉同步來的帳號（主機代號留著）。"""
        rec = self._get_secret("chatgpt-owner")
        revoked = self._revoke(rec) if rec and rec.get("refresh_token") and rec.get("client_id") else False
        try:
            self.store.delete_secret("chatgpt-owner")
        except StoreError as e:
            raise BrainError(f"清不掉 ChatGPT 登入資料：{e}", code="store", fallback=False) from None
        self._forget("chatgpt-owner", "owner")
        return revoked

    def _revoke(self, rec):
        fields = {"token": rec["refresh_token"], "token_type_hint": "refresh_token", "client_id": rec["client_id"]}
        headers = self._client_auth(fields)
        for attempt in (0, 1):
            try:
                status, _ = self._form_post(self.endpoints()["revoke_url"], fields, headers, timeout=10)
            except BrainError:
                status = 0
            if status == 200:
                return True
            if status and status < 500:
                _log(f"撤銷沒有成功 HTTP {status}")
                return False
            if attempt == 0:
                time.sleep(0.5)
        return False

    # ---- 本機 ↔ 雲端同步（OpenAI 文件的 self-hosted VM 做法）

    def export_account(self, uid=None, target=None):
        """要傳給雲端的憑證。target="cloud"（或什麼都不給）：begin_login(target="cloud") 存的那份；
        給 uid：那個帳號（例如伺服器用另一個 uid 替雲端登入時）。"""
        name = "chatgpt-cloud-client" if target == "cloud" or uid is None else self._acct(uid)
        rec = self._get_secret(name)
        if not rec or not rec.get("refresh_token") or not rec.get("access_token"):
            raise BrainError("還沒有要同步的登入，請按『登入並同步到雲端』", code="not_signed_in", fallback=False)
        if rec.get("plan_usage") is not True:
            raise BrainError("這個帳號沒有開放用 ChatGPT 方案", code="no_plan_scope", fallback=False)
        return {k: rec.get(k) for k in _EXPORT_FIELDS}

    def forget_tokens(self, uid):
        """只清掉這個帳號的 token（不撤銷）：替雲端登入、傳過去之後，這台電腦不留那份。client_id 和 email 留著。"""
        acct = self._acct(uid)
        rec = self._get_secret(acct)
        if rec:
            keep = {k: rec[k] for k in ("client_id", "email", "sub", "issuer", "ext_agent_host_id", "plan_usage") if k in rec}
            self._put_secret(acct, keep)
        self._forget(acct, self._key(uid))

    def cloud_sync_done(self, ok, error="", email=""):
        """同步結束（成功或失敗）：本機不留雲端那份的 token，只留 client_id、email 和結果，之後由雲端自己續期。"""
        rec = self._get_secret("chatgpt-cloud-client") or {}
        keep = {k: rec[k] for k in ("client_id", "email", "sub", "issuer", "ext_agent_host_id", "plan_usage") if k in rec}
        keep["last_sync_at"] = int(time.time()) if ok else int(_num(rec.get("last_sync_at"), 0))
        keep["last_sync_email"] = (email or str(rec.get("email") or "")) if ok else str(rec.get("last_sync_email") or "")
        keep["last_error"] = "" if ok else str(error or "同步失敗")[:200]
        self._put_secret("chatgpt-cloud-client", keep)

    def cloud_sync_error(self, error=""):
        """只改上次同步的錯誤訊息（重新設定雲端網址成功時清掉舊的錯誤）；一樣不留 token。"""
        rec = self._get_secret("chatgpt-cloud-client") or {}
        keep = {k: rec[k] for k in ("client_id", "email", "sub", "issuer", "ext_agent_host_id", "plan_usage",
                                    "last_sync_at", "last_sync_email") if k in rec}
        keep["last_error"] = str(error or "")[:200]
        self._put_secret("chatgpt-cloud-client", keep)

    def cloud_sync_status(self):
        """本機狀態頁用：{last_sync_at, last_sync_email, last_error}，不含 token。"""
        try:
            rec = self.store.get_secret("chatgpt-cloud-client") or {}
        except (StoreError, ValueError):
            rec = {}
        return {
            "last_sync_at": int(_num(rec.get("last_sync_at"), 0)),
            "last_sync_email": str(rec.get("last_sync_email") or ""),
            "last_error": str(rec.get("last_error") or ""),
        }

    def _cloud_post(self, cloud_url, path, body, sync_key, timeout=20):
        try:
            url = normalize_cloud_url(cloud_url) + path
        except ValueError as e:
            raise BrainError(str(e), code="bad_cloud_url", fallback=False) from None
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json",
            "X-Sync-Key": str(sync_key or ""), "User-Agent": APP_ID + "-sync/1",
        })
        try:
            with _opener_for(url, redirects=False).open(req, timeout=timeout) as r:
                return r.status, _json(r.read(200000))
        except urllib.error.HTTPError as e:
            try:
                raw = e.read(20000)
            except OSError:
                raw = b""
            return e.code, _json(raw)
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            raise BrainError("連不到雲端網址，請確認網址", code="cloud_unreachable", fallback=False) from None

    @staticmethod
    def _cloud_reason(status, data):
        """雲端回應 → 原因代碼（網頁照代碼顯示說明）。"""
        if 300 <= status < 400:
            return "redirect"
        if status == 404:
            return "sync_off"
        if status in (401, 403):
            return "sync_key" if data.get("sync") else "cloud_blocked"
        if status == 429:
            return "rate_limited"
        reason = str(data.get("reason") or "")
        return reason if status == 400 and _CODE_RE.match(reason) else "cloud"

    @staticmethod
    def _cloud_message(status, data):
        if 300 <= status < 400:
            return "雲端網址被轉址了，請貼最後的網址（https://…vercel.app）"
        if status == 404:
            return "雲端還沒設定 CHATGPT_SYNC_KEY，或網址不對（改完要 Redeploy）"
        if status in (401, 403):
            if data.get("sync"):
                return "同步密碼不對：要和 Vercel 上的 CHATGPT_SYNC_KEY 一樣"
            # 不是對練程式回的（沒有 sync 欄位）：多半是 Vercel 的 Deployment Protection 擋住了
            return "雲端擋住了這個請求：請用正式網址（https://專案名.vercel.app），或關掉 Deployment Protection"
        if status == 429:
            return "試太多次了，請 10 分鐘後再試"
        if status == 400 and data.get("error"):
            return str(data["error"])[:200]
        return f"雲端出錯（HTTP {status}）"

    def cloud_host_id(self, cloud_url, sync_key):
        """問雲端它自己的主機代號（同時確認網址和同步密碼都對）。"""
        status, data = self._cloud_post(cloud_url, "/api/chatgpt/sync/host", {}, sync_key)
        if status != 200:
            raise BrainError(self._cloud_message(status, data), code=self._cloud_reason(status, data), status=status,
                             fallback=False)
        hid = str(data.get("host_id") or "")
        if data.get("app") != APP_ID or not _HOST_RE.match(hid):
            raise BrainError("這個網址不是對練教練的雲端版", code="cloud_not_app", fallback=False)
        return hid

    def push_to_cloud(self, cloud_url, sync_key):
        """把剛幫雲端登入的憑證用 HTTPS 傳過去；不論成功失敗，本機都不留那份 token。"""
        try:
            payload = self.export_account(target="cloud")
            status, data = self._cloud_post(cloud_url, "/api/chatgpt/sync/import", payload, sync_key)
            if status != 200 or not data.get("ok"):
                raise BrainError(self._cloud_message(status, data), code=self._cloud_reason(status, data), status=status,
                                 fallback=False)
        except BrainError as e:
            # 雲端明確拒收（或這份沒有方案權限）：順便撤銷，免得留下沒人用的授權；沒回應就不撤銷（雲端可能已經收到）
            if e.code == "no_plan_scope" or 400 <= e.status < 500:
                try:
                    rec = self._get_secret("chatgpt-cloud-client")
                    if rec and rec.get("refresh_token") and rec.get("client_id"):
                        self._revoke(rec)
                except BrainError:
                    pass
            try:
                self.cloud_sync_done(False, error=str(e))
            except BrainError:
                pass
            raise
        email = str(data.get("email") or payload.get("email") or "")
        self.cloud_sync_done(True, email=email)
        return {"ok": True, "email": email, "expires_at": int(_num(data.get("expires_at"), 0))}

    def import_account(self, payload):
        """雲端收到本機傳來的憑證：檢查後加密存成 chatgpt-owner。回傳 {ok, email, expires_at} 或 {ok:false, reason, error}。"""
        def fail(reason, msg):
            _log(f"同步憑證被拒：{reason}")
            return {"ok": False, "reason": reason, "error": msg}

        if not self.cfg.sync_enabled:
            return fail("blocked", "雲端還沒設定 CHATGPT_SYNC_KEY")
        if not isinstance(payload, dict):
            return fail("bad_request", "資料格式錯誤")
        vals = {}
        for k in ("client_id", "access_token", "refresh_token", "id_token"):
            v = payload.get(k)
            if not isinstance(v, str) or not v or len(v) > 20000:
                return fail("missing_fields", f"缺少 {k}")
            vals[k] = v
        client_id = vals["client_id"]
        if not _CLIENT_RE.match(client_id) or client_id == DYNAMIC_CLIENT:
            return fail("bad_client", "client_id 格式不對")
        if not client_id.startswith("oaiapp_"):
            _log("client_id 不是 oaiapp_ 開頭（先接受）")
        scope = " ".join(str(payload.get("scope") or "").split())
        if PLAN_SCOPE not in scope.split():
            return fail("no_plan_scope", "這份登入沒有開放用 ChatGPT 方案")
        try:
            own = self.host_id()
        except BrainError as e:
            return fail("store", str(e))
        host = str(payload.get("ext_agent_host_id") or "")
        if host and host != own:
            # 主機代號不一樣：這份不是為這個雲端登入的（例如本機自己那份），收了會和本機互相搶續期
            return fail("host_mismatch", "這份登入不是給這個雲端網站的，請在本機版重新按『登入並同步到雲端』")
        try:
            claims = self.check_id_token(vals["id_token"], client_id, nonce=None, check_exp=False)
        except BrainError:
            return fail("bad_id_token", "登入憑證驗證失敗")
        if payload.get("sub") and str(payload["sub"]) != claims["sub"]:
            return fail("bad_id_token", "登入憑證和帳號對不起來")
        rec = dict(vals, expires_at=payload.get("expires_at"), earliest_refresh_at=payload.get("earliest_refresh_at"),
                   scope=scope, email=str(claims.get("email") or payload.get("email") or ""), sub=claims["sub"],
                   issuer=str(claims.get("iss") or ""))
        try:
            out = self.import_owner(rec)
        except BrainError as e:
            return fail(e.code or "store", str(e))
        return {"ok": True, "email": out["email"], "expires_at": out["expires_at"]}

    def import_owner(self, rec):
        """把檢查過的憑證存成雲端唯一的擁有者帳號（Redis 裡加密）。主機代號一律用雲端自己的。"""
        if not isinstance(rec, dict) or not rec.get("client_id") or not rec.get("refresh_token") or not rec.get("access_token"):
            raise BrainError("憑證不完整", code="missing_fields", fallback=False)
        own = self.host_id()
        host = str(rec.get("ext_agent_host_id") or "")
        if host and host != own:
            raise BrainError("這份登入不是給這個雲端網站的", code="host_mismatch", fallback=False)
        scope = " ".join(str(rec.get("scope") or "").split())
        data = {
            "client_id": str(rec["client_id"]),
            "access_token": str(rec["access_token"]),
            "refresh_token": str(rec["refresh_token"]),
            "id_token": str(rec.get("id_token") or ""),
            "token_type": "Bearer",
            "expires_at": _epoch(rec.get("expires_at")),
            "earliest_refresh_at": _epoch(rec.get("earliest_refresh_at")),
            "scope": scope,
            "plan_usage": PLAN_SCOPE in scope.split(),
            "needs_login": False,
            "email": str(rec.get("email") or ""),
            "sub": str(rec.get("sub") or ""),
            "issuer": str(rec.get("issuer") or ""),
            "ext_agent_host_id": own,
            "imported_at": int(time.time()),
        }
        try:
            with self.store.lock("cg-refresh:owner", ttl=30, wait=20):  # 不要和正在進行的續期互相蓋掉
                self.store.put_secret("chatgpt-owner", data)
        except StoreError as e:
            raise BrainError(f"存不了登入資料：{e}", code="store", fallback=False) from None
        self._forget("chatgpt-owner", "owner")
        return {"email": data["email"], "expires_at": data["expires_at"]}

    # ---- token 與續期

    def _load(self, acct):
        rec = self._get_secret(acct)
        if not rec or not (rec.get("access_token") or rec.get("refresh_token")):
            msg = "還沒同步 ChatGPT 到雲端" if self.cfg.mode == "synced" else "還沒登入 ChatGPT"
            raise BrainError(msg, code="not_signed_in")
        if rec.get("needs_login"):
            raise BrainError(self._needs_login_msg(), code="needs_login")
        if rec.get("plan_usage") is not True:
            raise BrainError("這個帳號沒有開放用 ChatGPT 方案", code="no_plan_scope")
        return rec

    def _token(self, uid, force=False, stale=None):
        acct = self._acct(uid)
        now = time.time()
        if not force:
            mem = self._tokens.get(acct)
            if mem and mem[1] - 60 > now:
                return mem[0]
        rec = self._load(acct)
        expires_at = _epoch(rec.get("expires_at"))
        if not force and rec.get("access_token"):
            if expires_at - 60 > now:
                self._tokens[acct] = (rec["access_token"], expires_at)
                return rec["access_token"]
            if _epoch(rec.get("earliest_refresh_at")) > now and expires_at > now + 5:
                return rec["access_token"]  # 還不到可以續期的時間，舊的還能用
        return self._refresh(uid, stale if stale is not None else rec.get("access_token"))

    def _mark(self, acct, **fields):
        try:
            rec = self.store.get_secret(acct)
            if rec:
                rec.update(fields)
                if fields.get("needs_login"):
                    rec["access_token"] = ""
                self.store.put_secret(acct, rec)
        except (StoreError, ValueError):
            pass
        self._tokens.pop(acct, None)

    def _refresh(self, uid, stale):
        """同一個帳號同時只續期一次（refresh token 每次都會換新）；拿到鎖後重讀，別人剛續好就直接用。"""
        acct, key = self._acct(uid), self._key(uid)
        try:
            with self.store.lock("cg-refresh:" + key, ttl=30, wait=20):
                rec = self._load(acct)
                now = time.time()
                if rec.get("access_token") and rec["access_token"] != stale and _epoch(rec.get("expires_at")) - 60 > now:
                    self._tokens[acct] = (rec["access_token"], _epoch(rec.get("expires_at")))
                    return rec["access_token"]
                if not rec.get("refresh_token") or not rec.get("client_id"):
                    rec.update(needs_login=True, access_token="")
                    self.store.put_secret(acct, rec)
                    self._tokens.pop(acct, None)
                    raise BrainError(self._needs_login_msg(), code="needs_login")
                fields = {
                    "grant_type": "refresh_token", "client_id": rec["client_id"],
                    "refresh_token": rec["refresh_token"], "resource": self.cfg.resource,
                }
                headers = self._client_auth(fields)
                status, data = self._form_post(self.endpoints()["token_url"], fields, headers)
                if status == 200 and isinstance(data.get("access_token"), str) and data["access_token"]:
                    now = time.time()
                    rec.update(
                        access_token=data["access_token"],
                        refresh_token=str(data.get("refresh_token") or rec["refresh_token"]),
                        expires_at=int(now + _num(data.get("expires_in"), 3600)),
                        earliest_refresh_at=_epoch(data.get("earliest_refresh_at")),
                        refreshed_at=int(now),
                    )
                    if isinstance(data.get("id_token"), str) and data["id_token"]:
                        rec["id_token"] = data["id_token"]
                    if data.get("scope"):
                        rec["scope"] = " ".join(str(data["scope"]).split())
                        rec["plan_usage"] = PLAN_SCOPE in rec["scope"].split()
                    self.store.put_secret(acct, rec)  # 新的 refresh token 一定要馬上寫回去
                    self._tokens[acct] = (rec["access_token"], rec["expires_at"])
                    if rec["plan_usage"] is not True:
                        raise BrainError("這個帳號沒有開放用 ChatGPT 方案", code="no_plan_scope")
                    return rec["access_token"]
                codes = _oauth_codes(data)
                _log(f"續期失敗 HTTP {status} {','.join(sorted(codes)) or '-'}")
                if status in (400, 401) and (codes & _REFRESH_DEAD or any(c.startswith("refresh_token_") for c in codes)):
                    # refresh token 已經不能用：清掉 token、要重新登入（client_id 和 email 留著）
                    rec.update(needs_login=True, access_token="", refresh_token="", needs_login_code=sorted(codes)[0])
                    self.store.put_secret(acct, rec)
                    self._tokens.pop(acct, None)
                    raise BrainError(self._needs_login_msg(), code="needs_login", status=status)
                raise BrainError("ChatGPT 續期暫時失敗", code="refresh_failed", status=status)
        except StoreError as e:
            raise BrainError(f"ChatGPT 登入資料暫時讀寫不了：{e}", code="store") from None

    # ---- 模型清單

    def list_models(self, uid):
        """[{slug, display_name}]，快取 10 分鐘。"""
        key = self._key(uid)
        try:
            hit = self.store.eph_get("cg-models:" + key)
        except StoreError:
            hit = None
        if isinstance(hit, list) and hit:
            return hit
        token = self._token(uid)
        url = self.cfg.api_base + "/models"
        data = {}
        for attempt in (0, 1):
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
            try:
                with _opener_for(url).open(req, timeout=15) as r:
                    data = _json(r.read(500000))
                break
            except urllib.error.HTTPError as e:
                try:
                    err = _api_error(e.read(4000))
                except OSError:
                    err = {"code": "", "message": "", "param": ""}
                if (e.code == 401 or err["code"] == "subscription_sharing_invalid_user") and attempt == 0:
                    token = self._token(uid, force=True, stale=token)
                    continue
                self._on_error(uid, "", e.code, err, True, {}, {"auth", "param:text", "param:reasoning"})
            except (urllib.error.URLError, OSError, http.client.HTTPException):
                raise BrainError("連不到 ChatGPT", code="network") from None
        rows = parse_models(data)
        if rows:
            try:
                self.store.eph_set("cg-models:" + key, rows, 600)
            except StoreError:
                pass
        return rows

    def _auto_model(self, uid):
        rows = self.list_models(uid)
        if not rows:
            raise BrainError("ChatGPT 沒有可用的模型", code="no_model")
        return rows[0]["slug"]

    # ---- 回覆（/v1/responses 串流）

    def _body(self, model, instructions, input_items, json_schema, effort):
        items = []
        for it in input_items or []:
            if isinstance(it, dict):
                it = dict(it)
                if it.get("role") == "system":
                    it["role"] = "developer"  # 不能送 system：文件說會被拒
                items.append(it)
        body = {"model": model, "input": items, "store": False, "stream": True}
        if instructions:
            body["instructions"] = instructions
        if json_schema is not None and self.cfg.json_mode == "schema":
            body["text"] = {"format": {"type": "json_schema", "name": "result", "strict": True, "schema": strict_schema(json_schema)}}
        if effort:
            body["reasoning"] = {"effort": effort}
        for field in self._rejected.get(model, ()):
            body.pop(field, None)
        return body

    def _open(self, token, body, timeout, deadline):
        url = self.cfg.api_base + "/responses"
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                raise BrainError("ChatGPT 逾時", code="timeout")
            timeout = max(0.5, min(float(timeout), left))
        req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST", headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "text/event-stream",
        })
        try:
            return _opener_for(url).open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            try:
                raw = e.read(4000)
            except OSError:
                raw = b""
            raise _Fail(e.code, _api_error(raw), e.headers.get("x-request-id", "") if e.headers else "") from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            reason = getattr(e, "reason", e)
            if isinstance(e, TimeoutError) or isinstance(reason, TimeoutError):
                raise BrainError("ChatGPT 沒有回應，已逾時", code="timeout") from None
            raise BrainError("連不到 ChatGPT", code="network") from None

    def _events(self, lines, json_schema, cancel, deadline, max_chars):
        """讀串流事件，產生文字片段；只有 response.completed 才算成功（max_chars 提早結束也算）。"""
        total = 0
        for name, payload in parse_sse(lines):
            if cancel is not None and cancel.is_set():
                raise BrainError("已取消", code="cancelled", fallback=False)
            if deadline is not None and time.monotonic() > deadline:
                raise BrainError("ChatGPT 逾時", code="timeout")
            if payload == "[DONE]":
                break
            data = _json(payload)
            kind = data.get("type") or name
            if kind == "response.output_text.delta":
                piece = data.get("delta")
                if not isinstance(piece, str) or not piece:
                    continue
                if max_chars and total + len(piece) >= max_chars:
                    cut = _sentence_cut(piece, max_chars - total - 1)
                    if cut is not None:
                        yield piece[:cut]  # 夠長了：講完這句就停，剩下的不要
                        return
                total += len(piece)
                yield piece
            elif kind == "response.completed":
                if total == 0:
                    text = _completed_text(data)
                    if text:
                        yield text
                return
            elif kind == "response.incomplete":
                if total and json_schema is None:
                    return  # 一般對話已經有內容：當作講完了
                raise BrainError("ChatGPT 回覆不完整", code="incomplete")
            elif kind == "response.failed":
                err = (data.get("response") or {}).get("error") or {}
                if not isinstance(err, dict):
                    err = {}
                raise _Fail(200, {"code": str(err.get("code") or ""), "message": str(err.get("message") or "")[:300],
                                  "param": str(err.get("param") or "")})
            elif kind == "error":
                err = data.get("error") if isinstance(data.get("error"), dict) else data
                raise _Fail(200, {"code": str(err.get("code") or ""), "message": str(err.get("message") or "")[:300],
                                  "param": str(err.get("param") or "")})
        raise BrainError("ChatGPT 連線中斷", code="stream_interrupted")

    def _on_error(self, uid, model, status, err, started, body, tried, rid=""):
        """依錯誤碼決定：回 "retry"（拿掉不支援的欄位）、"refresh"（換新 token）或丟 BrainError。"""
        code, param = err.get("code") or "", err.get("param") or ""
        _log(f"HTTP {status} {code or '-'}{(' param=' + param) if param else ''}{(' request=' + rid) if rid else ''}")
        acct = self._acct(uid)
        top = _top(param)
        if code == "subscription_sharing_unsupported_capability" or (status == 400 and top in ("text", "reasoning")):
            if not started and top in ("text", "reasoning") and top in body and "param:" + top not in tried:
                tried.add("param:" + top)
                self._rejected.setdefault(model, set()).add(top)  # 記住這個模型不收，之後直接不送
                return "retry"
            raise BrainError("ChatGPT 不支援這個設定", code=code or "unsupported_capability", status=status, param=param, started=started)
        if code == "subscription_sharing_invalid_user" or status == 401:
            if not started and "auth" not in tried:
                tried.add("auth")
                return "refresh"
            self._mark(acct, needs_login=True)
            raise BrainError(self._needs_login_msg(), code="needs_login", status=status, started=started)
        if code == "subscription_sharing_usage_limit_exceeded":
            self.cooldown(uid, self.cfg.cooldown_sec)
            raise BrainError("ChatGPT 用量已達上限，暫用備援", code=code, status=status, started=started)
        if code in ("subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable"):
            self.cooldown(uid, 60)
            raise BrainError("ChatGPT 暫時不能用，暫用備援", code=code, status=status, started=started)
        if code in ("chatpass_v2_scope_not_authorized", "chatpass_v2_invalid_authorization_context"):
            self._mark(acct, needs_login=True, reconsent=True)  # 重新登入時加 prompt=consent
            raise BrainError("ChatGPT 方案權限不足，請重新登入並同意使用方案", code=code, status=status, started=started)
        if code == "subscription_sharing_user_not_eligible":
            self._mark(acct, plan_usage=False)
            raise BrainError("這個帳號不能用 ChatGPT 方案", code=code, status=status, started=started)
        if code in ("subscription_sharing_v2_client_not_enabled", "subscription_sharing_route_not_supported"):
            self.cooldown(uid, 6 * 3600)
            raise BrainError("ChatGPT 方案目前不能用在這個程式", code=code, status=status, started=started)
        if status == 403:
            self.cooldown(uid, 900)  # 權限或服務地區限制：先別一直試
            raise BrainError("ChatGPT 拒絕這次請求（權限或地區限制）", code=code or "forbidden", status=status, started=started)
        if status == 429:
            self.cooldown(uid, 60)
            raise BrainError("ChatGPT 太忙，暫用備援", code=code or "rate_limited", status=status, started=started)
        if status >= 500 or status == 0:
            raise BrainError("ChatGPT 暫時沒有回應", code=code or "server_error", status=status, started=started)
        if status == 200:
            raise BrainError("ChatGPT 回覆失敗", code=code or "failed", status=status, param=param, started=started)
        if status == 404 and model:
            try:
                self.store.eph_pop("cg-models:" + self._key(uid))  # 模型可能下架了：下次重抓清單
            except StoreError:
                pass
        raise BrainError(f"ChatGPT 回應錯誤（{code or status}）", code=code or "bad_request", status=status, param=param, started=started)

    def stream(self, uid, instructions, input_items, *, model, json_schema=None, reasoning_effort=None,
               timeout=60, deadline=None, cancel=None, max_chars=None):
        """產生回覆文字片段。timeout 是每次等資料的秒數；deadline 是 time.monotonic() 的截止時間。失敗丟 BrainError。"""
        cfg = self.cfg
        if not cfg.available:
            raise BrainError(cfg.blocked_reason or "ChatGPT 不能用", code="blocked")
        if self.cooldown_until(uid):
            raise BrainError("ChatGPT 用量已達上限，暫用備援", code="cooldown")
        model = model or self._auto_model(uid)
        effort = cfg.reasoning_effort if reasoning_effort is None else _effort(reasoning_effort)
        token = self._token(uid)
        tried = set()
        started = False
        while True:
            body = self._body(model, instructions, input_items, json_schema, effort)
            try:
                resp = self._open(token, body, timeout, deadline)
            except _Fail as f:
                if self._on_error(uid, model, f.status, f.err, False, body, tried, f.request_id) == "refresh":
                    token = self._token(uid, force=True, stale=token)
                continue
            try:
                for piece in self._events(resp, json_schema, cancel, deadline, max_chars):
                    started = True
                    yield piece
                return
            except _Fail as f:
                if self._on_error(uid, model, f.status, f.err, started, body, tried) == "refresh":
                    token = self._token(uid, force=True, stale=token)
                continue
            except BrainError as e:
                e.started = e.started or started
                raise
            except (OSError, http.client.HTTPException, ValueError) as e:  # 讀到一半逾時或斷線
                if isinstance(e, TimeoutError):
                    raise BrainError("ChatGPT 沒有回應，已逾時", code="timeout", started=started) from None
                raise BrainError("ChatGPT 連線中斷", code="stream_interrupted", started=started) from None
            finally:
                resp.close()
