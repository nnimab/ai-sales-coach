"""假的自架臉孔引擎（只用來測試，不用顯卡、不連 LiveKit）：照 RunPod 上 127.0.0.1:8010 的約定
POST /face/session、POST /face/stop、GET /face/health。一張顯卡一次只能一通：第二通會被拒絕。
用法：python tools/fakes/fake_faceengine.py --port 9105 --secret <FACE_SECRET>
server.py 要指到這裡：FACE_SECRET=<同一個>、FACE_URL=<url>、FACE_PUBLIC_URL=ws://127.0.0.1:<port>
子程序測試可以用 POST /__control {"ready":false,"faces":["wang"],"busy_status":409,"fail":"..."}、GET /__state。
控制連線（WebSocket）沒有做：標準函式庫沒有 WebSocket 伺服器（網頁那邊的邏輯在 tests/js 用假的測）。"""

import os
import sys
import threading
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import Handler, run_forever, serve  # noqa: E402

SECRET = "fake-face-secret-0123456789"
FIELDS = ("session_id", "face", "expression", "livekit_url", "livekit_token", "max_seconds")


class FEState:
    def __init__(self, secret=SECRET):
        self.lock = threading.Lock()
        self.base = ""
        self.secret = secret
        # ready：模型載入好了沒；busy_status：有人在用時回的 HTTP 狀態（真的引擎可能回 200 + ok:false）；fail：開始時直接失敗
        self.knobs = {"ready": True, "faces": ["wang"], "busy_status": 409, "fail": ""}
        self.sessions = {}  # session_id -> 收到的 body
        self.active = ""  # 現在佔著顯卡的 session_id
        self.calls = []  # (方法, 路徑)
        self.stops = []  # 收到的 stop（包含重複的）
        self.bad_secret = 0

    def snapshot(self):
        with self.lock:
            return {"calls": list(self.calls), "knobs": dict(self.knobs), "stops": list(self.stops),
                    "active": self.active, "sessions": dict(self.sessions), "bad_secret": self.bad_secret}


class H(Handler):
    def secret_ok(self):
        if self.headers.get("X-Face-Secret") != self.state.secret or not self.state.secret:
            with self.state.lock:
                self.state.bad_secret += 1
            self.reply(401, {"ok": False, "error": "密碼不對"})
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
            n = 1 if st.active else 0
        if u.path == "/face/health":  # 不用密碼（約定上沒有要求）
            return self.reply(200, {"ok": True, "gpu": "Fake GPU 24GB", "faces": list(k["faces"]), "sessions": n,
                                    "ready": bool(k["ready"])})
        self.reply(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        st = self.state
        u = urlparse(self.path)
        body = self.body_json()
        if u.path == "/__control":
            with st.lock:
                st.knobs.update(body or {})
                if (body or {}).get("reset"):
                    st.sessions.clear()
                    st.calls.clear()
                    st.stops.clear()
                    st.active = ""
                    st.bad_secret = 0
            return self.reply(200, {"ok": True})
        with st.lock:
            st.calls.append(("POST", u.path))
        if not self.secret_ok():
            return
        body = body if isinstance(body, dict) else {}
        if u.path == "/face/session":
            return self.session(body)
        if u.path == "/face/stop":
            sid = str(body.get("session_id") or "")
            with st.lock:
                st.stops.append(sid)
                if st.active == sid:
                    st.active = ""
            return self.reply(200, {"ok": True})
        self.reply(404, {"ok": False, "error": "not found"})

    def session(self, body):
        st = self.state
        missing = [f for f in FIELDS if f not in body]
        if missing:
            return self.reply(400, {"ok": False, "error": "少了 " + "、".join(missing)})
        with st.lock:
            k = dict(st.knobs)
            if not k["ready"]:
                return self.reply(503, {"ok": False, "error": "臉孔引擎還在載入，請稍後再試"})
            if k["fail"]:
                return self.reply(500, {"ok": False, "error": k["fail"]})
            if body["face"] not in k["faces"]:
                return self.reply(404, {"ok": False, "error": "沒有這個臉孔"})
            if st.active and st.active != body["session_id"]:
                return self.reply(int(k["busy_status"]), {"ok": False, "error": "真人正在和別人通話（一次只能一通），請稍後再試"})
            st.active = body["session_id"]
            st.sessions[body["session_id"]] = dict(body)
        self.reply(200, {"ok": True, "ws_path": "/face/ws"})


def start(port=None, secret=SECRET):
    state = FEState(secret)
    srv = serve(H, port, state)
    state.base = srv.url
    return srv


if __name__ == "__main__":
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1]) if "--port" in args else 9105
    secret = args[args.index("--secret") + 1] if "--secret" in args else SECRET
    run_forever(H, port, FEState(secret), "fake faceengine")
