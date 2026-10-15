"""真人模式（HeyGen LiveAvatar）：
1. liveavatar.py 對假的 LiveAvatar（tools/fakes/fake_liveavatar.py）
2. server.py 的 /api/avatar/*：本機子程序（沒有 .env 的暫存複本）和雲端 app.py（APP_MODE=cloud、測試模式）
3. 網頁那邊的純邏輯：node 跑 tests/js/liveavatar_test.js（沒裝 node 就略過）
全部只連 127.0.0.1，不會碰到真的 api.liveavatar.com，也不會用到任何真的金鑰。"""

import http.client
import json
import shutil
import subprocess
import unittest
import uuid

import _util
import fake_gemini
import fake_liveavatar as fla
import liveavatar as la

KEY = "fake-la-key-0123456789abcdef"
APP_SECRET = "app-secret-" + "q" * 40


def reset(state, **knobs):
    with state.lock:
        state.sessions.clear()
        state.calls.clear()
        state.stops.clear()
        state.knobs.update({"credits": 100, "fail_start": False, "max_duration": 0, "mine_status": 200}, **knobs)


# ---------------------------------------------------------------- 1. liveavatar.py


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = fla.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def setUp(self):
        reset(self.srv.state)

    def client(self, key=KEY):
        return la.LiveAvatar(key, self.srv.url)

    def knobs(self, **kw):
        with self.srv.state.lock:
            self.srv.state.knobs.update(kw)

    def test_avatars_mine_first_and_filtered(self):
        rows = self.client().avatars()
        ids = [r["id"] for r in rows]
        self.assertEqual(ids[0], fla.MINE)
        self.assertTrue(rows[0]["custom"])
        self.assertEqual(set(ids), {fla.MINE, fla.WAYNE, fla.ANN, fla.JUDY})  # 訓練中、過期的不列
        self.assertEqual(len(ids), len(set(ids)))
        judy = next(r for r in rows if r["id"] == fla.JUDY)
        self.assertEqual(judy["preview_url"], "")
        self.assertEqual(set(rows[1]), {"id", "name", "preview_url", "custom"})
        self.assertIn(("GET", "/v1/avatars/public"), self.srv.state.calls)

    def test_avatars_without_own_list(self):
        self.knobs(mine_status=403)
        rows = self.client().avatars()
        self.assertTrue(rows)
        self.assertFalse(any(r["custom"] for r in rows))

    def test_start_sends_lite_and_hides_agent_token(self):
        s = self.client().start(fla.ANN, False, 1200)
        self.assertEqual(set(s), {"session_id", "session_token", "livekit_url", "livekit_client_token", "ws_url",
                                  "max_session_duration"})
        self.assertEqual(s["max_session_duration"], 1200)
        sent = self.srv.state.sessions[s["session_id"]]
        self.assertEqual(sent["state"], "started")
        self.assertEqual(sent["body"], {"mode": "LITE", "avatar_id": fla.ANN, "is_sandbox": False,
                                        "video_settings": {"quality": "high", "encoding": "H264"},
                                        "max_session_duration": 1200})

    def test_sandbox_only_wayne(self):
        s = self.client().start(fla.WAYNE, True, 1200)
        sess = self.srv.state.sessions[s["session_id"]]
        self.assertTrue(sess["sandbox"])
        self.assertNotIn("max_session_duration", sess["body"])  # 測試模式只有 60 秒：送 1200 會被拒絕（實測）
        self.assertEqual(s["max_session_duration"], 60)
        self.assertEqual(self.srv.state.calls.count(("POST", "/v1/sessions/token")), 1)
        with self.assertRaises(la.AvatarError) as cm:
            self.client().start(fla.ANN, True, 600)
        self.assertEqual(cm.exception.kind, "sandbox")

    def test_cloudflare_block_is_not_a_key_error(self):
        # 實測：urllib 預設的 User-Agent 會被 Cloudflare 擋（Error 1010）；我們有自己的 User-Agent，也不能誤報成金鑰不對
        self.assertNotIn("Python-urllib", la.USER_AGENT)
        e = la._error(403, la._message('{"title":"Error 1010: Access denied","status":403,'
                                       '"type":"https://developers.cloudflare.com/support/error-1010/"}'))
        self.assertEqual(e.kind, "blocked")
        self.assertNotIn("金鑰", str(e))

    def test_friendly_errors(self):
        with self.assertRaises(la.AvatarError) as cm:
            self.client("bad-key-0000").start(fla.ANN)
        self.assertEqual((cm.exception.kind, cm.exception.code), ("key", 401))
        self.assertIn("金鑰", str(cm.exception))
        with self.assertRaises(la.AvatarError) as cm:
            self.client().start(str(uuid.uuid4()))
        self.assertEqual(cm.exception.kind, "avatar")
        self.knobs(credits=0)
        with self.assertRaises(la.AvatarError) as cm:
            self.client().start(fla.ANN)
        self.assertEqual(cm.exception.kind, "credits")
        self.assertIn("點數", str(cm.exception))
        self.knobs(credits=100)
        dead = f"http://127.0.0.1:{_util.free_port()}"
        with self.assertRaises(la.AvatarError) as cm:
            la.LiveAvatar(KEY, dead).avatars()
        self.assertEqual(cm.exception.kind, "network")
        for e in (cm.exception,):
            self.assertNotIn(KEY, str(e) + e.detail)

    def test_plan_limit_retries_without_duration(self):
        self.knobs(max_duration=600)
        s = self.client().start(fla.ANN, False, 1200)
        self.assertNotIn("max_session_duration", self.srv.state.sessions[s["session_id"]]["body"])
        self.assertEqual(self.srv.state.calls.count(("POST", "/v1/sessions/token")), 2)

    def test_failed_start_is_stopped(self):
        self.knobs(fail_start=True)
        with self.assertRaises(la.AvatarError) as cm:
            self.client().start(fla.ANN)
        self.assertEqual(cm.exception.kind, "server")
        self.assertEqual(len(self.srv.state.stops), 1)  # 開到一半的工作階段有收掉

    def test_stop_twice(self):
        s = self.client().start(fla.ANN)
        self.assertTrue(self.client().stop(s["session_id"]))
        self.assertFalse(self.client().stop(s["session_id"]))
        self.assertFalse(self.client().stop(str(uuid.uuid4())))
        self.assertEqual(self.srv.state.stops, [s["session_id"]])


