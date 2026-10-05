"""雲端同步（OpenAI 文件的 self-hosted VM 做法）：本機替雲端登入（用雲端的主機代號）→ 用同步密碼把憑證傳到雲端 →
雲端自己續期（Redis 鎖，換新的 refresh token 寫回去）→ 續期失敗就要重新同步、改用 Gemini。

第一部分只用 chatgpt_brain + storage（假的 OpenAI、假的 Upstash、測試裡的迷你雲端），不需要 server.py。
第二部分是完整的 HTTP 流程（雲端 app.py + 本機 server.py 兩個子程序、假的 Gemini），server.py 還沒有同步功能時會略過。"""

import http.client
import json
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler

import _util
import chatgpt_brain as cb
import common
import fake_gemini
import fake_openai
import fake_upstash
import websession
from chatgpt_brain import BrainError, ChatGPT, ChatGPTConfig
from storage import FileStore, MemoryStore, RedisStore

try:
    import cryptography  # noqa: F401
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False

SYNC_KEY = "sync-key-0123456789abcdef0123456789"
APP_SECRET = "app-secret-" + "z" * 40
TOKEN = "upstash-test-token"


class MiniCloud(BaseHTTPRequestHandler):
    """測試用的雲端：只做 /api/chatgpt/sync/host 和 /import（照修正計畫 B 的規則）。"""

    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def do_POST(self):
        st = self.server.state
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        brain = st["brain"]
        if not brain.cfg.sync_enabled or st.get("down"):
            return self.reply(404 if not st.get("down") else 500, {"error": "not found"})
        if not websession.passcode_ok(self.headers.get("X-Sync-Key") or "", brain.cfg.sync_key):
            return self.reply(401, {"error": "同步密碼不對", "sync": True})  # 和 server.py 一樣帶 sync
        if self.path == "/api/chatgpt/sync/host":
            return self.reply(200, {"host_id": brain.host_id(), "app": cb.APP_ID})
        if self.path == "/api/chatgpt/sync/import":
            res = brain.import_account(body)
            return self.reply(200 if res["ok"] else 400, res)
        self.reply(404, {"error": "not found"})

    def reply(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class SyncFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.oa = fake_openai.start()
        cls.up = fake_upstash.start(token=TOKEN)
        cls.cloud_state = {}
        cls.cloud_srv = common.serve(MiniCloud, state=cls.cloud_state)

    @classmethod
    def tearDownClass(cls):
        for s in (cls.oa, cls.up, cls.cloud_srv):
            s.stop()

    def setUp(self):
        self.oa.state.reset()
        self.up.state.c_flushall()
        env = _util.fake_openai_env(self.oa.url)
        self.local = ChatGPT(ChatGPTConfig.from_env(env, False), FileStore(tempfile.mkdtemp(prefix="local-")))
        self.cloud_env = dict(env, CHATGPT_SYNC_KEY=SYNC_KEY, APP_SECRET=APP_SECRET)
        self.cloud_store = self.new_cloud_store()
        self.cloud = self.new_cloud()
        self.cloud_state.clear()
        self.cloud_state["brain"] = self.cloud

    def new_cloud_store(self):
        if HAVE_CRYPTO:
            return RedisStore(self.up.url, TOKEN, prefix="cloud:", app_secret=APP_SECRET)
        return getattr(self, "cloud_store", None) or MemoryStore()  # 沒有 cryptography：用記憶體代替

    def new_cloud(self, store=None):
        return ChatGPT(ChatGPTConfig.from_env(self.cloud_env, True), store or self.cloud_store)

    def knobs(self, **kw):
        with self.oa.state.lock:
            self.oa.state.knobs.update(kw)
            self.oa.state.pre_error_used = 0

    def sync(self):
        host = self.local.cloud_host_id(self.cloud_srv.url, SYNC_KEY)
        url = self.local.begin_login("local", self.local.redirect_uri(9150), "", host_id=host, target="cloud")
        loc = _util.location(url, self.oa.url)
        res = self.local.finish_login("local", _util.query_of(loc))
        self.assertTrue(res["ok"] and res["target"] == "cloud", res)
        out = self.local.push_to_cloud(self.cloud_srv.url, SYNC_KEY)
        return host, url, out

    def stream(self, brain, uid="anyone"):
        return "".join(brain.stream(uid, "你是客戶", [{"role": "user", "content": "你好"}], model="gpt-6.1-sol"))

    def owner(self):
        return self.cloud_store.get_secret("chatgpt-owner")

    # ---- 測試

    def test_end_to_end(self):
        host, url, out = self.sync()
        self.assertEqual(host, self.cloud.host_id())
        self.assertNotEqual(host, self.local.host_id())
        self.assertEqual(_util.query_of(url)["ext_agent_host_id"], [host])  # 用雲端自己的主機代號登入
        self.assertEqual(out["email"], "rep@example.test")
        owner = self.owner()
        self.assertTrue(owner["refresh_token"] and owner["plan_usage"])
        self.assertEqual(owner["ext_agent_host_id"], host)
        if HAVE_CRYPTO:
            raw = self.up.state.run(["GET", "cloud:secret:chatgpt-owner"])
            self.assertNotIn(owner["refresh_token"], raw)  # Redis 裡是加密過的
        kept = self.local.store.get_secret("chatgpt-cloud-client")
        for k in ("access_token", "refresh_token", "id_token"):
            self.assertNotIn(k, kept)  # 本機不留雲端那份的 token
        self.assertTrue(kept["client_id"].startswith("oaiapp_"))
        self.assertGreater(self.local.cloud_sync_status()["last_sync_at"], 0)
        self.assertEqual(self.local.cloud_sync_status()["last_error"], "")
        self.assertIsNone(self.local.store.get_secret("chatgpt-account-local"))  # 本機自己的登入沒被動到
        # 雲端每個登入的人都用這一個帳號（單人使用）
        for uid in ("u1", "u2"):
            st = self.cloud.account_status(uid)
            self.assertTrue(st["signed_in"] and st["configured"], st)
            self.assertEqual(st["message"], "使用 ChatGPT 方案")
        self.assertEqual(self.stream(self.cloud, "u1"), fake_openai.DEFAULT_KNOBS["reply"])
        client = self.oa.state.clients[owner["client_id"]]
        self.assertIn(host, client["hosts"])

    def test_second_sync_reuses_registration(self):
        self.sync()
        client_id = self.local.store.get_secret("chatgpt-cloud-client")["client_id"]
        host, url, out = self.sync()
        q = {k: v[0] for k, v in _util.query_of(url).items()}
        self.assertEqual(q["client_id"], client_id)
        self.assertEqual(q["login_hint"], "rep@example.test")
        self.assertNotIn("agent_name_hint", q)
        self.assertEqual(self.owner()["client_id"], client_id)

    def test_sync_errors_have_clear_messages(self):
        with self.assertRaises(BrainError) as cm:
            self.local.cloud_host_id(self.cloud_srv.url, "wrong-key")
        self.assertIn("同步密碼不對", str(cm.exception))
        off = ChatGPT(ChatGPTConfig.from_env(_util.fake_openai_env(self.oa.url), True), MemoryStore())
        self.cloud_state["brain"] = off
        with self.assertRaises(BrainError) as cm:
            self.local.cloud_host_id(self.cloud_srv.url, SYNC_KEY)
        self.assertIn("CHATGPT_SYNC_KEY", str(cm.exception))
        self.cloud_state["brain"] = self.cloud
        dead = f"http://127.0.0.1:{_util.free_port()}"
        with self.assertRaises(BrainError) as cm:
            self.local.cloud_host_id(dead, SYNC_KEY)
        self.assertEqual(cm.exception.code, "cloud_unreachable")
        for bad in ("http://evil.com", "https://x.vercel.app/path", "https://user:pw@x.vercel.app", "ftp://x", "", "https://x.vercel.app?a=1"):
            with self.assertRaises(ValueError):
                cb.normalize_cloud_url(bad)
            with self.assertRaises(BrainError):
                self.local.cloud_host_id(bad, SYNC_KEY)
        self.assertEqual(cb.normalize_cloud_url("https://Demo.vercel.app/"), "https://demo.vercel.app")
        self.assertEqual(cb.normalize_cloud_url("http://127.0.0.1:9123"), "http://127.0.0.1:9123")

    def test_failed_push_strips_tokens(self):
        host = self.local.cloud_host_id(self.cloud_srv.url, SYNC_KEY)
        url = self.local.begin_login("local", self.local.redirect_uri(9150), "", host_id=host, target="cloud")
        self.local.finish_login("local", _util.query_of(_util.location(url, self.oa.url)))
        self.cloud_state["down"] = True
        with self.assertRaises(BrainError):
            self.local.push_to_cloud(self.cloud_srv.url, SYNC_KEY)
        kept = self.local.store.get_secret("chatgpt-cloud-client")
        self.assertNotIn("refresh_token", kept)
        self.assertTrue(self.local.cloud_sync_status()["last_error"])
        self.assertIsNone(self.owner())
        with self.assertRaises(BrainError) as cm:
            self.local.export_account()
        self.assertEqual(cm.exception.code, "not_signed_in")

    def test_import_validation(self):
        host = self.cloud.host_id()
        url = self.local.begin_login("local", self.local.redirect_uri(9150), "", host_id=host, target="cloud")
        self.local.finish_login("local", _util.query_of(_util.location(url, self.oa.url)))
        good = self.local.export_account()
        cases = {
            "missing_fields": dict(good, refresh_token=""),
            "no_plan_scope": dict(good, scope="openid profile email offline_access"),
            "bad_client": dict(good, client_id="dynamic_agent_client"),
            "host_mismatch": dict(good, ext_agent_host_id=self.local.host_id()),
            "bad_id_token": dict(good, client_id="oaiapp_other"),  # aud 對不上
        }
        forged = common.make_jwt({"iss": self.oa.url, "aud": good["client_id"], "sub": "x", "exp": 9999999999},
                                 __import__("testkeys").OTHER_N, __import__("testkeys").OTHER_D, "test-key-1")
        cases["bad_id_token_sig"] = dict(good, id_token=forged)
        for reason, payload in cases.items():
            res = self.cloud.import_account(payload)
            self.assertFalse(res["ok"], reason)
            self.assertEqual(res["reason"], reason.replace("_sig", ""), reason)
            self.assertTrue(res["error"])
            self.assertIsNone(self.owner(), reason)
        self.assertFalse(self.cloud.import_account("x")["ok"])
        self.assertTrue(self.cloud.import_account(good)["ok"])

    def test_cloud_refresh_rotates_once_under_lock(self):
        self.sync()
        rec = self.owner()
        old_refresh = rec["refresh_token"]
        rec["expires_at"] = 0
        self.cloud_store.put_secret("chatgpt-owner", rec)
        self.knobs(refresh_delay=0.3)
        # 兩個 Vercel 執行個體（各自有快取和記憶體）共用同一個 Redis
        instances = [self.new_cloud(self.new_cloud_store()), self.new_cloud(self.new_cloud_store())]
        results, errors = [], []

        def run(brain):
            try:
                results.append(self.stream(brain))
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        ts = [threading.Thread(target=run, args=(instances[i % 2],)) for i in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 6)
        refreshes = [c for c in self.oa.state.token_calls if c[0] == "refresh_token"]
        self.assertEqual(len(refreshes), 1, refreshes)
        new = self.owner()
        self.assertNotEqual(new["refresh_token"], old_refresh)  # 換新的 refresh token 已經寫回 Redis
        self.assertGreater(new["expires_at"], time.time() + 3000)
        self.assertEqual(self.oa.state.snapshot()["active_refresh"], 1)

    def test_cloud_refresh_failure_needs_resync(self):
        self.sync()
        rec = self.owner()
        rec["expires_at"] = 0
        self.cloud_store.put_secret("chatgpt-owner", rec)
        self.knobs(refresh_mode="invalidated")
        with self.assertRaises(BrainError) as cm:
            self.stream(self.cloud)
        self.assertEqual((cm.exception.code, cm.exception.fallback), ("needs_login", True))
        st = self.cloud.account_status("u1")
        self.assertTrue(st["needs_login"])
        self.assertIn("登入並同步到雲端", st["message"])
        host = self.cloud.host_id()
        self.knobs(refresh_mode="ok")
        self.sync()  # 再同步一次就好了
        self.assertTrue(self.cloud.account_status("u1")["configured"])
        self.assertTrue(self.stream(self.cloud))
        self.cloud.logout("u1")  # 雲端「清除 ChatGPT 憑證」：整份刪掉，主機代號留著
        self.assertIsNone(self.owner())
        self.assertEqual(self.cloud.host_id(), host)
        self.assertIn("還沒同步", self.cloud.account_status("u1")["message"])

    def test_cloud_status_without_owner(self):
        st = self.cloud.account_status("u1")
        self.assertEqual((st["mode"], st["signed_in"], st["can_sign_in"]), ("synced", False, False))
        self.assertIn("還沒同步", st["message"])
        with self.assertRaises(BrainError) as cm:
            self.stream(self.cloud)
        self.assertEqual((cm.exception.code, cm.exception.fallback), ("not_signed_in", True))


# ---------------------------------------------------------------- 第二部分：完整 HTTP（需要 server.py 的同步功能）


def _server_has_sync():
    try:
        text = (_util.ROOT / "server.py").read_text(encoding="utf-8")
    except OSError:
        return False
    return "/api/chatgpt/sync/import" in text and "/api/chatgpt/cloud" in text


@unittest.skipUnless(_server_has_sync(), "server.py 還沒有雲端同步的路由")
@unittest.skipUnless(HAVE_CRYPTO, "雲端存登入資料需要 cryptography")
class HttpSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.oa = fake_openai.start()
        cls.up = fake_upstash.start(token=TOKEN)
        cls.gm = fake_gemini.start()
        cls.cloud_dir, cls.local_dir = _util.copy_project(), _util.copy_project()
        cls.pc = _util.free_port()
        cls.pl = _util.free_port(exclude=range(cls.pc, cls.pc + 1))
        common_env = dict(_util.fake_openai_env(cls.oa.url), GEMINI_API_KEY="fake-gemini-key",
                          GEMINI_OPENAI_URL=cls.gm.url + "/v1beta/openai", GEMINI_BASE_URL=cls.gm.url + "/v1beta",
                          OLLAMA_BASE_URL=f"http://127.0.0.1:{_util.free_port(exclude=(cls.pc, cls.pl))}",
                          BRAIN_BACKUPS="gemini")
        cloud_env = dict(common_env, APP_MODE="cloud", APP_PASSCODE="pass-1234", APP_SECRET=APP_SECRET,
                         KV_REST_API_URL=cls.up.url, KV_REST_API_TOKEN=TOKEN, CHATGPT_SYNC_KEY=SYNC_KEY,
                         LLM_PROVIDER="chatgpt", LIVE_ENABLED="0", ALLOWED_HOSTS="127.0.0.1")
        local_env = dict(common_env, PORT=str(cls.pl), LLM_PROVIDER="gemini", LIVE_ENABLED="0")
        cls.cloud = _util.start_proc(["app.py", "--port", str(cls.pc)], cls.cloud_dir, _util.clean_env(cloud_env), cls.cloud_dir / "cloud.log")
        cls.local = _util.start_proc(["server.py", "--no-browser"], cls.local_dir, _util.clean_env(local_env), cls.local_dir / "local.log")
        ok = _util.wait_port(cls.pc, 25) and _util.wait_port(cls.pl, 25)
        if not ok:
            cls.tearDownClass()
            raise AssertionError("伺服器沒有啟動")

    @classmethod
    def tearDownClass(cls):
        for p in (getattr(cls, "cloud", None), getattr(cls, "local", None)):
            _util.stop_proc(p)
        for s in (cls.oa, cls.up, cls.gm):
            s.stop()

    def req(self, port, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        h = {"Host": f"127.0.0.1:{port}"}
        if body is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        conn.request(method, path, body=None if body is None else json.dumps(body).encode("utf-8"), headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, r.getheader("Location") or "", r.getheader("Set-Cookie") or "", data

    def login_cloud(self):
        status, _, cookie, data = self.req(self.pc, "POST", "/api/login", {"passcode": "pass-1234"})
        self.assertEqual(status, 200, data)
        return cookie.split(";", 1)[0]

    def sse(self, port, body, cookie):
        status, _, _, data = self.req(port, "POST", "/api/chat", body, {"Cookie": cookie})
        self.assertEqual(status, 200, data[:300])
        return [json.loads(line[5:]) for line in data.decode("utf-8").splitlines() if line.startswith("data:")]

    def test_full_flow(self):
        # 同步端點：沒有同步密碼或密碼錯都不行；本機沒有這些端點
        st, _, _, _ = self.req(self.pc, "POST", "/api/chatgpt/sync/host", {}, {"X-Sync-Key": "wrong"})
        self.assertIn(st, (401, 403))
        st, _, _, _ = self.req(self.pl, "POST", "/api/chatgpt/sync/host", {}, {"X-Sync-Key": SYNC_KEY})
        self.assertEqual(st, 404)
        # 本機設定雲端網址和同步密碼
        st, _, _, data = self.req(self.pl, "POST", "/api/chatgpt/cloud", {"cloud_url": f"http://127.0.0.1:{self.pc}", "sync_key": SYNC_KEY})
        self.assertEqual(st, 200, data)
        self.assertTrue(json.loads(data)["host_id"].startswith("urn:"))
        # 本機替雲端登入：/auth/chatgpt/start?target=cloud → 假 OpenAI → 本機 callback → 傳到雲端
        st, loc, _, _ = self.req(self.pl, "GET", "/auth/chatgpt/start?target=cloud")
        self.assertEqual(st, 302)
        cb_url = _util.location(loc, self.oa.url)
        self.assertTrue(cb_url.startswith(f"http://127.0.0.1:{self.pl}/auth/callback?"))
        p = urllib.parse.urlparse(cb_url)
        st, loc2, _, _ = self.req(self.pl, "GET", p.path + "?" + p.query)
        self.assertEqual(st, 302)
        self.assertIn("chatgpt=synced", loc2)
        # 雲端：登入後狀態是已同步，對話走 ChatGPT
        cookie = self.login_cloud()
        st, _, _, data = self.req(self.pc, "GET", "/api/status", headers={"Cookie": cookie})
        status = json.loads(data)
        self.assertTrue(status["chatgpt"]["signed_in"], status["chatgpt"])
        st, _, _, data = self.req(self.pc, "GET", "/api/scenarios", headers={"Cookie": cookie})
        sid = json.loads(data)["scenarios"][0]["id"]
        chat = {"scenario_id": sid, "messages": [{"role": "rep", "text": "您好，我是理專。"}]}
        events = self.sse(self.pc, chat, cookie)
        text = "".join(e.get("delta", "") for e in events)
        self.assertTrue(text)
        self.assertFalse(any(e.get("fallback") for e in events), events)
        # 續期失敗（devkit #5）：要重新同步，這次改用 Gemini
        with self.oa.state.lock:
            self.oa.state.knobs["refresh_mode"] = "invalidated"
            for info in self.oa.state.access.values():
                info["exp"] = 0  # access token 立刻過期：雲端會先續期，續期失敗
        events = self.sse(self.pc, chat, cookie)
        self.assertTrue(any(e.get("fallback") and e.get("brain") == "gemini" for e in events), events)
        self.assertTrue("".join(e.get("delta", "") for e in events))
        st, _, _, data = self.req(self.pc, "GET", "/api/status", headers={"Cookie": cookie})
        self.assertTrue(json.loads(data)["chatgpt"]["needs_login"])
        # 記錄裡不能有 code、token 或同步密碼
        for log in (self.cloud_dir / "cloud.log", self.local_dir / "local.log"):
            text = log.read_text("utf-8", "replace")
            for secret in ("code_", "rt_", "at_", SYNC_KEY, APP_SECRET):
                self.assertNotIn(secret, text, log.name)


if __name__ == "__main__":
    unittest.main()
