"""雲端版的登入 cookie：內容用 HMAC-SHA256 簽名，改過就無效。只用標準函式庫。"""

import base64
import hashlib
import hmac
import json
import secrets
import time

COOKIE = "aic_session"
# payload：{"uid","name","role":"user"|"admin","pv","iat","exp"}


def _b64e(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(body, secret):
    return hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()


def sign(payload, secret):
    if not secret:
        raise ValueError("沒有 APP_SECRET，不能簽發登入")
    body = _b64e(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return body + "." + _b64e(_mac(body, secret))


def verify(token, secret, check_exp=True):
    """簽名對、沒過期才回傳內容；任何問題都回 None（不丟例外）。"""
    if not token or not secret or not isinstance(token, str) or len(token) > 4096:
        return None
    body, sep, sig = token.partition(".")
    if not sep or not body or not sig:
        return None
    try:
        if not hmac.compare_digest(_b64d(sig), _mac(body, secret)):
            return None
        payload = json.loads(_b64d(body).decode("utf-8"))
    except (ValueError, TypeError, UnicodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if check_exp:
        exp = payload.get("exp")
        if not isinstance(exp, (int, float)) or isinstance(exp, bool) or exp <= time.time():
            return None
    return payload


def parse_cookies(header):
    """{名稱: 值}；同名的取第一個（瀏覽器會把最符合的放前面）。格式壞掉的片段直接略過。"""
    out = {}
    for part in (header or "").split(";"):
        name, sep, value = part.strip().partition("=")
        name = name.strip()
        if not sep or not name or name in out:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        out[name] = value
    return out


def set_cookie(token, secure, max_age):
    cookie = f"{COOKIE}={token}; Max-Age={int(max_age)}; Path=/; HttpOnly; SameSite=Lax"
    return cookie + "; Secure" if secure else cookie


def clear_cookie(secure):
    cookie = f"{COOKIE}=; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/; HttpOnly; SameSite=Lax"
    return cookie + "; Secure" if secure else cookie


def passcode_ok(given, expected):
    # 先各自雜湊成一樣長再比，比對時間不會洩漏長度或內容
    if not expected or not isinstance(given, str):
        return False
    a = hashlib.sha256(given.encode("utf-8")).digest()
    b = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(a, b)


def passcode_version(expected):
    """通行碼一改，舊的登入就失效。"""
    return hashlib.sha256((expected or "").encode("utf-8")).hexdigest()[:12]


def new_uid():
    return secrets.token_hex(16)