# ---------------------------------------------------------------- 2. server.py（HTTP）


class _Http(unittest.TestCase):
    port = 0
    cookie = ""

    def req(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Host": f"127.0.0.1:{self.port}"}
        if self.cookie:
            h["Cookie"] = self.cookie
        if body is not None or raw is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode("utf-8"))
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        text = r.read().decode("utf-8")
        conn.close()
        try:
            js = json.loads(text)
        except ValueError:
            js = None
        return r.status, js, text, r.getheader("Set-Cookie") or ""

    def assert_no_key(self, text):
        self.assertNotIn(KEY, text)


class HttpLocalTests(_Http):
    """本機：在「聲音設定」貼金鑰、開關、挑臉孔、開和關工作階段。"""

    @classmethod
    def setUpClass(cls):
        cls.gm = fake_gemini.start()
        cls.la = fla.start()
        cls.dir = _util.copy_project()
        cls.port = _util.free_port()
        env = _util.clean_env({
            "PORT": str(cls.port), "LLM_PROVIDER": "gemini", "LIVE_ENABLED": "1", "BRAIN_BACKUPS": "gemini",
            "GEMINI_API_KEY": "fake-gemini-key", "GEMINI_OPENAI_URL": cls.gm.url + "/v1beta/openai",
            "GEMINI_BASE_URL": cls.gm.url + "/v1beta", "LIVEAVATAR_BASE_URL": cls.la.url,
            "OLLAMA_BASE_URL": f"http://127.0.0.1:{_util.free_port(exclude=(cls.port,))}",
        })
        cls.proc = _util.start_proc(["server.py", "--no-browser"], cls.dir, env, cls.dir / "local.log")
        if not _util.wait_port(cls.port, 25):
            cls.tearDownClass()
            raise AssertionError("server.py 沒有啟動")

    @classmethod
    def tearDownClass(cls):
        _util.stop_proc(getattr(cls, "proc", None))
        cls.gm.stop()
        cls.la.stop()

    def setUp(self):
        reset(self.la.state)

    def test_full_flow(self):
        st, js, _, _ = self.req("GET", "/api/status")
        self.assertEqual(st, 200)
        av = js["avatar"]
        self.assertFalse(av["key_set"] or av["enabled"] or av["available"])
        self.assertIn("金鑰", av["reason"])
        self.assertEqual(self.req("GET", "/api/avatar/list")[0], 400)
        self.assertEqual(self.req("POST", "/api/avatar/config", {"enabled": True})[0], 400)  # 還沒有金鑰不能開

        # 貼金鑰：格式不對擋掉；對的存進 .env，但任何回應都不能帶出來
        self.assertEqual(self.req("POST", "/api/avatar/key", {"key": "has space in it"})[0], 400)
        st, js, text, _ = self.req("POST", "/api/avatar/key", {"key": KEY})
        self.assertEqual(st, 200, text)
        self.assertTrue(js["avatar"]["key_set"])
        self.assert_no_key(text)
        self.assertIn(f"LIVEAVATAR_API_KEY={KEY}", (self.dir / ".env").read_text("utf-8"))
        st, js, text, _ = self.req("GET", "/api/status")
        self.assert_no_key(text)
        self.assertTrue(js["avatar"]["available"])
        self.assertFalse(js["avatar"]["on"])

        # 還沒打開、還沒挑臉孔都不能開工作階段
        st, js, _, _ = self.req("POST", "/api/avatar/session", {"avatar": "chen"})
        self.assertEqual(st, 409)
        self.assertIn("關閉", js["error"])
        st, js, text, _ = self.req("POST", "/api/avatar/config", {"enabled": True})
        self.assertEqual(st, 200, text)
        self.assertTrue(js["avatar"]["on"])
        self.assertIn("LIVEAVATAR_ENABLED=1", (self.dir / ".env").read_text("utf-8"))
        st, js, _, _ = self.req("POST", "/api/avatar/session", {"avatar": "chen"})
        self.assertEqual(st, 409)
        self.assertIn("挑", js["error"])

        # 臉孔清單：第二次看用伺服器的快取，不再問 LiveAvatar
        st, js, text, _ = self.req("GET", "/api/avatar/list")
        self.assertEqual(st, 200, text)
        self.assertEqual(js["avatars"][0]["id"], fla.MINE)
        self.assertEqual(self.req("GET", "/api/avatar/list")[0], 200)
        self.assertEqual(self.la.state.calls.count(("GET", "/v1/avatars/public")), 1)

        # 挑臉孔：代號、客戶要對；圖片網址只收 https
        self.assertEqual(self.req("POST", "/api/avatar/assign", {"avatar": "chen", "avatar_id": "not-a-uuid"})[0], 400)
        self.assertEqual(self.req("POST", "/api/avatar/assign", {"avatar": "nobody", "avatar_id": fla.ANN})[0], 404)
        st, js, text, _ = self.req("POST", "/api/avatar/assign", {
            "avatar": "chen", "avatar_id": fla.ANN, "name": "Ann\nTherapist", "preview_url": "javascript:alert(1)"})
        self.assertEqual(st, 200, text)
        chen = next(c for c in js["customers"] if c["avatar"] == "chen")
        self.assertEqual(chen["face"], {"id": fla.ANN, "name": "Ann Therapist", "preview_url": ""})

        # 開工作階段：瀏覽器拿到連線資料，拿不到金鑰和 agent token
        st, js, text, _ = self.req("POST", "/api/avatar/session", {"avatar": "chen"})
        self.assertEqual(st, 200, text)
        self.assert_no_key(text)
        self.assertNotIn("livekit_agent_token", js)
        self.assertNotIn("lk_agent_", text)
        for k in ("session_id", "session_token", "livekit_url", "livekit_client_token", "ws_url"):
            self.assertTrue(js[k], k)
        self.assertEqual((js["sandbox"], js["name"], js["max_session_duration"]), (False, "Ann Therapist", 1200))
        sent = self.la.state.sessions[js["session_id"]]["body"]
        self.assertEqual((sent["mode"], sent["avatar_id"], sent["is_sandbox"]), ("LITE", fla.ANN, False))
        self.assertEqual(sent["video_settings"], {"quality": "high", "encoding": "H264"})

        # 結束：重複呼叫沒關係，LiveAvatar 只被叫一次；不是這裡開的不會去叫
        sid = js["session_id"]
        st, js, _, _ = self.req("POST", "/api/avatar/stop", {"session_id": sid})
        self.assertEqual((st, js["stopped"]), (200, True))
        st, js, _, _ = self.req("POST", "/api/avatar/stop", {"session_id": sid},
                                {"Sec-Fetch-Site": "same-origin", "Origin": f"http://127.0.0.1:{self.port}"})  # 像 sendBeacon
        self.assertEqual((st, js["stopped"]), (200, False))
        st, js, _, _ = self.req("POST", "/api/avatar/stop", {"session_id": str(uuid.uuid4())})
        self.assertEqual((st, js["stopped"]), (200, False))
        self.assertEqual(self.req("POST", "/api/avatar/stop", {"session_id": "../x"})[0], 400)
        self.assertEqual(self.la.state.stops, [sid])

        # LiveAvatar 的錯誤翻成中文
        with self.la.state.lock:
            self.la.state.knobs["credits"] = 0
        st, js, _, _ = self.req("POST", "/api/avatar/session", {"avatar": "chen"})
        self.assertEqual(st, 502)
        self.assertIn("點數", js["error"])
        with self.la.state.lock:
            self.la.state.knobs["credits"] = 100

        # 即時通話關掉：真人模式跟著不能用（開關還記著，畫面上會說原因）
        self.assertEqual(self.req("POST", "/api/live/config", {"enabled": False})[0], 200)
        st, js, _, _ = self.req("GET", "/api/status")
        self.assertTrue(js["avatar"]["enabled"])
        self.assertFalse(js["avatar"]["on"])
        self.assertIn("即時通話", js["avatar"]["reason"])
        self.assertEqual(self.req("POST", "/api/avatar/session", {"avatar": "chen"})[0], 400)
        self.assertEqual(self.req("POST", "/api/avatar/config", {"enabled": True})[0], 400)
        self.assertEqual(self.req("POST", "/api/avatar/config", {"enabled": False})[0], 200)  # 關掉永遠可以
        self.assertEqual(self.req("POST", "/api/live/config", {"enabled": True})[0], 200)

        self.assert_no_key((self.dir / "local.log").read_text("utf-8", "replace"))

    def test_rejects_other_sites(self):
        body = json.dumps({"avatar": "chen"}).encode("utf-8")
        for headers in ({"Origin": "http://evil.example"}, {"Sec-Fetch-Site": "cross-site"}, {"Content-Type": "text/plain"}):
            st, _, _, _ = self.req("POST", "/api/avatar/session", raw=body, headers=headers)
            self.assertEqual(st, 403, headers)
            st, _, _, _ = self.req("POST", "/api/avatar/stop", raw=json.dumps({"session_id": str(uuid.uuid4())}).encode(),
                                   headers=headers)
            self.assertEqual(st, 403, headers)
        self.assertEqual(self.la.state.calls, [])


