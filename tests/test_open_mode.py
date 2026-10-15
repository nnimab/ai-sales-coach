"""雲端不用登入（AUTH_MODE=open）和審查修正：
1. 雲端 app.py（子程序、沒有 .env 的暫存複本、假的 Gemini）：沒有 cookie 也能用、每個瀏覽器一個匿名代號、
   沒設 ADMIN_PASSCODE 人人都能改設定、設了就要到 /login 輸入；程式和情境檔照樣拿不到；同步密碼照樣要對。
2. 探測程式（子程序裡 import server）：各種 AUTH_MODE 的規則、設定值的前後空白、資料庫連不上時登入次數照樣會擋。
全部只連 127.0.0.1，不會用到任何真的金鑰。"""

import http.client
import json
import subprocess
import sys
import unittest

import _util
import fake_gemini

APP_SECRET = "app-secret-" + "o" * 40
SYNC_KEY = "sync-key-open-0123456789abcdef"
IGNORED = "ignored-pass-1234"  # AUTH_MODE=open 時設了也不看
ADMIN = "admin-pass-9876"
LEAKS = ("/scenarios/01-dca-volatility.json", "/server.py", "/app.py", "/.env", "/storage.py", "/web/../server.py")


class _Cloud(unittest.TestCase):
    """開一個雲端 app.py（AUTH_MODE=open）；extra 是各自多加的環境變數。"""

    extra = {}

    @classmethod
    def setUpClass(cls):
        cls.gm = fake_gemini.start()
        cls.dir = _util.copy_project()
        cls.port = _util.free_port()
        env = _util.clean_env(dict({
            "APP_MODE": "cloud", "AUTH_MODE": "open", "APP_SECRET": APP_SECRET, "ALLOWED_HOSTS": "127.0.0.1",
            "LLM_PROVIDER": "gemini", "LIVE_ENABLED": "0", "BRAIN_BACKUPS": "gemini",
            "GEMINI_API_KEY": "fake-gemini-key", "GEMINI_OPENAI_URL": cls.gm.url + "/v1beta/openai",
            "GEMINI_BASE_URL": cls.gm.url + "/v1beta",
        }, **cls.extra))
        cls.proc = _util.start_proc(["app.py", "--port", str(cls.port)], cls.dir, env, cls.dir / "cloud.log")
        if not _util.wait_port(cls.port, 25):
            cls.tearDownClass()
            raise AssertionError("app.py 沒有啟動：" + (cls.dir / "cloud.log").read_text("utf-8", "replace")[-800:])

    @classmethod
    def tearDownClass(cls):
        _util.stop_proc(getattr(cls, "proc", None))
        cls.gm.stop()

    def req(self, method, path, body=None, cookie="", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h = {"Host": f"127.0.0.1:{self.port}"}
        if cookie:
            h["Cookie"] = cookie
        if body is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        conn.request(method, path, body=None if body is None else json.dumps(body).encode("utf-8"), headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        try:
            js = json.loads(data.decode("utf-8"))
        except ValueError:
            js = None
        return r.status, js, data, r.getheader("Set-Cookie") or ""

    def anon(self):
        """沒有 cookie 打開網站：直接能用，拿到一個匿名 cookie。"""
        st, js, data, set_cookie = self.req("GET", "/api/status")
        self.assertEqual(st, 200, data[:300])
        self.assertTrue(set_cookie.startswith("aic_session="), set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        return js, set_cookie.split(";", 1)[0]

    def assert_no_leaks(self, cookie=""):
        for path in LEAKS:
            st, _, data, _ = self.req("GET", path, cookie=cookie)
            self.assertEqual(st, 404, path)
            self.assertNotIn(b"server_only", data)


class OpenModeTests(_Cloud):
    """AUTH_MODE=open、沒設 ADMIN_PASSCODE：就算 APP_PASSCODE 還留著，也不用登入、人人都能改設定。
    同步密碼故意帶前後空白（Vercel 上貼的值多一個換行）：同步照樣要對得上。"""

    extra = {"APP_PASSCODE": IGNORED, "CHATGPT_SYNC_KEY": " " + SYNC_KEY + " \n"}

    def test_no_login_needed(self):
        st, cookie = self.anon()
        self.assertTrue(st["can_admin"])
        self.assertEqual((st["auth"]["required"], st["auth"]["admin_login"]), (False, False))
        for p in st["setup_problems"]:
            self.assertNotIn("PASSCODE", p)
            self.assertNotIn("通行碼", p)
        # 帶著 cookie 再來：同一個代號，不會每次都發新的
        st2, _, _, again = self.req("GET", "/api/status", cookie=cookie)
        self.assertEqual((st2, again), (200, ""))
        st2, js, _, _ = self.req("GET", "/api/session", cookie=cookie)
        self.assertEqual((js["auth_required"], js["logged_in"], js["setup"], js["admin_login"], js["role"]),
                         (False, True, False, False, "user"))
        self.assertFalse(any("PASSCODE" in p for p in js["setup_problems"]))
        # 首頁直接打開，不會被轉到登入頁
        st2, _, data, _ = self.req("GET", "/", cookie=cookie)
        self.assertEqual(st2, 200)
        self.assertIn(b"<html", data.lower())

    def test_static_files_still_hidden(self):
        self.assert_no_leaks()
        _, cookie = self.anon()
        self.assert_no_leaks(cookie)

    def test_everyone_can_change_settings(self):
        _, cookie = self.anon()
        st, js, data, _ = self.req("POST", "/api/live/config", {"enabled": False}, cookie=cookie)
        self.assertEqual(st, 200, data[:300])
        # 別的網站還是不能叫瀏覽器偷偷改
        st, _, _, _ = self.req("POST", "/api/live/config", {"enabled": False}, cookie=cookie,
                               headers={"Origin": "http://evil.example"})
        self.assertEqual(st, 403)

    def test_app_passcode_is_ignored(self):
        st, js, _, _ = self.req("POST", "/api/login", {"passcode": IGNORED})
        self.assertEqual(st, 401)
        self.assertNotIn(IGNORED, (self.dir / "cloud.log").read_text("utf-8", "replace"))

    def test_sync_still_needs_key(self):
        st, js, _, _ = self.req("POST", "/api/chatgpt/sync/host", {}, headers={"X-Sync-Key": "wrong-key-0123456789"})
        self.assertEqual(st, 401)
        self.assertTrue(js.get("sync"))
        st, js, _, _ = self.req("POST", "/api/chatgpt/sync/host", {})
        self.assertEqual(st, 401)
        # 雲端的值有前後空白，比的是去掉空白後的：同一組密碼要對得上
        st, js, data, _ = self.req("POST", "/api/chatgpt/sync/host", {}, headers={"X-Sync-Key": SYNC_KEY})
        self.assertEqual(st, 200, data[:300])
        self.assertTrue(js["host_id"].startswith("urn:"))

    def test_chat_with_fake_gemini(self):
        _, cookie = self.anon()
        st, js, _, _ = self.req("GET", "/api/scenarios", cookie=cookie)
        self.assertEqual(st, 200)
        sid = js["scenarios"][0]["id"]
        chat = {"scenario_id": sid, "messages": [{"role": "rep", "text": "您好，我是理專。"}]}
        st, _, data, _ = self.req("POST", "/api/chat", chat, cookie=cookie)
        self.assertEqual(st, 200, data[:300])
        events = [json.loads(ln[5:]) for ln in data.decode("utf-8").splitlines() if ln.startswith("data:")]
        self.assertTrue("".join(e.get("delta", "") for e in events), events)
        self.assertFalse(any(e.get("error") for e in events), events)
        text = (self.dir / "cloud.log").read_text("utf-8", "replace")
        for secret in (APP_SECRET, SYNC_KEY):
            self.assertNotIn(secret, text)


class OpenAdminTests(_Cloud):
    """AUTH_MODE=open、有設 ADMIN_PASSCODE（故意帶空白）：練習不用登入，改設定要先到 /login 輸入管理者通行碼。"""

    extra = {"ADMIN_PASSCODE": ADMIN + "  \n"}

    def test_visitor_cannot_change_settings(self):
        st, cookie = self.anon()
        self.assertFalse(st["can_admin"])
        self.assertEqual((st["auth"]["required"], st["auth"]["admin_login"], st["auth"]["role"]), (False, True, "user"))
        st2, js, _, _ = self.req("GET", "/api/session", cookie=cookie)
        self.assertEqual((js["auth_required"], js["logged_in"], js["admin_login"], js["role"]), (False, True, True, "user"))
        st2, js, _, _ = self.req("POST", "/api/live/config", {"enabled": False}, cookie=cookie)
        self.assertEqual(st2, 403)
        self.assertTrue(js.get("admin"))
        # 練習照樣可以
        self.assertEqual(self.req("GET", "/api/scenarios", cookie=cookie)[0], 200)
        self.assert_no_leaks(cookie)

    def test_admin_login_and_logout(self):
        _, cookie = self.anon()
        self.assertEqual(self.req("POST", "/api/login", {"passcode": "wrong-one"}, cookie=cookie)[0], 401)
        st, js, _, set_cookie = self.req("POST", "/api/login", {"passcode": ADMIN, "name": "主管"}, cookie=cookie)
        self.assertEqual((st, js["role"]), (200, "admin"))
        admin = set_cookie.split(";", 1)[0]
        st, js, _, _ = self.req("GET", "/api/status", cookie=admin)
        self.assertTrue(js["can_admin"])
        self.assertEqual(js["auth"]["role"], "admin")
        self.assertEqual(self.req("POST", "/api/live/config", {"enabled": False}, cookie=admin)[0], 200)
        st, js, _, set_cookie = self.req("POST", "/api/logout", {}, cookie=admin)
        self.assertEqual(st, 200)
        self.assertIn("Max-Age=0", set_cookie)
        st, cookie = self.anon()  # 登出後又是一般訪客
        self.assertFalse(st["can_admin"])


PROBE = r'''
import io, json, os, sys
sys.path.insert(0, ".")
import app, server, storage

out = {"raw_passcode": os.environ.get("APP_PASSCODE"), "env_passcode": server.ENV.get("APP_PASSCODE"),
       "raw_sync": os.environ.get("CHATGPT_SYNC_KEY"), "env_sync": server.ENV.get("CHATGPT_SYNC_KEY"),
       "cfg_sync": server.CHATGPT_CFG.sync_key}
BASE = dict(server.ENV)
AUTH_KEYS = ("AUTH_MODE", "APP_PASSCODE", "ADMIN_PASSCODE", "APP_SECRET")


def rules(on_cloud=True, **env):
    server.ON_CLOUD = on_cloud
    server.ENV = {k: v for k, v in BASE.items() if k not in AUTH_KEYS}
    server.ENV.update(env)
    server.CTX.uid, server.CTX.role = "u1", "user"
    res = {"required": server.auth_required(), "sessions": server.sessions_on(), "admin_login": server.admin_login(),
           "problem": server.auth_setup_problem(), "setup": server.setup_problems(), "admin": server.is_admin(),
           "app_passcode": server.app_passcode()}
    server.ON_CLOUD, server.ENV = True, dict(BASE)
    return res


S = "s" * 40
out["open"] = rules(AUTH_MODE="open", APP_SECRET=S, APP_PASSCODE="still-here")
out["open_admin"] = rules(AUTH_MODE="Open ", APP_SECRET=S, ADMIN_PASSCODE="a-pass")
out["open_no_secret"] = rules(AUTH_MODE="open")
out["passcode"] = rules(AUTH_MODE="passcode", APP_SECRET=S, APP_PASSCODE="p-1234")
out["passcode_missing"] = rules(APP_SECRET=S)
out["vercel"] = rules(AUTH_MODE="vercel", APP_SECRET=S)
out["typo"] = rules(AUTH_MODE="opne", APP_SECRET=S)
out["local_open"] = rules(on_cloud=False, AUTH_MODE="open", APP_PASSCODE="p-1234")
out["local_passcode"] = rules(on_cloud=False, APP_PASSCODE="p-1234")


def call(method, path, body=None, ip="10.0.0.1", headers=None):
    data = b"" if body is None else json.dumps(body).encode("utf-8")
    env = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": "", "wsgi.input": io.BytesIO(data),
           "REMOTE_ADDR": ip, "HTTP_HOST": "127.0.0.1"}
    if data:
        env["CONTENT_LENGTH"], env["CONTENT_TYPE"] = str(len(data)), "application/json"
    for k, v in (headers or {}).items():
        env["HTTP_" + k.upper().replace("-", "_")] = v
    got = {}

    def start(status, hdrs):
        got["status"] = int(status.split()[0])

    b"".join(app.app(env, start))
    return got["status"]


# 設定值的前後空白：通行碼、同步密碼都比得上
out["login_ok"] = call("POST", "/api/login", {"passcode": "pass-1234"}, ip="10.0.0.2")
out["login_spaces"] = call("POST", "/api/login", {"passcode": " pass-1234 "}, ip="10.0.0.2")
out["sync_ok"] = call("POST", "/api/chatgpt/sync/host", {}, ip="10.0.0.3", headers={"X-Sync-Key": "sync-key-probe-0123456789"})


class Broken(storage.MemoryStore):
    """資料庫連不上：短期資料一律丟 StoreError。"""

    def eph_incr(self, key, ttl):
        raise storage.StoreError("down")

    def eph_get(self, key):
        raise storage.StoreError("down")


server.STORE = Broken()
out["login_down"] = [call("POST", "/api/login", {"passcode": "wrong"}, ip="10.0.0.4") for _ in range(11)]
out["login_down_right"] = call("POST", "/api/login", {"passcode": "pass-1234"}, ip="10.0.0.4")
out["login_down_other_ip"] = call("POST", "/api/login", {"passcode": "pass-1234"}, ip="10.0.0.5")
bad = {"X-Sync-Key": "wrong-key-0123456789abc"}
out["sync_down"] = [call("POST", "/api/chatgpt/sync/host", {}, ip="10.0.0.6", headers=bad) for _ in range(11)]
print(json.dumps(out, ensure_ascii=False))
'''


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = _util.copy_project()
        env = _util.clean_env({
            "APP_MODE": "cloud", "APP_SECRET": APP_SECRET, "ALLOWED_HOSTS": "127.0.0.1",
            "APP_PASSCODE": "pass-1234 \n", "CHATGPT_SYNC_KEY": "\tsync-key-probe-0123456789 \n",
        })
        r = subprocess.run([sys.executable, "-c", PROBE], cwd=str(cls.dir), capture_output=True, timeout=120, env=env)
        lines = [ln for ln in r.stdout.decode("utf-8", "replace").splitlines() if ln.startswith("{")]
        if r.returncode or not lines:
            raise AssertionError("探測程式失敗：" + r.stderr.decode("utf-8", "replace")[-1500:])
        cls.o = json.loads(lines[-1])

    def test_open_mode_rules(self):
        o = self.o
        self.assertEqual((o["open"]["required"], o["open"]["sessions"], o["open"]["admin"]), (False, True, True))
        self.assertEqual((o["open"]["problem"], o["open"]["app_passcode"], o["open"]["admin_login"]), ("", "", False))
        for case in ("open", "open_admin", "open_no_secret", "vercel"):
            self.assertFalse(any("PASSCODE" in p or "通行碼" in p for p in o[case]["setup"]), case)
        # 有 ADMIN_PASSCODE：一般訪客不是管理者，/login 只用來拿管理者權限（AUTH_MODE 大小寫、空白都可以）
        self.assertEqual((o["open_admin"]["required"], o["open_admin"]["admin"], o["open_admin"]["admin_login"]),
                         (False, False, True))
        # APP_SECRET 還是一定要
        self.assertIn("APP_SECRET", o["open_no_secret"]["problem"])
        self.assertTrue(any("APP_SECRET" in p for p in o["open_no_secret"]["setup"]))

    def test_other_modes_unchanged(self):
        o = self.o
        self.assertEqual((o["passcode"]["required"], o["passcode"]["problem"], o["passcode"]["admin"]), (True, "", True))
        self.assertIn("APP_PASSCODE", o["passcode_missing"]["problem"])
        self.assertTrue(any("APP_PASSCODE" in p for p in o["passcode_missing"]["setup"]))
        self.assertEqual((o["vercel"]["required"], o["vercel"]["sessions"], o["vercel"]["problem"]), (False, True, ""))
        self.assertIn("AUTH_MODE", o["typo"]["problem"])  # 打錯字：整個關起來，不會變成誰都能用
        # 本機：AUTH_MODE=open 不用登入、不發 cookie；只設通行碼的照舊要登入
        self.assertEqual((o["local_open"]["required"], o["local_open"]["sessions"], o["local_open"]["admin"]), (False, False, True))
        self.assertEqual((o["local_passcode"]["required"], o["local_passcode"]["sessions"]), (True, True))

    def test_env_values_are_trimmed(self):
        o = self.o
        self.assertNotEqual(o["raw_passcode"], "pass-1234")  # 前提：環境變數真的帶著空白
        self.assertEqual(o["env_passcode"], "pass-1234")
        self.assertNotEqual(o["raw_sync"], "sync-key-probe-0123456789")
        self.assertEqual(o["env_sync"], o["cfg_sync"])
        self.assertEqual(o["cfg_sync"], "sync-key-probe-0123456789")
        self.assertEqual((o["login_ok"], o["login_spaces"], o["sync_ok"]), (200, 200, 200))

    def test_rate_limits_hold_when_store_is_down(self):
        o = self.o
        # 資料庫連不上：改在這台機器的記憶體數，第 11 次就擋（以前會變成不限次數）
        self.assertEqual(o["login_down"], [401] * 10 + [429])
        self.assertEqual(o["login_down_right"], 429)
        self.assertEqual(o["login_down_other_ip"], 200)
        self.assertEqual(o["sync_down"], [401] * 10 + [429])


if __name__ == "__main__":
    unittest.main()
