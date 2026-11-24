"""「新增情境」三步驟（web/arena.js）和情境裡的劇本（playbook）：
1. 網頁的純邏輯：node 跑 tests/js/arena_test.js（沒裝 node 就略過）。
2. 三種人設 × 四種商品（FCN、海外債、基金、自訂）× 三種刁難度轉出來的 36 個情境，真的送去伺服器存（雲端 app.py、假的 Gemini），每一個都要收得下。
3. 劇本跟著情境存、清單和編輯畫面拿得到；沒送就沿用、送 null 就拿掉；太大或格式不對會被擋；隱藏設定照樣不外洩。
全部只連 127.0.0.1，不會用到任何真的金鑰。"""

import http.client
import json
import shutil
import subprocess
import unittest

import _util
import fake_gemini

APP_SECRET = "app-secret-" + "a" * 40
NODE = shutil.which("node")


def node(*args):
    return subprocess.run(["node", *args], cwd=str(_util.ROOT), capture_output=True, timeout=120)


@unittest.skipUnless(NODE, "沒有安裝 node")
class JsTests(unittest.TestCase):
    def test_syntax(self):
        r = node("--check", "web/arena.js")
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))

    def test_logic(self):
        r = node("tests/js/arena_test.js")
        out = (r.stdout + r.stderr).decode("utf-8", "replace")
        self.assertEqual(r.returncode, 0, out)
        self.assertRegex(out, "all [0-9]+ ok")


