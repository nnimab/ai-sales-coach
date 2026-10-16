"""真人版（RunPod）的固定入口：/live 和 /api/live/register，加上 Pod 那邊的 tools/runpod/live_link.py。
1. 存資料：live_link 這份文件在檔案、記憶體、Upstash（假的）都存得進去、讀得回來
2. live_link.py 本身：補環境變數、設定檢查、簽名
3. 探測程式（子程序裡 import app、server，直接呼叫 WSGI）：各種 AUTH_MODE 都不用登入、不發 cookie；
   簽名對了才收、15 分鐘沒回報就顯示「沒開」；簽名錯、時間太舊、沒設密碼、網址不對一律 403
4. 真的 app.py 子程序（預設的通行碼模式、AUTH_MODE=open）＋ live_link.py --once 從頭到尾跑一次
全部只連 127.0.0.1，不會用到任何真的金鑰，也不會連到 Vercel 或 RunPod。"""

import hashlib
import hmac
import http.client
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import _util
import fake_upstash
import storage

SECRET = "live-link-secret-" + "k" * 40
POD_ID = "abcd1234efgh"
POD_URL = f"https://{POD_ID}-8765.proxy.runpod.net"
APP_SECRET = "app-secret-" + "l" * 40
PASSCODE = "pass-live-1234"


def sign(url, ts, secret=SECRET):
    return hmac.new(secret.encode(), f"{url}\n{ts}".encode(), hashlib.sha256).hexdigest()


