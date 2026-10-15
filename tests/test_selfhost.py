"""RunPod 那一份（APP_MODE=server）和自架真人：
1. faceengine.py 的 LiveKit 通行證（欄位、期限、只能進一個房間、看得到的地方沒有密碼）和控制連線的簽名
2. faceengine.py 對假的臉孔引擎（tools/fakes/fake_faceengine.py）：開、關、一次一通、引擎沒開
3. 伺服器模式的資料：檔案放在 DATA_DIR，每個瀏覽器的背景各一個檔
4. server.py 子程序（APP_MODE=server、AUTH_MODE=open、假的 Gemini 和引擎）：代理的標頭、免登入、設定重開還在、
   自架真人的 /api/avatar/session、/api/avatar/stop；回應和記錄都不能有 LIVEKIT_API_SECRET、FACE_SECRET
5. 本機（沒有這些變數）的真人模式跟以前一樣是 HeyGen
全部只連 127.0.0.1，不會用到任何真的金鑰。"""

import base64
import hashlib
import hmac
import http.client
import json
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import _util
import fake_faceengine as ffe
import fake_gemini
import faceengine as fe
import storage

LK_KEY = "APIfakeLiveKitKey"
LK_SECRET = "lk-secret-" + "s" * 40
FACE_SECRET = "face-secret-" + "f" * 40
APP_SECRET = "app-secret-" + "p" * 40
POD_HOST = "podtest-8765.proxy.runpod.net"
SECRETS = (LK_SECRET, FACE_SECRET, APP_SECRET)
FACE_RETRY = 2  # 和 server.FACE_HEALTH_RETRY 一樣（伺服器在子程序裡，不能直接 import）


