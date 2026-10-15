"""真人模式（自架臉孔）：同一台機器上的臉孔引擎（RunPod 上的 127.0.0.1:8010），和 HeyGen 一樣用 LiveKit 傳影像。
這裡負責：簽 LiveKit 通行證（JWT，HS256）、開和關引擎的工作階段、看引擎好了沒、簽瀏覽器控制連線的網址。只用標準函式庫。
FACE_SECRET 和 LIVEKIT_API_SECRET 只在伺服器用：不回給瀏覽器，也不放進錯誤訊息或記錄。"""

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlparse

EXPRESSIONS = ("neutral", "worried", "annoyed", "happy")
TOKEN_TTL = 600  # LiveKit 通行證 10 分鐘：只用來進房間，連上之後 LiveKit 會自己換新的
WS_TTL = 300  # 控制連線的簽名 5 分鐘內要用掉
KEEPALIVE_MS = 20000  # RunPod 的代理（Cloudflare）閒置 100 秒就斷：每 20 秒報一次平安
HEARD_LAG_SEC = 0.25  # 實測：引擎說開口後約 0.15 秒聲音到台灣（HeyGen 要 0.6 秒）
USER_AGENT = "AICoachDemo/1.0 (+faceengine)"


class FaceError(Exception):
    """kind：busy 有人在用（一張顯卡一次一通）、face 沒有這個臉孔、network 引擎沒開、server 引擎出錯、other。
    message 給使用者看（引擎回的中文原句，或我們的說明），detail 是原始訊息（不含密碼）。"""

    def __init__(self, message, code=0, detail="", kind="other"):
        super().__init__(message)
        self.code = code
        self.detail = detail
        self.kind = kind


