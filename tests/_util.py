"""測試共用：路徑、假服務的環境變數、跟著 OpenAI 授權轉址走、複製專案到暫存資料夾（不帶 .env 和個人資料）。"""

import atexit
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAKES = ROOT / "tools" / "fakes"
for p in (str(ROOT), str(FAKES)):
    if p not in sys.path:
        sys.path.insert(0, p)

# 測試建的暫存資料夾都放在這一個底下，跑完整個刪掉（不然每跑一次就在系統暫存區留下幾百個）
_RUN_TMP = tempfile.mkdtemp(prefix="aicoach-tests-")
tempfile.tempdir = _RUN_TMP
atexit.register(shutil.rmtree, _RUN_TMP, True)

# 複製專案時要帶的東西；.env、.chatgpt、voices.json、profile.json、tts_cache、scenarios/custom 一律不帶
COPY_FILES = ("server.py", "tts.py", "storage.py", "chatgpt_brain.py", "websession.py", "app.py", "liveavatar.py")
COPY_DIRS = ("web", "tools")


def fake_openai_env(url):
    """ChatGPT 相關網址全部指到假的 OpenAI；沒有一個會連到真的服務。"""
    return {
        "CHATGPT_DISCOVERY_URL": url + "/.well-known/openid-configuration",
        "CHATGPT_AUTHORIZE_URL": url + "/api/accounts/authorize",
        "CHATGPT_TOKEN_URL": url + "/api/accounts/oauth/token",
        "CHATGPT_REVOKE_URL": url + "/api/accounts/oauth/revoke",
        "CHATGPT_JWKS_URL": url + "/.well-known/jwks.json",
        "CHATGPT_ISSUER": url,
        "CHATGPT_API_BASE": url + "/v1",
    }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def no_redirect_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def location(url, allowed_prefix):
    """GET 網址（不跟轉址），回傳 Location。網址一定要在 allowed_prefix 底下，確保不會連到真的 OpenAI。"""
    if not url.startswith(allowed_prefix):
        raise AssertionError("不該連到這個網址：" + url.split("?", 1)[0])
    try:
        with no_redirect_opener().open(url, timeout=10) as r:
            raise AssertionError(f"沒有轉址（HTTP {r.status}）")
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307):
            raise AssertionError(f"授權失敗 HTTP {e.code}：{e.read()[:200]!r}") from None
        return e.headers["Location"]


def query_of(url):
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


def free_port(exclude=()):
    import common
    return common.free_port(exclude)


def wait_port(port, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
            return True
        except OSError:
            time.sleep(0.1)
    return False


def copy_project(dest=None):
    """把程式（不含 .env 和任何個人資料）複製到新的暫存資料夾，回傳路徑。"""
    dest = Path(dest or tempfile.mkdtemp(prefix="aicoach-test-"))
    dest.mkdir(parents=True, exist_ok=True)
    for name in COPY_FILES:
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, dest / name)
    for name in COPY_DIRS:
        if (ROOT / name).is_dir():
            shutil.copytree(ROOT / name, dest / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (dest / "scenarios").mkdir(exist_ok=True)
    for f in (ROOT / "scenarios").glob("*.json"):
        shutil.copy2(f, dest / "scenarios" / f.name)
    assert not (dest / ".env").exists()
    return dest


def clean_env(extra=None):
    """子程序用的環境變數：拿掉所有可能帶真實金鑰或設定的變數。"""
    drop = ("LLM_", "OPENAI_", "AZURE_OPENAI_", "ELEVENLABS_", "GEMINI_", "YATING_", "TTS_", "CHATGPT_", "APP_",
            "AUTH_", "ADMIN_", "PROFILE_", "BRAIN_", "LIVE_", "STORE_", "KV_", "UPSTASH_", "OLLAMA_", "VERCEL",
            "SESSION_", "ALLOWED_HOSTS", "CLOUD_URL", "PORT", "LIVEAVATAR_")
    env = {k: v for k, v in os.environ.items() if not k.startswith(drop)}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env.update(extra or {})
    return env


def start_proc(args, cwd, env, log_path):
    log = open(log_path, "wb")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    p = subprocess.Popen([sys.executable] + list(args), cwd=str(cwd), env=env, stdout=log, stderr=subprocess.STDOUT,
                         creationflags=flags)
    p._log = log
    return p


def stop_proc(p):
    if p is None:
        return
    try:
        p.terminate()
        p.wait(timeout=10)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass
    try:
        p._log.close()
    except Exception:
        pass