@unittest.skipUnless(NODE, "沒有安裝 node")
class SaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = node("tests/js/arena_test.js", "--dump")
        if r.returncode != 0:
            raise AssertionError(r.stderr.decode("utf-8", "replace"))
        cls.scenarios = json.loads(r.stdout.decode("utf-8"))
        cls.gm = fake_gemini.start()
        cls.dir = _util.copy_project()
        cls.port = _util.free_port()
        env = _util.clean_env({
            "APP_MODE": "cloud", "AUTH_MODE": "open", "APP_SECRET": APP_SECRET, "ALLOWED_HOSTS": "127.0.0.1",
            "LLM_PROVIDER": "gemini", "LIVE_ENABLED": "0", "BRAIN_BACKUPS": "gemini",
            "GEMINI_API_KEY": "fake-gemini-key", "GEMINI_OPENAI_URL": cls.gm.url + "/v1beta/openai",
            "GEMINI_BASE_URL": cls.gm.url + "/v1beta",
        })
        cls.proc = _util.start_proc(["app.py", "--port", str(cls.port)], cls.dir, env, cls.dir / "cloud.log")
        if not _util.wait_port(cls.port, 25):
            cls.tearDownClass()
            raise AssertionError("app.py 沒有啟動：" + (cls.dir / "cloud.log").read_text("utf-8", "replace")[-800:])
        st, _, _, set_cookie = cls.req(cls, "GET", "/api/status")
        assert st == 200
        cls.cookie = set_cookie.split(";", 1)[0]

    @classmethod
    def tearDownClass(cls):
        _util.stop_proc(getattr(cls, "proc", None))
        cls.gm.stop()

    def req(self, method, path, body=None, cookie=""):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h = {"Host": f"127.0.0.1:{self.port}"}
        if cookie:
            h["Cookie"] = cookie
        if body is not None:
            h["Content-Type"] = "application/json"
        conn.request(method, path, body=None if body is None else json.dumps(body).encode("utf-8"), headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        try:
            js = json.loads(data.decode("utf-8"))
        except ValueError:
            js = None
        return r.status, js, data, r.getheader("Set-Cookie") or ""

    def post(self, path, body):
        st, js, data, _ = self.req("POST", path, body, self.cookie)
        return st, js, data

    def test_1_all_combinations_save(self):
        self.assertEqual(len(self.scenarios), 36)
        ids = []
        for sc in self.scenarios:
            st, js, data = self.post("/api/editor/save", {"scenario": sc})
            self.assertEqual(st, 200, f"{sc['title']}：{data[:300].decode('utf-8', 'replace')}")
            ids.append(js["id"])
        self.assertEqual(len(set(ids)), 36)
        type(self).ids = ids

        # 編輯畫面：劇本原樣拿得回來；隱藏設定只有字數
        st, js, data = self.post("/api/editor/get", {"id": ids[0]})
        self.assertEqual(st, 200, data[:300])
        got = js["scenario"]
        self.assertEqual(got["playbook"]["pid"], self.scenarios[0]["playbook"]["pid"])
        self.assertEqual(got["playbook"]["rows"], self.scenarios[0]["playbook"]["rows"])
        self.assertNotIn("server_only", got)
        self.assertGreater(got["hidden"]["persona"], 20)
        self.assertGreater(got["hidden"]["product_facts"], 10)
        self.assertEqual(got["call_direction"], "outgoing")
        self.assertEqual(got["initial_trust"], 60)

        # 清單：標得出哪些有劇本
        st, js, _ = self.post("/api/editor/list", {})
        rows = {r["id"]: r for r in js["scenarios"]}
        self.assertTrue(all(rows[i]["playbook"] for i in ids))
        self.assertFalse(rows["dca-volatility"]["playbook"])

        # 首頁那份：有劇本，沒有隱藏設定，代稱換成實際的字
        st, js, data, _ = self.req("GET", "/api/scenarios", cookie=self.cookie)
        self.assertEqual(st, 200)
        self.assertNotIn(b"server_only", data)
        self.assertNotIn("隱藏".encode("utf-8") + b'":', data)
        mine = next(s for s in js["scenarios"] if s["id"] == ids[0])
        self.assertIn("playbook", mine)
        self.assertNotIn("{業務稱呼}", mine["opening_line"])

    def test_2_keep_or_remove_playbook(self):
        sid = self.ids[1]
        st, js, _ = self.post("/api/editor/get", {"id": sid})
        sc = js["scenario"]
        sc.pop("hidden")
        sc.pop("source")
        pb = sc.pop("playbook")
        # 用進階編輯改別的欄位（沒送劇本、隱藏設定留空）：劇本和隱藏設定都要留著
        sc["first_tip"] = "改過的提示"
        st, js, data = self.post("/api/editor/save", {"id": sid, "scenario": sc})
        self.assertEqual(st, 200, data[:300])
        st, js, _ = self.post("/api/editor/get", {"id": sid})
        self.assertEqual(js["scenario"]["playbook"], pb)
        self.assertEqual(js["scenario"]["first_tip"], "改過的提示")
        self.assertGreater(js["scenario"]["hidden"]["persona"], 20)
        # 明確送 null：拿掉劇本
        sc["playbook"] = None
        st, js, data = self.post("/api/editor/save", {"id": sid, "scenario": sc})
        self.assertEqual(st, 200, data[:300])
        st, js, _ = self.post("/api/editor/get", {"id": sid})
        self.assertNotIn("playbook", js["scenario"])

    def test_3_bad_playbook_rejected(self):
        base = json.loads(json.dumps(self.scenarios[2]))
        bad = [
            ("太大", {"rows": [{"t": "字" * 1400} for _ in range(30)]}),
            ("一段太長", {"t": "字" * 1501}),
            ("層數太多", {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": 1}}}}}}}}),
            ("欄位名稱不對", {"bad key": 1}),
            ("清單太長", {"rows": list(range(81))}),
            ("不是物件", ["x"]),
            ("不是物件", "x"),
        ]
        for name, pb in bad:
            sc = dict(base, playbook=pb)
            st, js, data = self.post("/api/editor/save", {"scenario": sc})
            self.assertEqual(st, 400, f"{name}：{data[:200].decode('utf-8', 'replace')}")
            self.assertEqual(js.get("field"), "playbook", name)

    def test_4_sizes(self):
        biggest = max(len(json.dumps(s["playbook"], ensure_ascii=False, separators=(",", ":"))) for s in self.scenarios)
        self.assertLess(biggest, 24000)


if __name__ == "__main__":
    unittest.main()