def _b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def livekit_token(api_key, api_secret, identity, room, publish, subscribe, ttl=TOKEN_TTL, name="", now=None):
    """LiveKit 的通行證：只能進 room 這一個房間。publish：引擎送影像和聲音；subscribe：瀏覽器看和聽。
    格式照 LiveKit 的 AccessToken（iss＝API key、sub＝身分、video＝權限）。"""
    if not api_key or not api_secret:
        raise ValueError("沒有 LiveKit 金鑰")
    now = int(time.time() if now is None else now)
    grant = {"room": room, "roomJoin": True, "canPublish": bool(publish), "canSubscribe": bool(subscribe),
             "canPublishData": bool(publish)}
    claims = {"iss": api_key, "sub": identity, "nbf": now - 5, "exp": now + int(ttl), "video": grant}
    if name:
        claims["name"] = name
    head = _b64u(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("ascii"))
    body = _b64u(json.dumps(claims, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    sig = hmac.new(api_secret.encode("utf-8"), f"{head}.{body}".encode("ascii"), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64u(sig)}"


def ws_sig(secret, session_id, exp):
    """瀏覽器控制連線的簽名：HMAC-SHA256(FACE_SECRET, "<session_id>.<exp>")，十六進位。"""
    return hmac.new(secret.encode("utf-8"), f"{session_id}.{int(exp)}".encode("utf-8"), hashlib.sha256).hexdigest()


def _message(raw):
    """引擎的錯誤回應：{"ok": false, "error": "中文說明"}；看不懂就留原文前 200 字。"""
    try:
        doc = json.loads(raw)
    except ValueError:
        return raw.strip()[:200]
    if isinstance(doc, dict):
        for k in ("error", "message", "detail"):
            v = doc.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()[:200]
    return raw.strip()[:200]


def _kind(status, text):
    t = text.lower()
    if status == 409 or status == 429 or "busy" in t or "使用中" in text or "忙" in text or "一次" in text:
        return "busy"
    if status == 404 or "face" in t and ("not found" in t or "unknown" in t) or "臉孔" in text:
        return "face"
    if status in (401, 403):
        return "secret"
    if status >= 500:
        return "server"
    return "other"


class FaceEngine:
    service = "自架真人"

    def __init__(self, base, secret, public_url):
        self.base = (base or "").rstrip("/")
        self._secret = secret or ""
        self.public_url = (public_url or "").rstrip("/")
        # 引擎在同一台機器：不走系統代理
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _call(self, method, path, body=None, timeout=20):
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT, "X-Face-Secret": self._secret}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=timeout) as r:
                payload = r.read()
        except urllib.error.HTTPError as e:
            detail = _message(e.read(2000).decode("utf-8", "replace"))
            kind = _kind(e.code, detail)
            if kind == "secret":
                raise FaceError("自架真人的密碼（FACE_SECRET）對不上，請檢查伺服器設定", e.code, detail, kind) from None
            text = detail if detail and not detail.startswith("<") else f"自架真人引擎回應錯誤（HTTP {e.code}）"
            raise FaceError(text, e.code, detail, kind) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise FaceError("自架真人引擎沒有開（或還在啟動），請稍後再試", 0, str(getattr(e, "reason", e))[:200],
                            "network") from None
        try:
            doc = json.loads(payload.decode("utf-8") or "{}")
        except ValueError:
            raise FaceError("自架真人引擎回傳的資料看不懂", 0, payload[:200].decode("utf-8", "replace")) from None
        return doc if isinstance(doc, dict) else {}

    def health(self, timeout=2.0):
        """{"ok", "ready", "gpu", "faces": [...], "sessions"}；連不上就丟 FaceError。"""
        doc = self._call("GET", "/face/health", timeout=timeout)
        faces = []
        for f in doc.get("faces") if isinstance(doc.get("faces"), list) else []:
            name = f.get("id") or f.get("name") if isinstance(f, dict) else f  # ["wang"] 或 [{"id": "wang"}] 都認
            if isinstance(name, str) and name and name not in faces:
                faces.append(name[:40])
        return {
            "ok": bool(doc.get("ok")),
            "ready": bool(doc.get("ready")),
            "gpu": str(doc.get("gpu") or "")[:80],
            "faces": faces[:50],
            "sessions": doc.get("sessions") if isinstance(doc.get("sessions"), int) else 0,
        }

    def start(self, session_id, face, expression, livekit_url, livekit_token, max_seconds):
        """請引擎進 LiveKit 房間開始送影像；回傳 ws_path。引擎說不行（例如有人在用）就丟 FaceError。"""
        doc = self._call("POST", "/face/session", {
            "session_id": session_id, "face": face, "expression": expression if expression in EXPRESSIONS else "neutral",
            "livekit_url": livekit_url, "livekit_token": livekit_token, "max_seconds": int(max_seconds),
        }, timeout=30)
        if not doc.get("ok"):
            detail = str(doc.get("error") or "")[:200]
            raise FaceError(detail or "自架真人引擎沒有開始", 200, detail, _kind(200, detail))
        path = doc.get("ws_path")
        return path if isinstance(path, str) and path.startswith("/") else "/face/ws"

    def stop(self, session_id, quiet=True):
        """結束；重複呼叫或已經結束都沒關係。quiet：錯誤也不丟（掛斷時不能因此卡住）。"""
        try:
            self._call("POST", "/face/stop", {"session_id": session_id}, timeout=10)
            return True
        except FaceError:
            if quiet:
                return False
            raise

    def ws_url(self, ws_path, session_id, ttl=WS_TTL, now=None):
        """瀏覽器連控制通道的網址（wss://<pod>-8010.proxy.runpod.net/face/ws?session=&exp=&sig=）。"""
        exp = int(time.time() if now is None else now) + int(ttl)
        query = urlencode({"session": session_id, "exp": exp, "sig": ws_sig(self._secret, session_id, exp)})
        return f"{self.public_url}{ws_path}?{query}"


def public_ok(url):
    """FACE_PUBLIC_URL 要是 wss://（本機測試可以 ws://）而且有主機名稱。"""
    try:
        u = urlparse(url or "")
    except ValueError:
        return False
    return u.scheme in ("wss", "ws") and bool(u.hostname)
