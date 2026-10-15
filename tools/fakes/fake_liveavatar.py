"""假的 LiveAvatar（只用來測試，不會連到真的 api.liveavatar.com）：
/v1/sessions/token、/v1/sessions/start、/v1/sessions/stop、/v1/sessions/keep-alive、/v1/avatars、/v1/avatars/public。
用法：python tools/fakes/fake_liveavatar.py --port 9104
server.py 要指到這裡：LIVEAVATAR_API_KEY=fake-la-key、LIVEAVATAR_BASE_URL=<url>
金鑰開頭是 bad 就當成金鑰不對。子程序測試可以用 POST /__control {"credits":0,"fail_start":true,"max_duration":600,
"mine_status":403}、GET /__state。WebSocket（ws_url）沒有做：標準函式庫沒有 WebSocket 伺服器。"""

import os
import secrets
import sys
import threading
import uuid
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import Handler, run_forever, serve  # noqa: E402

WAYNE = "dd73ea75-1218-4ef3-92ce-606d5f7fbc0a"
ANN = "4c5b3a1e-2f6d-4e8a-9b7c-0d1e2f3a4b5c"
JUDY = "7f8e9d0c-1b2a-4c3d-8e5f-6a7b8c9d0e1f"
MINE = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
TRAINING = "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e"
EXPIRED = "c3d4e5f6-a7b8-4c9d-8e0f-2a3b4c5d6e7f"


def _avatar(aid, name, status="ACTIVE", kind="VIDEO", expired=False, preview=True):
    return {
        "id": aid, "space_id": "space_fake", "type": kind, "status": status, "name": name,
        "preview_url": f"https://files.fake-liveavatar.test/{aid}.webp" if preview else None,
        "default_voice": None, "default_voice_agent_id": None, "is_1080p": False,
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        "is_expired": expired, "error_message": None,
    }


PUBLIC = [
    _avatar(WAYNE, "Wayne"),
    _avatar(ANN, "Ann Therapist"),
    _avatar(JUDY, "Judy Doctor Sitting", preview=False),
    _avatar(TRAINING, "還在訓練", status="DEPLOYING"),
]
OWN = [
    _avatar(MINE, "我的臉", kind="IMAGE"),
    _avatar(EXPIRED, "過期的臉", expired=True),
]


class LAState:
    def __init__(self):
        self.lock = threading.Lock()
        self.base = ""
        # credits：0 就不能開（測試模式不扣）；max_duration：方案上限（0 = 不限制）；mine_status：列自己的臉孔回什麼
        self.knobs = {"credits": 100, "fail_start": False, "max_duration": 0, "mine_status": 200}
        self.sessions = {}  # session_id -> {token, avatar_id, sandbox, state, body}
        self.calls = []  # (方法, 路徑)
        self.stops = []  # 真的被收掉的 session_id

    def snapshot(self):
        with self.lock:
            return {"calls": list(self.calls), "knobs": dict(self.knobs), "stops": list(self.stops),
                    "sessions": {k: {x: v[x] for x in ("avatar_id", "sandbox", "state", "body")} for k, v in self.sessions.items()}}


def _ok(data, code=200):
    return code, {"code": 100, "data": data, "message": "Success"}


def _err(code, message):
    return code, {"code": code, "data": None, "message": message}


