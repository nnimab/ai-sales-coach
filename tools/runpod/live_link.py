"""真人版的固定入口：每 5 分鐘把這台 Pod 的網址（https://<RUNPOD_POD_ID>-8765.proxy.runpod.net）回報給
LIVE_LINK_TARGET（Vercel 那一份）。手機永遠開 <LIVE_LINK_TARGET>/live：Pod 開著就轉過來，Pod 重建換了網址也一樣。
平常由 /workspace/start_all.sh 看顧（當掉會自動重開）；手動試一次：python3 /workspace/app/tools/runpod/live_link.py --once
要的環境變數（Pod 的設定；SSH 進來的 shell 沒有，就從 1 號程序補）：
  LIVE_LINK_SECRET   和 Vercel 上的一樣；只拿來算簽名，不印出來、也不送出去
  LIVE_LINK_TARGET   例如 https://ai-sales-coach-phi.vercel.app
  RUNPOD_POD_ID      RunPod 自己會設
少一個就安靜地什麼都不做。只用標準函式庫。"""

import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

EVERY = 300  # 秒；Vercel 那邊 15 分鐘沒收到就當作沒開
NEEDED = ("LIVE_LINK_SECRET", "LIVE_LINK_TARGET", "RUNPOD_POD_ID")
POD_RE = re.compile(r"[a-z0-9]{8,24}")  # 和 server.py 的 LIVE_URL_RE 一樣：網址只能是這種 Pod 代號
REGISTER_PATH = "/api/live/register"
# Vercel 前面的 Cloudflare 會擋 Python-urllib 這種預設的 User-Agent
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


def log(text):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 真人版入口：{text}", flush=True)


def pod_env(environ=None, proc="/proc/1/environ"):
    """要的三個變數；這個程序沒有的（SSH 進來開的），從 1 號程序的環境變數補上（已經有的不蓋掉）。"""
    environ = os.environ if environ is None else environ
    env = {k: (environ.get(k) or "").strip() for k in NEEDED}
    if all(env.values()):
        return env
    try:
        with open(proc, "rb") as f:
            raw = f.read()
    except OSError:
        return env
    for item in raw.split(b"\0"):
        k, sep, v = item.partition(b"=")
        k = k.decode("ascii", "replace")
        if sep and k in env and not env[k]:
            env[k] = v.decode("utf-8", "replace").strip()
    return env


def target_ok(target):
    """要回報的網站：https 才送（金鑰的簽名不能走明碼）；http 只給本機測試用。"""
    try:
        u = urlparse(target)
        host = u.hostname
    except ValueError:
        return False
    if not host or u.username or u.password or u.query or u.fragment:
        return False
    return u.scheme == "https" or (u.scheme == "http" and host in ("127.0.0.1", "localhost"))


def config(environ=None, proc="/proc/1/environ"):
    """回傳 (設定, 原因)；沒設好就是 (None, 為什麼)。"""
    env = pod_env(environ, proc)
    missing = [k for k in NEEDED if not env[k]]
    if missing:
        return None, "沒有設定 " + "、".join(missing) + "，不回報"
    if not POD_RE.fullmatch(env["RUNPOD_POD_ID"]):
        return None, "RUNPOD_POD_ID 的格式看不懂，不回報"
    if not target_ok(env["LIVE_LINK_TARGET"]):
        return None, "LIVE_LINK_TARGET 要是 https:// 開頭的網址，不回報"
    return {
        "secret": env["LIVE_LINK_SECRET"],
        "url": f"https://{env['RUNPOD_POD_ID']}-8765.proxy.runpod.net",
        "register": env["LIVE_LINK_TARGET"].rstrip("/") + REGISTER_PATH,
    }, ""


def sign(secret, url, ts):
    return hmac.new(secret.encode("utf-8"), f"{url}\n{ts}".encode("utf-8"), hashlib.sha256).hexdigest()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # 被轉到別的地方就當作失敗（不把簽名送到別處）


_OPENER = urllib.request.build_opener(_NoRedirect())


def register(cfg, now=None, timeout=20):
    """送一次；回傳 (成功嗎, 一行說明)。網路出錯不丟例外。"""
    ts = int(time.time() if now is None else now)
    body = json.dumps({"url": cfg["url"], "ts": ts, "sig": sign(cfg["secret"], cfg["url"], ts)}).encode("utf-8")
    req = urllib.request.Request(cfg["register"], data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json", "User-Agent": UA})
    where = urlparse(cfg["register"]).netloc
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            r.read(4096)
            return True, f"已回報 {cfg['url']} → {where}（HTTP {r.status}）"
    except urllib.error.HTTPError as e:
        hint = "：LIVE_LINK_SECRET 和網站上的不一樣，或這台的時間不準" if e.code == 403 else ""
        return False, f"{where} 不收（HTTP {e.code}）{hint}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = getattr(e, "reason", None)
        kind = type(reason if isinstance(reason, BaseException) else e).__name__
        return False, f"連不到 {where}（{kind}）"


def main(argv):
    once = "--once" in argv
    said = ""
    while True:
        try:
            cfg, why = config()
            if cfg is None:
                if why != said:  # 沒設定：說一次就好，之後安靜地等
                    log(why)
                    said = why
                if once:
                    return 0
            else:
                said = ""
                ok, text = register(cfg)
                log(text)
                if once:
                    return 0 if ok else 1
        except Exception as e:  # 不能因為一次意外就停掉（start_all.sh 也會重開，但不用等）
            log(f"出錯了（{type(e).__name__}），等一下再試")
            if once:
                return 1
        time.sleep(EVERY)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(0)