class HttpCloudTests(_Http):
    """雲端（app.py、APP_MODE=cloud）：金鑰只能放在 Vercel 環境變數；測試模式一律用 Wayne。"""

    @classmethod
    def setUpClass(cls):
        cls.gm = fake_gemini.start()
        cls.la = fla.start()
        cls.dir = _util.copy_project()
        cls.port = _util.free_port()
        env = _util.clean_env({
            "APP_MODE": "cloud", "APP_PASSCODE": "pass-1234", "APP_SECRET": APP_SECRET, "ALLOWED_HOSTS": "127.0.0.1",
            "LLM_PROVIDER": "gemini", "LIVE_ENABLED": "1", "BRAIN_BACKUPS": "gemini",
            "GEMINI_API_KEY": "fake-gemini-key", "GEMINI_OPENAI_URL": cls.gm.url + "/v1beta/openai",
            "GEMINI_BASE_URL": cls.gm.url + "/v1beta",
            "LIVEAVATAR_API_KEY": KEY, "LIVEAVATAR_BASE_URL": cls.la.url, "LIVEAVATAR_SANDBOX": "1",
            "LIVEAVATAR_ENABLED": "1",
        })
        cls.proc = _util.start_proc(["app.py", "--port", str(cls.port)], cls.dir, env, cls.dir / "cloud.log")
        if not _util.wait_port(cls.port, 25):
            cls.tearDownClass()
            raise AssertionError("app.py 沒有啟動")

    @classmethod
    def tearDownClass(cls):
        _util.stop_proc(getattr(cls, "proc", None))
        cls.gm.stop()
        cls.la.stop()

    def test_cloud_sandbox_flow(self):
        self.cookie = ""
        self.assertEqual(self.req("POST", "/api/avatar/session", {"avatar": "lin"})[0], 401)  # 要先登入
        st, _, _, cookie = self.req("POST", "/api/login", {"passcode": "pass-1234"})
        self.assertEqual(st, 200)
        self.cookie = cookie.split(";", 1)[0]
        st, js, text, _ = self.req("GET", "/api/status")
        self.assert_no_key(text)
        av = js["avatar"]
        self.assertEqual((av["key_set"], av["sandbox"], av["enabled"], av["on"], av["key_source"]),
                         (True, True, True, True, "env"))
        st, js, _, _ = self.req("POST", "/api/avatar/key", {"key": "another-key-123456"})
        self.assertEqual((st, js.get("env_name")), (409, "LIVEAVATAR_API_KEY"))

        # 測試模式：沒挑臉孔也能開，一律用 Wayne
        st, js, text, _ = self.req("POST", "/api/avatar/session", {"avatar": "lin"})
        self.assertEqual(st, 200, text)
        self.assert_no_key(text)
        self.assertEqual((js["sandbox"], js["name"]), (True, "Wayne"))
        sess = self.la.state.sessions[js["session_id"]]
        self.assertEqual((sess["avatar_id"], sess["sandbox"]), (fla.WAYNE, True))
        st, js2, _, _ = self.req("POST", "/api/avatar/stop", {"session_id": js["session_id"]})
        self.assertEqual((st, js2["stopped"]), (200, True))
        text = (self.dir / "cloud.log").read_text("utf-8", "replace")
        for secret in (KEY, APP_SECRET):
            self.assertNotIn(secret, text)


# ---------------------------------------------------------------- 3. 網頁的純邏輯（node）


@unittest.skipUnless(shutil.which("node"), "沒有安裝 node")
class JsTests(unittest.TestCase):
    def node(self, *args):
        return subprocess.run(["node", *args], cwd=str(_util.ROOT), capture_output=True, timeout=120)

    def test_syntax(self):
        for f in ("web/app.js", "web/liveavatar.js", "web/avatars.js", "web/login.js"):
            r = self.node("--check", f)
            self.assertEqual(r.returncode, 0, f + r.stderr.decode("utf-8", "replace"))

    def test_logic(self):
        r = self.node("tests/js/liveavatar_test.js")
        out = (r.stdout + r.stderr).decode("utf-8", "replace")
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("ok", out)


if __name__ == "__main__":
    unittest.main()