class H(Handler):
    def key(self):
        return self.headers.get("X-API-KEY") or ""

    def bearer(self):
        auth = self.headers.get("Authorization") or ""
        return auth[7:] if auth.startswith("Bearer ") else ""

    def key_ok(self):
        k = self.key()
        if not k or k.startswith("bad"):
            self.reply(*_err(401, "Invalid API key"))
            return False
        return True

    def do_GET(self):
        st = self.state
        u = urlparse(self.path)
        if u.path == "/__state":
            return self.reply(200, st.snapshot())
        with st.lock:
            st.calls.append(("GET", u.path))
            k = dict(st.knobs)
        if not self.key_ok():
            return
        q = parse_qs(u.query)
        try:
            page = max(1, int((q.get("page") or ["1"])[0]))
            size = int((q.get("page_size") or ["20"])[0])
        except ValueError:
            return self.reply(422, {"detail": [{"loc": ["query", "page"], "msg": "not an integer", "type": "int_parsing"}]})
        if not 1 <= size <= 100:
            return self.reply(422, {"detail": [{"loc": ["query", "page_size"], "msg": "must be <= 100", "type": "less_than_equal"}]})
        if u.path == "/v1/avatars/public":
            rows = PUBLIC
        elif u.path == "/v1/avatars":
            if k["mine_status"] != 200:
                return self.reply(*_err(int(k["mine_status"]), "Not allowed on your plan"))
            rows = OWN
        else:
            return self.reply(*_err(404, "Not Found"))
        chunk = rows[(page - 1) * size:page * size]
        nxt = f"{st.base}{u.path}?page={page + 1}&page_size={size}" if page * size < len(rows) else None
        self.reply(*_ok({"count": len(rows), "next": nxt, "previous": None, "results": chunk}))

    def do_POST(self):
        st = self.state
        u = urlparse(self.path)
        body = self.body_json()
        if u.path == "/__control":
            with st.lock:
                st.knobs.update(body or {})
            return self.reply(200, {"ok": True})
        with st.lock:
            st.calls.append(("POST", u.path))
        if u.path == "/v1/sessions/token":
            return self.token(body if isinstance(body, dict) else {})
        if u.path == "/v1/sessions/start":
            return self.start()
        if u.path in ("/v1/sessions/stop", "/v1/sessions/keep-alive"):
            return self.stop_or_keep(u.path, body if isinstance(body, dict) else {})
        self.reply(*_err(404, "Not Found"))

    def token(self, body):
        st = self.state
        if not self.key_ok():
            return
        if body.get("mode") != "LITE":
            return self.reply(422, {"detail": [{"loc": ["body", "mode"], "msg": "this fake only does LITE", "type": "literal_error"}]})
        aid = body.get("avatar_id")
        if aid not in {a["id"] for a in PUBLIC + OWN if a["status"] == "ACTIVE" and not a["is_expired"]}:
            return self.reply(*_err(404, "Avatar not found"))
        sandbox = bool(body.get("is_sandbox"))
        if sandbox and aid != WAYNE:
            return self.reply(*_err(400, "Sandbox mode only supports the Wayne avatar"))
        if sandbox and int(body.get("max_session_duration") or 0) > 60:  # 真的服務也這樣回（實測）
            asked = int(body["max_session_duration"])
            return self.reply(*_err(400, f"max_session_duration ({asked}s) exceeds the maximum allowed (60s)"))
        with st.lock:
            k = dict(st.knobs)
        limit = int(k.get("max_duration") or 0)
        if limit and int(body.get("max_session_duration") or 0) > limit:
            return self.reply(422, {"detail": [{"loc": ["body", "max_session_duration"],
                                                "msg": f"max_session_duration exceeds your plan limit ({limit})", "type": "value_error"}]})
        if not sandbox and int(k.get("credits") or 0) <= 0:
            return self.reply(*_err(402, "Insufficient credits"))
        sid = str(uuid.uuid4())
        token = "st_" + secrets.token_hex(12)
        with st.lock:
            st.sessions[sid] = {"token": token, "avatar_id": aid, "sandbox": sandbox, "state": "created", "body": body}
        self.reply(*_ok({"session_id": sid, "session_token": token}))

    def start(self):
        st = self.state
        if (self.headers.get("User-Agent") or "").startswith("Python-urllib"):
            # 真的服務前面有 Cloudflare：urllib 預設的 User-Agent 會被擋（實測 Error 1010）
            return self.reply(403, {"type": "https://developers.cloudflare.com/support/troubleshooting/http-status-codes/"
                                            "cloudflare-1xxx-errors/error-1010/", "title": "Error 1010: Access denied",
                                    "status": 403, "detail": "The site owner has blocked access based on your browser's signature."})
        token = self.bearer()
        with st.lock:
            k = dict(st.knobs)
            sid, sess = next(((i, s) for i, s in st.sessions.items() if s["token"] == token and token), (None, None))
        if sess is None:
            return self.reply(*_err(401, "Invalid session token"))
        if k.get("fail_start"):
            return self.reply(*_err(500, "Failed to dispatch avatar"))
        if sess["state"] != "created":
            return self.reply(*_err(400, "Session already started"))
        port = self.server.server_port
        with st.lock:
            sess["state"] = "started"
        limit = 60 if sess["sandbox"] else int(sess["body"].get("max_session_duration") or 1200)
        self.reply(*_ok({
            "session_id": sid,
            "livekit_url": "wss://fake-livekit.test",
            "livekit_client_token": "lk_client_" + secrets.token_hex(8),
            "livekit_agent_token": "lk_agent_" + secrets.token_hex(8),
            "max_session_duration": limit,
            "ws_url": f"ws://127.0.0.1:{port}/v1/ws/{sid}",
        }, 201))

    def stop_or_keep(self, path, body):
        st = self.state
        token = self.bearer()
        with st.lock:
            by_token = next((i for i, s in st.sessions.items() if s["token"] == token and token), None)
        if not token and not self.key_ok():
            return
        sid = body.get("session_id") or by_token
        with st.lock:
            sess = st.sessions.get(sid)
            if sess is None:
                out = _err(404, "Session not found")
            elif token and by_token != sid:
                out = _err(403, "Not your session")
            elif sess["state"] == "stopped":
                out = _err(400, "Session already stopped")
            else:
                out = _ok(None)
                if path.endswith("/stop"):
                    sess["state"] = "stopped"
                    st.stops.append(sid)
        self.reply(*out)


def start(port=None):
    state = LAState()
    srv = serve(H, port, state)
    state.base = srv.url
    return srv


if __name__ == "__main__":
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1]) if "--port" in args else 9104
    run_forever(H, port, LAState(), "fake liveavatar")
