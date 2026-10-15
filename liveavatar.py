"""真人模式（HeyGen LiveAvatar，LITE「Avatar Only」）：開、關工作階段，列出可以用的臉孔。只用 Python 標準函式庫。
客戶的聲音還是 Gemini Live 的，LiveAvatar 只負責照聲音對嘴、畫出真人影像。
金鑰只在伺服器這邊用：不回給瀏覽器，也不放進錯誤訊息或記錄。"""

import json
import re
import urllib.error
import urllib.request

BASE = "https://api.liveavatar.com"
SANDBOX_AVATAR = "dd73ea75-1218-4ef3-92ce-606d5f7fbc0a"  # 測試模式（不扣點數）只能用 Wayne
SANDBOX_NAME = "Wayne"
PAGE_SIZE = 100
SANDBOX_MAX_SEC = 60  # 測試模式最多 60 秒，送比這長的會被拒絕
USER_AGENT = "AICoachDemo/1.0 (+liveavatar)"
_PLAN_RE = re.compile(r"\b(plan|subscription|upgrade)\b")


class AvatarError(Exception):
    """kind：key 金鑰不對、plan 要付費方案、credits 點數用完、avatar 找不到臉孔、sandbox 測試模式限制、
    busy 太忙、blocked 被 Cloudflare 擋、server LiveAvatar 出錯、network 連不到、other 其他。
    message 給使用者看，detail 是原始訊息（不含金鑰）。"""

    def __init__(self, message, code=0, detail="", kind="other"):
        super().__init__(message)
        self.code = code
        self.detail = detail
        self.kind = kind


def _message(raw):
    """從錯誤回應挑出看得懂的那句：{message}、FastAPI 的 {detail}（字串或清單）或 {error}。"""
    try:
        doc = json.loads(raw)
    except ValueError:
        return raw.strip()[:200]
    if not isinstance(doc, dict):
        return raw.strip()[:200]
    detail = doc.get("detail")
    if isinstance(detail, list):
        parts = []
        for d in detail:
            if isinstance(d, dict):
                loc = ".".join(str(x) for x in d.get("loc") or [] if x != "body")
                parts.append(f"{loc}: {d.get('msg', '')}" if loc else str(d.get("msg", "")))
        detail = "；".join(p for p in parts if p)
    err = doc.get("error")
    if isinstance(err, dict):
        err = err.get("message")
    for v in (doc.get("message"), detail, err):
        if isinstance(v, str) and v.strip():
            return v.strip()[:200]
    return raw.strip()[:200]


def _error(status, detail):
    """把 LiveAvatar 的錯誤翻成看得懂的中文。官方沒有列出錯誤代碼，只能看 HTTP 狀態和訊息裡的關鍵字。"""
    text = detail.lower()
    if "cloudflare" in text or "error 1010" in text:
        return AvatarError("LiveAvatar 的防護（Cloudflare）擋住了這個連線，請稍後再試", status, detail, "blocked")
    if status in (400, 402, 403) and _PLAN_RE.search(text):
        return AvatarError("這個臉孔要 LiveAvatar 付費方案才能用（免費版只能用公開臉孔）", status, detail, "plan")
    if status == 402 or any(w in text for w in ("credit", "insufficient", "balance", "quota")):
        return AvatarError("LiveAvatar 的點數用完了（免費版每月 10 分鐘）。請到 app.liveavatar.com 加值，"
                           "或先用測試模式（LIVEAVATAR_SANDBOX=1）", status, detail, "credits")
    if status in (401, 403) or "api key" in text or "api_key" in text or "unauthorized" in text:
        return AvatarError("LiveAvatar 金鑰不對", status, detail, "key")
    if "sandbox" in text:
        return AvatarError("測試模式只能用 Wayne 這個臉孔", status, detail, "sandbox")
    if "avatar" in text and (status == 404 or any(w in text for w in ("not found", "not exist", "invalid", "inactive"))):
        return AvatarError("找不到這個真人臉孔，可能已經刪除：請到「聲音設定」重新挑一個", status, detail, "avatar")
    if status == 429 or "concurrent" in text or "too many" in text:
        return AvatarError("LiveAvatar 太忙或同時開了太多真人畫面，請等一下再試", status, detail, "busy")
    if status >= 500:
        return AvatarError(f"LiveAvatar 暫時有問題（HTTP {status}），請稍後再試", status, detail, "server")
    short = detail[:120] or "沒有說明"
    return AvatarError(f"LiveAvatar 回應錯誤（HTTP {status}）：{short}", status, detail, "other")