def load_live_link():
    spec = importlib.util.spec_from_file_location("live_link", _util.ROOT / "tools" / "runpod" / "live_link.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- 1. 存資料


class StoreDocTests(unittest.TestCase):
    DOC = {"url": POD_URL + "/", "ts": 1_800_000_000}

    def test_file_store(self):
        root = Path(tempfile.mkdtemp())
        for st in (storage.FileStore(root), storage.FileStore(root / "data", per_user=True)):
            self.assertIsNone(st.get_doc("live_link"))
            st.put_doc("live_link", self.DOC)
            self.assertEqual(st.get_doc("live_link"), self.DOC)
            self.assertEqual(json.loads((st.root / "live_link.json").read_text("utf-8")), self.DOC)

    def test_memory_store(self):
        st = storage.MemoryStore()
        self.assertIsNone(st.get_doc("live_link"))
        st.put_doc("live_link", self.DOC)
        self.assertEqual(st.get_doc("live_link"), self.DOC)

    def test_redis_store(self):
        srv = fake_upstash.start(token="tok-live")
        try:
            st = storage.RedisStore(srv.url, "tok-live", prefix="t:")
            self.assertIsNone(st.get_doc("live_link"))
            st.put_doc("live_link", self.DOC)
            self.assertEqual(json.loads(srv.state.data["t:doc:live_link"]), self.DOC)
            fresh = storage.RedisStore(srv.url, "tok-live", prefix="t:")  # 另一個執行個體（沒有快取）也讀得到
            self.assertEqual(fresh.get_doc("live_link"), self.DOC)
        finally:
            srv.stop()


# ---------------------------------------------------------------- 2. live_link.py


class PodScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ll = load_live_link()

    def test_env_from_pid1(self):
        f = Path(tempfile.mkdtemp()) / "environ"
        f.write_bytes(b"PATH=/usr/bin\0LIVE_LINK_SECRET=from-pid1\0RUNPOD_POD_ID=" + POD_ID.encode()
                      + b"\0LIVE_LINK_TARGET=https://pid1.example\0junk\0")
        # 這個程序已經有的不蓋掉；沒有的才從 1 號程序補
        env = self.ll.pod_env({"LIVE_LINK_TARGET": "https://mine.example", "LIVE_LINK_SECRET": "  "}, proc=str(f))
        self.assertEqual(env, {"LIVE_LINK_SECRET": "from-pid1", "LIVE_LINK_TARGET": "https://mine.example",
                               "RUNPOD_POD_ID": POD_ID})
        # 沒有 /proc/1/environ（例如 Windows）也不會出錯
        self.assertEqual(self.ll.pod_env({}, proc=str(f) + ".missing")["RUNPOD_POD_ID"], "")

    def test_config_checks(self):
        nowhere = str(Path(tempfile.mkdtemp()) / "none")
        good = {"LIVE_LINK_SECRET": SECRET, "LIVE_LINK_TARGET": "https://ai-coach.example/", "RUNPOD_POD_ID": POD_ID}
        cfg, why = self.ll.config(good, nowhere)
        self.assertEqual(why, "")
        self.assertEqual((cfg["url"], cfg["register"]), (POD_URL, "https://ai-coach.example/api/live/register"))
        for k in good:  # 少一個就不回報，原因不含密碼
            cfg, why = self.ll.config(dict(good, **{k: ""}), nowhere)
            self.assertIsNone(cfg)
            self.assertIn(k, why)
            self.assertNotIn(SECRET, why)
        bad = (("RUNPOD_POD_ID", "abc"), ("RUNPOD_POD_ID", "ABCD1234EFGH"), ("RUNPOD_POD_ID", "abcd1234/../x"),
               ("LIVE_LINK_TARGET", "http://ai-coach.example"), ("LIVE_LINK_TARGET", "ftp://ai-coach.example"),
               ("LIVE_LINK_TARGET", "https://user:pw@ai-coach.example"), ("LIVE_LINK_TARGET", "https://x.example/?a=1"))
        for k, v in bad:
            self.assertIsNone(self.ll.config(dict(good, **{k: v}), nowhere)[0], (k, v))
        # http 只給本機測試
        cfg, _ = self.ll.config(dict(good, LIVE_LINK_TARGET="http://127.0.0.1:9999"), nowhere)
        self.assertEqual(cfg["register"], "http://127.0.0.1:9999/api/live/register")

    def test_signature_and_user_agent(self):
        self.assertEqual(self.ll.sign(SECRET, POD_URL, 1_800_000_000), sign(POD_URL, 1_800_000_000))
        self.assertNotIn("python", self.ll.UA.lower())
        self.assertTrue(self.ll.UA.startswith("Mozilla/5.0"))

    def test_network_error_does_not_raise(self):
        cfg = {"secret": SECRET, "url": POD_URL, "register": f"http://127.0.0.1:{_util.free_port()}/api/live/register"}
        ok, text = self.ll.register(cfg, timeout=2)
        self.assertFalse(ok)
        self.assertIn("連不到", text)
        self.assertNotIn(SECRET, text)


# ---------------------------------------------------------------- 3. 探測程式（各種模式）


PROBE = r'''
import hashlib, hmac, io, json, sys, tempfile, time
sys.path.insert(0, ".")
import app, server, storage

SECRET, UPSTASH = sys.argv[1], sys.argv[2]
GOOD = "https://abcd1234efgh-8765.proxy.runpod.net"
BASE = dict(server.ENV)
DROP = ("AUTH_MODE", "APP_PASSCODE", "ADMIN_PASSCODE", "APP_SECRET", "LIVE_LINK_SECRET")
S = "s" * 40
MODES = {
    # 名稱: (雲端？, 環境變數, 存資料的地方)
    "passcode": (True, {"APP_SECRET": S, "APP_PASSCODE": "pass-1234"}, lambda: storage.MemoryStore()),
    "open": (True, {"AUTH_MODE": "open", "APP_SECRET": S},
             lambda: storage.RedisStore(UPSTASH, "tok-probe", prefix="p%d:" % time.monotonic_ns())),
    "vercel": (True, {"AUTH_MODE": "vercel", "APP_SECRET": S}, lambda: storage.MemoryStore()),
    "cloud_not_set_up": (True, {}, lambda: storage.MemoryStore()),  # 沒設 APP_SECRET：別的網址都關著
    "server": (True, {"AUTH_MODE": "open", "APP_SECRET": S},
               lambda: storage.FileStore(tempfile.mkdtemp(), per_user=True)),
    "local": (False, {}, lambda: storage.FileStore(tempfile.mkdtemp())),
}
SIGS = []


def sig(url, ts, secret=SECRET):
    s = hmac.new(secret.encode(), f"{url}\n{ts}".encode(), hashlib.sha256).hexdigest()
    SIGS.append(s)
    return s


def use(mode, secret=SECRET):
    on_cloud, env, store = MODES[mode]
    server.ON_CLOUD = on_cloud
    server.ENV = {k: v for k, v in BASE.items() if k not in DROP}
    server.ENV.update(env)
    set_secret(secret)
    server.STORE = store()


def set_secret(secret):
    server.ENV.pop("LIVE_LINK_SECRET", None)
    if secret is not None:
        server.ENV["LIVE_LINK_SECRET"] = secret


def call(method, path, body=None, raw=None, headers=None, ctype="application/json"):
    data = raw if raw is not None else (b"" if body is None else json.dumps(body).encode("utf-8"))
    env = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": "", "wsgi.input": io.BytesIO(data),
           "REMOTE_ADDR": "10.0.0.9", "HTTP_HOST": "127.0.0.1"}
    if data:
        env["CONTENT_LENGTH"], env["CONTENT_TYPE"] = str(len(data)), ctype
    for k, v in (headers or {}).items():
        env["HTTP_" + k.upper().replace("-", "_")] = v
    got = {}

    def start(status, hdrs):
        got["status"], got["headers"] = int(status.split()[0]), hdrs

    out = b"".join(app.app(env, start))
    h = {}
    for k, v in got["headers"]:
        h.setdefault(k.lower(), []).append(v)
    return {"status": got["status"], "location": (h.get("location") or [""])[0],
            "cache": (h.get("cache-control") or [""])[0], "cookie": bool(h.get("set-cookie")),
            "ctype": (h.get("content-type") or [""])[0], "body": out.decode("utf-8", "replace")}


def reg(url=GOOD, ts=None, s=None, secret=SECRET, **headers):
    ts = int(time.time()) if ts is None else ts
    body = {"url": url, "ts": ts, "sig": sig(url, ts, secret) if s is None else s}
    return call("POST", "/api/live/register", body, headers=headers)


out = {}
for mode in MODES:
    use(mode)
    r = {"off": call("GET", "/live")}
    now = int(time.time())
    # 從別的網站、用 text/plain 送也一樣：這條不看來源，只看簽名
    r["reg"] = call("POST", "/api/live/register", {"url": GOOD, "ts": now, "sig": sig(GOOD, now)},
                    headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}, ctype="text/plain")
    r["doc"] = server.STORE.get_doc("live_link")
    r["on"] = call("GET", "/live")
    server.STORE.put_doc("live_link", {"url": GOOD + "/", "ts": now - 901})
    r["stale"] = call("GET", "/live")
    server.STORE.put_doc("live_link", {"url": GOOD + "/", "ts": now - 880})
    r["fresh"] = call("GET", "/live")
    # 存的網址被改成別的（不該發生）：照樣不轉
    server.STORE.put_doc("live_link", {"url": "https://evil.example/", "ts": now})
    r["tampered"] = call("GET", "/live")
    r["status_api"] = call("GET", "/api/status")["status"]
    r["config_cross_site"] = call("POST", "/api/live/config", {"enabled": False},
                                  headers={"Origin": "https://evil.example"})["status"]
    out[mode] = r

# ---- 拒絕（用最嚴的預設通行碼模式）
use("passcode")
now = int(time.time())
rej = {
    "bad_sig": reg(s="0" * 64),
    "other_secret": reg(secret="another-secret-value-123"),
    "old_ts": reg(ts=now - 301),
    "future_ts": reg(ts=now + 301),
    "huge_ts": reg(ts=10 ** 30),
    "sig_short": reg(s="ab" * 10),
    "sig_not_hex": reg(s="zz" * 32),
    "sig_upper_wrong": reg(s=("0" * 64).upper()),
}
odd = {
    "ts_str": {"url": GOOD, "ts": str(now), "sig": sig(GOOD, now)},
    "ts_float": {"url": GOOD, "ts": now + 0.5, "sig": sig(GOOD, now + 0.5)},
    "ts_bool": {"url": GOOD, "ts": True, "sig": sig(GOOD, True)},
    "sig_missing": {"url": GOOD, "ts": now},
    "sig_number": {"url": GOOD, "ts": now, "sig": 12345},
    "url_missing": {"ts": now, "sig": sig("None", now)},
    "url_list": {"url": [GOOD], "ts": now, "sig": sig(str([GOOD]), now)},
}
for k, body in odd.items():
    rej[k] = call("POST", "/api/live/register", body)
BAD_URLS = {
    "other_host": "https://evil.example",
    "suffix_host": "https://abcd1234efgh-8765.proxy.runpod.net.evil.example",
    "prefix_host": "https://evil.abcd1234efgh-8765.proxy.runpod.net",
    "path": GOOD + "/admin",
    "path2": GOOD + "//evil.example",
    "query": GOOD + "/?next=https://evil.example",
    "query2": GOOD + "?x=1",
    "fragment": GOOD + "#x",
    "port_8010": "https://abcd1234efgh-8010.proxy.runpod.net",
    "explicit_port": "https://abcd1234efgh-8765.proxy.runpod.net:443",
    "http": "http://abcd1234efgh-8765.proxy.runpod.net",
    "newline": GOOD + "\n",
    "space": GOOD + " ",
    "upper": "https://ABCD1234EFGH-8765.proxy.runpod.net",
    "userinfo": "https://evil.example@abcd1234efgh-8765.proxy.runpod.net",
    "short_id": "https://abc-8765.proxy.runpod.net",
    "long_id": "https://" + "a" * 25 + "-8765.proxy.runpod.net",
    "two_slashes": GOOD + "//",
}
for k, u in BAD_URLS.items():
    rej["url_" + k] = reg(url=u)  # 簽名是對的，網址不對照樣不收
rej["too_big"] = call("POST", "/api/live/register", raw=json.dumps({"url": GOOD, "pad": "x" * 3000}).encode())
rej["not_json"] = call("POST", "/api/live/register", raw=b"url=" + GOOD.encode())
rej["array"] = call("POST", "/api/live/register", raw=b"[1, 2]")
set_secret(None)  # 網站沒設密碼：簽得再對也不收
rej["no_secret"] = reg()
set_secret("  \n")
rej["blank_secret"] = reg()
set_secret(SECRET)
out["rej"] = {k: v["status"] for k, v in rej.items()}
out["rej_bodies"] = sorted({v["body"] for v in rej.values() if v["status"] == 403})
out["after_rej"] = call("GET", "/live")
out["doc_after_rej"] = server.STORE.get_doc("live_link")

# ---- 重送舊的回報：不蓋掉比較新的；結尾的 / 可有可無
now = int(time.time())
NEW = "https://zyxw9876vuts-8765.proxy.runpod.net/"
out["newer"] = reg(url=NEW, ts=now)["status"]
out["replay_older"] = reg(url=GOOD, ts=now - 30)["status"]
out["doc_after_replay"] = server.STORE.get_doc("live_link")
out["after_replay"] = call("GET", "/live")["location"]
out["sigs"] = SIGS
print(json.dumps(out, ensure_ascii=False))
'''


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.up = fake_upstash.start(token="tok-probe")
        cls.dir = _util.copy_project()
        env = _util.clean_env({"APP_MODE": "cloud", "ALLOWED_HOSTS": "127.0.0.1"})
        try:
            r = subprocess.run([sys.executable, "-c", PROBE, SECRET, cls.up.url], cwd=str(cls.dir),
                               capture_output=True, timeout=180, env=env)
        finally:
            cls.up.stop()
        cls.stderr = r.stderr.decode("utf-8", "replace")
        lines = [ln for ln in r.stdout.decode("utf-8", "replace").splitlines() if ln.startswith("{")]
        if r.returncode or not lines:
            raise AssertionError("探測程式失敗：" + cls.stderr[-1500:])
        cls.o = json.loads(lines[-1])

    def assert_off_page(self, r, mode):
        self.assertEqual(r["status"], 200, (mode, r["body"][:200]))
        self.assertTrue(r["ctype"].startswith("text/html"), mode)
        self.assertIn("真人版目前沒有開", r["body"])
        self.assertIn('href="/"', r["body"])
        self.assertEqual(r["cache"], "no-store", mode)
        self.assertFalse(r["cookie"], mode)  # 不發 cookie

    def assert_redirect(self, r, mode, url=POD_URL + "/"):
        self.assertEqual((r["status"], r["location"]), (302, url), (mode, r["body"][:200]))
        self.assertEqual(r["cache"], "no-store", mode)
        self.assertFalse(r["cookie"], mode)

    def test_every_mode_without_login(self):
        for mode in ("passcode", "open", "vercel", "cloud_not_set_up", "server", "local"):
            r = self.o[mode]
            self.assert_off_page(r["off"], mode)  # 還沒有 Pod 回報
            self.assertEqual(r["reg"]["status"], 200, (mode, r["reg"]["body"]))
            self.assertFalse(r["reg"]["cookie"], mode)
            self.assertEqual(r["doc"], {"url": POD_URL + "/", "ts": r["doc"]["ts"]}, mode)
            self.assertLessEqual(abs(time.time() - r["doc"]["ts"]), 120)
            self.assert_redirect(r["on"], mode)
            self.assert_off_page(r["stale"], mode)  # 超過 15 分鐘沒回報
            self.assert_redirect(r["fresh"], mode)  # 14 分多：還算開著
            self.assert_off_page(r["tampered"], mode)

    def test_other_routes_unchanged(self):
        o = self.o
        self.assertEqual(o["passcode"]["status_api"], 401)  # 別的 API 照樣要登入
        self.assertEqual(o["cloud_not_set_up"]["status_api"], 503)  # 沒設定好的雲端照樣關著
        for mode in ("open", "vercel", "local", "server"):
            self.assertEqual(o[mode]["status_api"], 200, mode)
        # 別的網站叫瀏覽器 POST 改設定：照樣擋
        for mode in ("open", "local", "server"):
            self.assertEqual(o[mode]["config_cross_site"], 403, mode)

    def test_rejections(self):
        rej = self.o["rej"]
        self.assertEqual(rej.pop("too_big"), 413)
        self.assertEqual(rej.pop("not_json"), 400)
        self.assertEqual(rej.pop("array"), 400)
        for name, st in rej.items():
            self.assertEqual(st, 403, name)
        for k in ("bad_sig", "other_secret", "old_ts", "future_ts", "no_secret", "blank_secret", "url_other_host",
                  "url_path", "url_query", "url_port_8010", "url_http", "url_newline", "url_userinfo"):
            self.assertIn(k, rej)
        # 不說原因
        self.assertEqual([json.loads(b) for b in self.o["rej_bodies"]], [{"error": "forbidden"}])
        # 被拒絕的一個都沒存
        self.assertIsNone(self.o["doc_after_rej"])
        self.assertEqual(self.o["after_rej"]["status"], 200)

    def test_replay_and_trailing_slash(self):
        o = self.o
        self.assertEqual((o["newer"], o["replay_older"]), (200, 200))
        self.assertEqual(o["doc_after_replay"]["url"], "https://zyxw9876vuts-8765.proxy.runpod.net/")
        self.assertEqual(o["after_replay"], "https://zyxw9876vuts-8765.proxy.runpod.net/")

    def test_no_secret_in_logs(self):
        self.assertNotIn(SECRET, self.stderr)
        for s in self.o["sigs"]:
            self.assertNotIn(s, self.stderr)
        self.assertIn("固定入口改轉到", self.stderr)  # 換網址時記一行（只有網址）


# ---------------------------------------------------------------- 4. 真的 app.py ＋ live_link.py


class _Cloud:
    """開一個雲端 app.py（extra 是各自多加的環境變數），用 live_link.py --once 當作 Pod 來回報。"""

    extra = {}

    @classmethod
    def setUpClass(cls):
        cls.dir = _util.copy_project()
        cls.port = _util.free_port()
        env = _util.clean_env(dict({"APP_MODE": "cloud", "APP_SECRET": APP_SECRET, "ALLOWED_HOSTS": "127.0.0.1",
                                    "LIVE_LINK_SECRET": " " + SECRET + "\n"}, **cls.extra))
        cls.proc = _util.start_proc(["app.py", "--port", str(cls.port)], cls.dir, env, cls.dir / "cloud.log")
        if not _util.wait_port(cls.port, 25):
            cls.tearDownClass()
            raise AssertionError("app.py 沒有啟動：" + (cls.dir / "cloud.log").read_text("utf-8", "replace")[-800:])

    @classmethod
    def tearDownClass(cls):
        _util.stop_proc(getattr(cls, "proc", None))

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request("GET", path, headers={"Host": f"127.0.0.1:{self.port}"})
        r = conn.getresponse()
        body = r.read().decode("utf-8", "replace")
        conn.close()
        return r.status, r.getheader("Location") or "", r.getheader("Cache-Control") or "", r.getheader("Set-Cookie"), body

    def pod(self, **env):
        """在子程序跑一次 live_link.py --once（像 Pod 上那樣），回傳 (結束代碼, 印出來的)。"""
        base = {"LIVE_LINK_SECRET": SECRET, "LIVE_LINK_TARGET": f"http://127.0.0.1:{self.port}", "RUNPOD_POD_ID": POD_ID}
        base.update(env)
        r = subprocess.run([sys.executable, str(self.dir / "tools" / "runpod" / "live_link.py"), "--once"],
                           cwd=str(self.dir), capture_output=True, timeout=60,
                           env=_util.clean_env({k: v for k, v in base.items() if v is not None}))
        text = (r.stdout + r.stderr).decode("utf-8", "replace")
        self.assertNotIn(SECRET, text)
        return r.returncode, text

    def test_pod_registers_and_live_redirects(self):
        st, loc, cache, cookie, body = self.get("/live")
        self.assertEqual((st, cache, cookie), (200, "no-store", None))
        self.assertIn("真人版目前沒有開", body)
        # 沒設定：安靜地結束，什麼都不送
        code, text = self.pod(LIVE_LINK_SECRET=None, LIVE_LINK_TARGET=None)
        self.assertEqual(code, 0)
        self.assertIn("沒有設定", text)
        self.assertEqual(len([ln for ln in text.splitlines() if ln.strip()]), 1)
        # 密碼不對：網站不收
        code, text = self.pod(LIVE_LINK_SECRET="wrong-secret-0123456789")
        self.assertEqual(code, 1)
        self.assertIn("HTTP 403", text)
        self.assertEqual(self.get("/live")[0], 200)
        # 對的：/live 轉到 Pod
        code, text = self.pod()
        self.assertEqual(code, 0, text)
        self.assertIn("已回報", text)
        self.assertEqual(len([ln for ln in text.splitlines() if ln.strip()]), 1)
        st, loc, cache, cookie, _ = self.get("/live")
        self.assertEqual((st, loc, cache, cookie), (302, POD_URL + "/", "no-store", None))
        log = (self.dir / "cloud.log").read_text("utf-8", "replace")
        self.assertNotIn(SECRET, log)
        self.assertNotIn(APP_SECRET, log)


class PasscodeModeTests(_Cloud, unittest.TestCase):
    """預設的 AUTH_MODE（通行碼）：/live 和回報都不用登入；其他網址照樣要。"""

    extra = {"APP_PASSCODE": PASSCODE}

    def test_rest_still_needs_login(self):
        st, loc, _, _, _ = self.get("/")
        self.assertEqual((st, loc), (302, "/login"))
        self.assertEqual(self.get("/api/status")[0], 401)


class OpenModeTests(_Cloud, unittest.TestCase):
    """AUTH_MODE=open（現在 Vercel 上的設定）。"""

    extra = {"AUTH_MODE": "open"}


if __name__ == "__main__":
    unittest.main()