def b64d(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def jwt_claims(token, secret):
    """驗 HS256 簽名，回傳內容；簽名不對就 AssertionError。"""
    head, body, sig = token.split(".")
    want = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(want, b64d(sig)):
        raise AssertionError("簽名不對")
    assert json.loads(b64d(head)) == {"alg": "HS256", "typ": "JWT"}
    return json.loads(b64d(body))


def engine_control(srv, **knobs):
    with srv.state.lock:
        srv.state.knobs.update(knobs)


def engine_reset(srv):
    with srv.state.lock:
        srv.state.knobs.update({"ready": True, "faces": ["wang"], "busy_status": 409, "fail": ""})
        srv.state.sessions.clear()
        srv.state.calls.clear()
        srv.state.stops.clear()
        srv.state.active = ""
        srv.state.bad_secret = 0


# ---------------------------------------------------------------- 1. 通行證和簽名


class TokenTests(unittest.TestCase):
    def test_publisher_and_viewer_claims(self):
        now = 1_800_000_000
        pub = fe.livekit_token(LK_KEY, LK_SECRET, "face-abc", "aic-room1", publish=True, subscribe=False, now=now)
        sub = fe.livekit_token(LK_KEY, LK_SECRET, "rep-abc", "aic-room1", publish=False, subscribe=True, ttl=120, now=now)
        p, s = jwt_claims(pub, LK_SECRET), jwt_claims(sub, LK_SECRET)
        self.assertEqual((p["iss"], p["sub"]), (LK_KEY, "face-abc"))
        self.assertEqual(p["exp"] - now, fe.TOKEN_TTL)
        self.assertLessEqual(fe.TOKEN_TTL, 900)  # 短效
        self.assertLessEqual(p["nbf"], now)
        self.assertEqual(p["video"], {"room": "aic-room1", "roomJoin": True, "canPublish": True, "canSubscribe": False,
                                      "canPublishData": True})
        self.assertEqual(s["exp"] - now, 120)
        self.assertEqual(s["video"], {"room": "aic-room1", "roomJoin": True, "canPublish": False, "canSubscribe": True,
                                      "canPublishData": False})
        # 只能進這個房間：沒有 roomAdmin、roomCreate、roomList 之類的權限
        for c in (p, s):
            self.assertEqual(set(c["video"]), {"room", "roomJoin", "canPublish", "canSubscribe", "canPublishData"})
        for t in (pub, sub):
            self.assertNotIn(LK_SECRET, t)
            self.assertNotIn(LK_SECRET, b64d(t.split(".")[1]).decode())
        with self.assertRaises(AssertionError):
            jwt_claims(sub, "another-secret")
        with self.assertRaises(ValueError):
            fe.livekit_token(LK_KEY, "", "x", "r", True, False)

    def test_ws_signature(self):
        eng = fe.FaceEngine("http://127.0.0.1:1", FACE_SECRET, "wss://pod-8010.proxy.runpod.net")
        url = eng.ws_url("/face/ws", "sess-1", ttl=300, now=1_800_000_000)
        u = urlparse(url)
        self.assertEqual((u.scheme, u.netloc, u.path), ("wss", "pod-8010.proxy.runpod.net", "/face/ws"))
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.assertEqual((q["session"], q["exp"]), ("sess-1", "1800000300"))
        want = hmac.new(FACE_SECRET.encode(), b"sess-1.1800000300", hashlib.sha256).hexdigest()
        self.assertEqual(q["sig"], want)
        self.assertNotIn(FACE_SECRET, url)
        self.assertTrue(fe.public_ok("wss://x.proxy.runpod.net"))
        self.assertFalse(fe.public_ok("https://x.proxy.runpod.net"))
        self.assertFalse(fe.public_ok(""))


# ---------------------------------------------------------------- 2. 引擎的用戶端


class EngineClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ffe.start(secret=FACE_SECRET)

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def setUp(self):
        engine_reset(self.srv)
        self.eng = fe.FaceEngine(self.srv.url, FACE_SECRET, "ws://127.0.0.1:1")

    def test_health_start_stop(self):
        h = self.eng.health()
        self.assertEqual((h["ok"], h["ready"], h["faces"], h["sessions"]), (True, True, ["wang"], 0))
        path = self.eng.start("s1", "wang", "worried", "wss://lk.test", "tok", 1200)
        self.assertEqual(path, "/face/ws")
        sent = self.srv.state.sessions["s1"]
        self.assertEqual(sent, {"session_id": "s1", "face": "wang", "expression": "worried", "livekit_url": "wss://lk.test",
                                "livekit_token": "tok", "max_seconds": 1200})
        self.assertEqual(self.eng.health()["sessions"], 1)
        # 一張顯卡一次一通：第二通被拒，原因是引擎說的中文
        with self.assertRaises(fe.FaceError) as cm:
            self.eng.start("s2", "wang", "neutral", "wss://lk.test", "tok", 1200)
        self.assertEqual(cm.exception.kind, "busy")
        self.assertIn("一次只能一通", str(cm.exception))
        engine_control(self.srv, busy_status=200)  # 真的引擎可能回 200 + ok:false
        with self.assertRaises(fe.FaceError) as cm:
            self.eng.start("s2", "wang", "neutral", "wss://lk.test", "tok", 1200)
        self.assertEqual(cm.exception.kind, "busy")
        self.assertTrue(self.eng.stop("s1"))
        self.assertTrue(self.eng.stop("s1"))  # 重複收沒關係
        self.assertEqual(self.srv.state.stops, ["s1", "s1"])
        self.eng.start("s3", "wang", "bogus", "wss://lk.test", "tok", 60)
        self.assertEqual(self.srv.state.sessions["s3"]["expression"], "neutral")  # 不認得的表情改成平常

    def test_errors(self):
        with self.assertRaises(fe.FaceError) as cm:
            self.eng.start("s1", "chen", "neutral", "wss://lk.test", "tok", 60)
        self.assertEqual(cm.exception.kind, "face")
        bad = fe.FaceEngine(self.srv.url, "wrong-secret", "")
        with self.assertRaises(fe.FaceError) as cm:
            bad.start("s1", "wang", "neutral", "wss://lk.test", "tok", 60)
        self.assertEqual(cm.exception.kind, "secret")
        self.assertNotIn("wrong-secret", str(cm.exception) + cm.exception.detail)
        self.assertFalse(bad.stop("s1"))  # 掛斷時不丟錯
        engine_control(self.srv, ready=False)
        with self.assertRaises(fe.FaceError) as cm:
            self.eng.start("s1", "wang", "neutral", "wss://lk.test", "tok", 60)
        self.assertIn("載入", str(cm.exception))
        dead = fe.FaceEngine(f"http://127.0.0.1:{_util.free_port()}", FACE_SECRET, "")
        with self.assertRaises(fe.FaceError) as cm:
            dead.health(timeout=1)
        self.assertEqual(cm.exception.kind, "network")
        self.assertIn("沒有開", str(cm.exception))


# ---------------------------------------------------------------- 3. 伺服器模式的資料


class ServerStoreTests(unittest.TestCase):
    def test_open_store_server_mode(self):
        root = Path(tempfile.mkdtemp())
        data = root / "data-dir"
        st = storage.open_store(root, {"APP_MODE": "server", "DATA_DIR": str(data)}, True)
        self.assertIsInstance(st, storage.FileStore)
        self.assertTrue(st.durable)
        self.assertEqual(st.root, data)
        st = storage.open_store(root, {"APP_MODE": "server"}, True)
        self.assertEqual(st.root, root / "data")  # 沒填 DATA_DIR：程式旁邊的 data/
        # 有 Upstash 變數也照樣用檔案（伺服器模式明確說要檔案）
        st = storage.open_store(root, {"APP_MODE": "server", "KV_REST_API_URL": "https://x.upstash.io",
                                       "KV_REST_API_TOKEN": "t"}, True)
        self.assertEqual(st.kind, "file")
        # 其他模式不變
        self.assertEqual(storage.open_store(root, {}, False).root, root)
        self.assertEqual(storage.open_store(root, {"APP_MODE": "cloud"}, True).kind, "memory")

    def test_per_user_profiles(self):
        root = Path(tempfile.mkdtemp())
        st = storage.FileStore(root, per_user=True)
        st.put_doc("profile:shared", {"name": "共用"})
        st.put_doc("profile:" + "a" * 32, {"name": "A"})
        st.put_doc("profile:" + "b" * 32, {"name": "B"})
        self.assertEqual(json.loads((root / "profile.json").read_text("utf-8"))["name"], "共用")
        self.assertEqual(st.get_doc("profile:" + "a" * 32), {"name": "A"})
        self.assertEqual(st.get_doc("profile:" + "b" * 32), {"name": "B"})
        self.assertTrue((root / "profiles" / ("a" * 32 + ".json")).exists())
        for bad in ("profile:../x", "profile:a/b", "profile:" + "x" * 81):
            with self.assertRaises(ValueError):
                st.put_doc(bad, {})
        st.set_setting("FACE_PROVIDER", "heygen")  # 網頁上改的設定：放在 DATA_DIR/.env
        self.assertEqual(st.settings()["FACE_PROVIDER"], "heygen")
        # 本機（per_user=False）跟以前一樣都是 profile.json
        local = storage.FileStore(root / "local")
        local.put_doc("profile:local", {"x": 1})
        self.assertTrue((root / "local" / "profile.json").exists())


# ---------------------------------------------------------------- 4. server.py 子程序（伺服器模式）


class PodServerTests(unittest.TestCase):
    """像 RunPod 那一份：APP_MODE=server、AUTH_MODE=open、PROFILE_SCOPE=shared、資料在 DATA_DIR，前面有代理。"""

    @classmethod
    def setUpClass(cls):
        cls.gm = fake_gemini.start()
        cls.fe = ffe.start(secret=FACE_SECRET)
        cls.dir = _util.copy_project()
        cls.data = Path(tempfile.mkdtemp(prefix="aicoach-data-"))
        cls.port = _util.free_port()
        cls.env = _util.clean_env({
            "APP_MODE": "server", "DATA_DIR": str(cls.data), "HOST": "127.0.0.1", "PORT": str(cls.port),
            "AUTH_MODE": "open", "APP_SECRET": APP_SECRET, "ALLOWED_HOSTS": POD_HOST, "PROFILE_SCOPE": "shared",
            "LLM_PROVIDER": "gemini", "LIVE_ENABLED": "1", "BRAIN_BACKUPS": "gemini", "LIVEAVATAR_ENABLED": "1",
            "GEMINI_API_KEY": "fake-gemini-key", "GEMINI_OPENAI_URL": cls.gm.url + "/v1beta/openai",
            "GEMINI_BASE_URL": cls.gm.url + "/v1beta",
            "LIVEKIT_URL": "wss://fake-livekit.test", "LIVEKIT_API_KEY": LK_KEY, "LIVEKIT_API_SECRET": LK_SECRET,
            "FACE_SECRET": FACE_SECRET, "FACE_URL": cls.fe.url, "FACE_PUBLIC_URL": "wss://podtest-8010.proxy.runpod.net",
        })
        cls.proc = None
        cls.start_server()

    @classmethod
    def start_server(cls):
        n = len(list(cls.dir.glob("pod*.log")))
        cls.proc = _util.start_proc(["server.py"], cls.dir, cls.env, cls.dir / f"pod{n}.log")
        if not _util.wait_port(cls.port, 25):
            log = (cls.dir / f"pod{n}.log").read_text("utf-8", "replace")[-800:]
            cls.tearDownClass()
            raise AssertionError("server.py（伺服器模式）沒有啟動：" + log)

    @classmethod
    def tearDownClass(cls):
        _util.stop_proc(cls.proc)
        cls.gm.stop()
        cls.fe.stop()

    def setUp(self):
        engine_reset(self.fe)
        self.cookie = ""

    def req(self, method, path, body=None, headers=None, forwarded=POD_HOST):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Host": "100.65.21.65:60972"}  # 代理連進來用的是內部位址（實測 RunPod 的代理就是這樣）
        if forwarded:
            h.update({"X-Forwarded-Host": forwarded, "X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.9",
                      "Cf-Connecting-Ip": "203.0.113.9"})
        if self.cookie:
            h["Cookie"] = self.cookie
        if body is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        conn.request(method, path, body=None if body is None else json.dumps(body).encode("utf-8"), headers=h)
        r = conn.getresponse()
        text = r.read().decode("utf-8", "replace")
        conn.close()
        try:
            js = json.loads(text)
        except ValueError:
            js = None
        cookie = r.getheader("Set-Cookie") or ""
        if cookie and not self.cookie:
            self.cookie = cookie.split(";", 1)[0]
        return r.status, js, text, cookie

    def logs(self):
        return "".join(p.read_text("utf-8", "replace") for p in self.dir.glob("pod*.log"))

    def assert_no_secret(self, text):
        for s in SECRETS:
            self.assertNotIn(s, text)

    def test_proxy_headers_and_open_mode(self):
        st, js, text, cookie = self.req("GET", "/api/session")
        self.assertEqual(st, 200, text)
        self.assertEqual((js["auth_required"], js["logged_in"], js["hosted"]), (False, True, True))
        self.assertEqual(js["setup_problems"], [])  # 檔案存得住：不會叫你去接 Upstash
        self.assertIn("Secure", cookie)  # 代理說是 https：cookie 只走 https
        self.assertIn("HttpOnly", cookie)
        st, _, text, _ = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn("<html", text.lower())
        # 不在允許清單的網址、沒經過代理（只有內部位址）都擋掉
        self.assertEqual(self.req("GET", "/api/status", forwarded="evil.example")[0], 403)
        self.assertEqual(self.req("GET", "/api/status", forwarded="")[0], 403)
        # 跨站送來的改設定擋掉；從頁面送的可以
        st, _, _, _ = self.req("POST", "/api/live/config", {"enabled": True}, headers={"Origin": "https://evil.example"})
        self.assertEqual(st, 403)
        st, _, text, _ = self.req("POST", "/api/live/config", {"enabled": True}, headers={"Origin": f"https://{POD_HOST}"})
        self.assertEqual(st, 200, text)
        for path in ("/server.py", "/.env", "/faceengine.py"):
            self.assertEqual(self.req("GET", path)[0], 404, path)

    def test_status_defaults_to_selfhost(self):
        st, js, text, _ = self.req("GET", "/api/status")
        self.assertEqual(st, 200, text)
        av = js["avatar"]
        self.assertEqual((av["provider"], av["choice"], av["enabled"], av["on"], av["available"]),
                         ("selfhost", "auto", True, True, True), av)
        self.assertEqual([p["id"] for p in av["providers"]], ["selfhost", "heygen"])
        self.assertEqual(av["selfhost_faces"], ["wang"])
        self.assert_no_secret(text)

    def test_selfhost_session_flow(self):
        st, js, text, _ = self.req("POST", "/api/avatar/session", {"avatar": "wang", "expression": "worried"})
        self.assertEqual(st, 200, text)
        self.assert_no_secret(text)
        self.assertEqual(set(js), {"session_id", "livekit_url", "livekit_client_token", "ws_url", "max_session_duration",
                                   "provider", "keepalive_ms", "heard_lag_sec", "sandbox", "name"})
        self.assertEqual((js["provider"], js["keepalive_ms"], js["livekit_url"], js["sandbox"]),
                         ("selfhost", 20000, "wss://fake-livekit.test", False))
        self.assertGreater(js["max_session_duration"], 0)
        sid = js["session_id"]
        uuid.UUID(sid)
        # 瀏覽器的通行證只能看，引擎的只能送；同一個房間、不同身分
        viewer = jwt_claims(js["livekit_client_token"], LK_SECRET)
        sent = self.fe.state.sessions[sid]
        engine = jwt_claims(sent["livekit_token"], LK_SECRET)
        self.assertEqual(viewer["video"]["room"], engine["video"]["room"])
        self.assertTrue(viewer["video"]["room"].startswith("aic-"))
        self.assertEqual((viewer["video"]["canPublish"], viewer["video"]["canSubscribe"]), (False, True))
        self.assertEqual((engine["video"]["canPublish"], engine["video"]["canSubscribe"]), (True, False))
        self.assertEqual(engine["sub"], f"face-{sid}")
        self.assertNotEqual(viewer["sub"], engine["sub"])
        self.assertLessEqual(viewer["exp"] - time.time(), fe.TOKEN_TTL + 5)
        self.assertNotIn(sent["livekit_token"], text)  # 能送影像的那張不給瀏覽器
        self.assertEqual((sent["face"], sent["expression"], sent["livekit_url"]), ("wang", "worried", "wss://fake-livekit.test"))
        # 控制連線的網址：公開的 8010 網址 + 簽名
        u = urlparse(js["ws_url"])
        self.assertEqual((u.scheme, u.netloc, u.path), ("wss", "podtest-8010.proxy.runpod.net", "/face/ws"))
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.assertEqual(q["session"], sid)
        self.assertEqual(q["sig"], hmac.new(FACE_SECRET.encode(), f"{sid}.{q['exp']}".encode(), hashlib.sha256).hexdigest())
        self.assertTrue(0 < int(q["exp"]) - time.time() <= fe.WS_TTL + 5)

        # 第二通：引擎說有人在用
        st, js2, text, _ = self.req("POST", "/api/avatar/session", {"avatar": "wang"})
        self.assertEqual(st, 409, text)
        self.assertIn("一次只能一通", js2["error"])
        # 沒有自架臉孔的客戶：說清楚，這通用語音
        st, js2, _, _ = self.req("POST", "/api/avatar/session", {"avatar": "chen"})
        self.assertEqual(st, 409)
        self.assertIn("自架", js2["error"])
        self.assertEqual(self.req("POST", "/api/avatar/session", {"avatar": "nobody"})[0], 404)

        # 別的瀏覽器（另一個匿名代號）不能收掉我的
        saved, self.cookie = self.cookie, ""
        st, _, _, _ = self.req("POST", "/api/avatar/stop", {"session_id": sid})
        self.assertEqual(st, 403)
        self.cookie = saved
        # 收掉：引擎收到 stop；重複收、查不到的也請引擎收（引擎可以重複收）
        st, js2, _, _ = self.req("POST", "/api/avatar/stop", {"session_id": sid})
        self.assertEqual((st, js2["stopped"]), (200, True))
        self.assertEqual(self.fe.state.active, "")
        st, js2, _, _ = self.req("POST", "/api/avatar/stop", {"session_id": sid})
        self.assertEqual(st, 200)
        self.assertEqual(self.fe.state.stops, [sid, sid])
        self.assertEqual(self.req("POST", "/api/avatar/stop", {"session_id": "../x"})[0], 400)
        self.assert_no_secret(self.logs())

    def test_engine_not_ready_and_switch_provider(self):
        engine_control(self.fe, ready=False)
        # 狀態還記著「好了」：引擎拒絕（還在載入）→ 伺服器馬上重問引擎，狀態列跟著改
        st, js, text, _ = self.req("POST", "/api/avatar/session", {"avatar": "wang"})
        self.assertIn(st, (400, 502), text)
        st, js, _, _ = self.req("GET", "/api/status")
        av = js["avatar"]
        # 沒有 HeyGen 金鑰：還是自架，原因說引擎還在準備；這通電話不會開真人
        self.assertEqual((av["provider"], av["on"], av["available"]), ("selfhost", False, False))
        self.assertIn("準備", av["reason"])
        self.assertEqual(self.req("POST", "/api/avatar/session", {"avatar": "wang"})[0], 400)
        engine_control(self.fe, ready=True)
        time.sleep(FACE_RETRY + 0.3)  # 還沒好的狀態只記幾秒：引擎一好，狀態列很快就跟上
        st, js, _, _ = self.req("GET", "/api/status")
        self.assertTrue(js["avatar"]["on"], js["avatar"])
        # 改成 HeyGen：這台沒有 LiveAvatar 金鑰，畫面說原因；再改回自架
        st, js, text, _ = self.req("POST", "/api/avatar/config", {"provider": "heygen"})
        self.assertEqual(st, 200, text)
        self.assertEqual((js["avatar"]["provider"], js["avatar"]["choice"], js["avatar"]["available"]), ("heygen", "heygen", False))
        self.assertIn("LiveAvatar", js["avatar"]["reason"])
        self.assertIn("伺服器的環境變數", js["avatar"]["reason"])  # 不是叫你去 Vercel
        self.assertEqual(js["avatar"]["key_source"], "server")
        st, js2, _, _ = self.req("POST", "/api/avatar/key", {"key": "another-key-123456"})
        self.assertEqual((st, js2.get("how")), (409, "server"))
        self.assertEqual(self.req("POST", "/api/avatar/config", {"provider": "bogus"})[0], 400)
        st, js, _, _ = self.req("POST", "/api/avatar/config", {"provider": "selfhost"})
        self.assertEqual((st, js["avatar"]["provider"], js["avatar"]["choice"]), (200, "selfhost", "selfhost"))
        st, js, _, _ = self.req("POST", "/api/avatar/config", {"provider": "auto"})
        self.assertEqual((st, js["avatar"]["choice"]), (200, "auto"))

    def test_zz_settings_survive_restart(self):
        # 改設定、存背景，重開伺服器還在；檔案都在 DATA_DIR，程式資料夾裡沒有任何個人資料
        st, _, text, _ = self.req("POST", "/api/avatar/config", {"provider": "heygen"})
        self.assertEqual(st, 200, text)
        st, js, _, _ = self.req("GET", "/api/profile")
        profile = dict(js["profile"], surname="陳", given_name="小明")
        st, js, text, _ = self.req("POST", "/api/profile", {"profile": profile})
        self.assertEqual(st, 200, text)
        self.assertTrue((self.data / "profile.json").exists())  # PROFILE_SCOPE=shared：大家共用一份
        self.assertIn("FACE_PROVIDER=heygen", (self.data / ".env").read_text("utf-8"))
        for name in (".env", "voices.json", "profile.json", "tts_cache", "data"):
            self.assertFalse((self.dir / name).exists(), name)
        _util.stop_proc(PodServerTests.proc)
        time.sleep(0.3)
        PodServerTests.start_server()
        self.cookie = ""
        st, js, _, _ = self.req("GET", "/api/profile")
        self.assertEqual((js["profile"]["surname"], js["profile"]["given_name"]), ("陳", "小明"))
        st, js, _, _ = self.req("GET", "/api/status")
        self.assertEqual(js["avatar"]["choice"], "heygen")
        self.req("POST", "/api/avatar/config", {"provider": "auto"})
        self.assert_no_secret(self.logs())


# ---------------------------------------------------------------- 5. 本機跟以前一樣


class LocalUnchangedTests(unittest.TestCase):
    def test_local_probe(self):
        d = _util.copy_project()
        code = ("import json, server; i = server.avatar_info(); "
                "print(json.dumps({'cloud': server.ON_CLOUD, 'server': server.SERVER_MODE, 'store': server.STORE.kind, "
                "'root': str(server.STORE.root), 'provider': i['provider'], 'providers': 'providers' in i}))")
        r = subprocess.run([sys.executable, "-c", code], cwd=str(d), env=_util.clean_env(), capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        out = json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])
        self.assertEqual((out["cloud"], out["server"], out["store"], out["provider"], out["providers"]),
                         (False, False, "file", "heygen", False))
        self.assertEqual(Path(out["root"]).resolve(), d.resolve())  # 本機的資料還是放在程式旁邊


if __name__ == "__main__":
    unittest.main()