def _row(a, custom):
    """只留畫面要用的欄位；還在訓練、失敗或過期的臉孔不列出來。"""
    if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not a.get("id"):
        return None
    if str(a.get("status") or "ACTIVE").upper() != "ACTIVE" or a.get("is_expired"):
        return None
    preview = a.get("preview_url") if isinstance(a.get("preview_url"), str) else ""
    return {
        "id": a["id"],
        "name": str(a.get("name") or "")[:60] or a["id"][:8],
        "preview_url": preview if preview.startswith("https://") else "",
        "custom": custom,
    }


class LiveAvatar:
    service = "LiveAvatar"

    def __init__(self, key, base=BASE):
        self.key = key
        self.base = base.rstrip("/")

    def _call(self, method, path, body=None, bearer="", timeout=20):
        """回傳回應裡的 data（沒有就回 {}）。bearer：用工作階段的通行證，不用金鑰。"""
        # 一定要有自己的 User-Agent：用 urllib 預設的「Python-urllib」，Cloudflare 會擋掉 /v1/sessions/start（實測 Error 1010）
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        else:
            headers["X-API-KEY"] = self.key
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.build_opener().open(req, timeout=timeout) as r:
                payload = r.read()
        except urllib.error.HTTPError as e:
            raise _error(e.code, _message(e.read().decode("utf-8", "replace")[:800])) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise AvatarError("連不到 LiveAvatar，請檢查網路後再試", 0, str(getattr(e, "reason", e))[:200], "network") from None
        try:
            doc = json.loads(payload.decode("utf-8") or "{}")
        except ValueError:
            raise AvatarError("LiveAvatar 回傳的資料看不懂") from None
        out = doc.get("data") if isinstance(doc, dict) else None
        return out if isinstance(out, dict) else {}

    def _results(self, path):
        data = self._call("GET", f"{path}?page=1&page_size={PAGE_SIZE}")
        rows = data.get("results")
        return rows if isinstance(rows, list) else []

    def avatars(self):
        """自己帳號的臉孔（custom）排前面，再接公開的（最多 100 個）。"""
        try:
            mine = self._results("/v1/avatars")
        except AvatarError as e:
            if e.kind == "key":
                raise
            mine = []  # 免費方案可能看不了自己的清單：只列公開的
        public = self._results("/v1/avatars/public")
        rows, seen = [], set()
        for custom, items in ((True, mine), (False, public)):
            for a in items:
                row = _row(a, custom)
                if row and row["id"] not in seen:
                    seen.add(row["id"])
                    rows.append(row)
        return rows

    def start(self, avatar_id, sandbox=False, max_seconds=0):
        """建立 LITE 工作階段並啟動。回傳瀏覽器要用的連線資料；livekit_agent_token 權限比較大，不往外給。"""
        body = {
            "mode": "LITE",
            "avatar_id": avatar_id,
            "is_sandbox": bool(sandbox),
            "video_settings": {"quality": "high", "encoding": "H264"},
        }
        if max_seconds and not sandbox:  # 測試模式本來就只有 60 秒，送了反而被拒絕
            body["max_session_duration"] = int(max_seconds)
        try:
            tok = self._call("POST", "/v1/sessions/token", body)
        except AvatarError as e:
            if "max_session_duration" not in body or e.code not in (400, 422) or "duration" not in e.detail.lower():
                raise
            body.pop("max_session_duration")  # 方案的上限比較短：改用 LiveAvatar 的預設
            tok = self._call("POST", "/v1/sessions/token", body)
        sid, token = tok.get("session_id"), tok.get("session_token")
        if not isinstance(sid, str) or not sid or not isinstance(token, str) or not token:
            raise AvatarError("LiveAvatar 沒有回傳工作階段")
        try:
            data = self._call("POST", "/v1/sessions/start", bearer=token)
            if not all(isinstance(data.get(k), str) and data.get(k) for k in ("livekit_url", "livekit_client_token", "ws_url")):
                raise AvatarError("LiveAvatar 沒有回傳連線資料")
        except AvatarError:
            self.stop(sid, "SERVER_ERROR", quiet=True)  # 開到一半失敗：先收掉，不要白扣點數
            raise
        try:
            limit = int(data.get("max_session_duration") or body.get("max_session_duration") or (SANDBOX_MAX_SEC if sandbox else 0))
        except (TypeError, ValueError):
            limit = 0
        return {
            "session_id": str(data.get("session_id") or sid),
            "session_token": token,
            "livekit_url": data["livekit_url"],
            "livekit_client_token": data["livekit_client_token"],
            "ws_url": data["ws_url"],
            "max_session_duration": limit,
        }

    def stop(self, session_id, reason="USER_CLOSED", quiet=False):
        """結束工作階段。已經結束（或找不到）回 False，可以放心重複呼叫；quiet：其他錯誤也不丟。"""
        try:
            self._call("POST", "/v1/sessions/stop", {"session_id": session_id, "reason": reason}, timeout=10)
            return True
        except AvatarError as e:
            if quiet or e.code in (400, 404, 409, 410):
                return False
            raise
