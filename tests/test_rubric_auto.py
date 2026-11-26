"""產品面評分標準不用人選：
1. 有選商品範本的情境，一律跟著範本的商品類型（情境自己存的不算數）。
2. 沒選範本時，用 AI 寫情境會自動選：商品那行字看得出是基金或債券就照它，看不出來才用 AI 判斷的。
3. 從商品那行字判斷的規則，伺服器（server.guess_rubric）和網頁（Arena.guessRubric）要一樣。
全部只連 127.0.0.1，不會用到任何真的金鑰。"""

import http.client
import json
import shutil
import subprocess
import sys
import unittest

import _util
import fake_gemini

APP_SECRET = "app-secret-" + "b" * 40
NODE = shutil.which("node")

# (商品那一行字, 該用哪一版)
SAMPLES = [
    ("海外投資級債券與美元短票", "bond"), ("高盛 20 年期步階票息美元債", "bond"), ("公司債", "bond"), ("美元票券", "bond"),
    ("高收益債券基金", "fund"), ("股票型基金定期定額", "fund"), ("群益0-5年期ESG投資等級公司債ETF證券投資信託基金（00985B）", "fund"),
    ("主動式債券 etf", "fund"), ("00981D", "general"), ("股權連結結構型商品 FCN", "general"), ("手續費折讓", "general"), ("", "general"),
]

PROBE = r'''
import json, sys
sys.path.insert(0, ".")
import server
out = {"guess": [server.guess_rubric(s) for s in json.loads(sys.argv[1])], "names": server.RUBRIC_TYPES}
fund = {"type": "fund", "p": {"name": "某某債券ETF基金"}}
fcn = {"type": "fcn", "p": {"name": "某某 FCN"}}
sc = {"template_id": "tpl-abc123", "product": "隨便", "rubric_type": "bond", "server_only": {}}
out["linked"] = [server.with_template(sc, {"tpl-abc123": t})["rubric_type"] for t in (fund, fcn)] + [server.with_template(sc, {})["rubric_type"], sc["rubric_type"]]
out["schema"] = [server.DRAFT_SCHEMA["properties"]["rubric_type"]["enum"], "rubric_type" in server.DRAFT_SCHEMA["required"],
                 server.DRAFT_EXAMPLE.get("rubric_type"), "rubric_type" in server.DRAFT_PROMPT]
print(json.dumps(out, ensure_ascii=False))
'''

FUND = {"type": "fund", "p": {"name": "某某全球優選收益債券ETF基金", "issuer": "某某投信", "risk": "RR2", "coupon": "每月配息", "risks": "利率風險\n匯率風險"},
        "points": ["把錢交給投信買一籃子公司債"], "qa": [{"q": "配息是不是賺來的？", "a": "不一定，可能來自收益平準金。"}], "donts": ["不能說保本"]}


class LogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = _util.copy_project()
        r = subprocess.run([sys.executable, "-c", PROBE, json.dumps([s for s, _ in SAMPLES])], cwd=str(cls.dir), env=_util.clean_env({}), capture_output=True, timeout=60)
        if r.returncode != 0:
            raise AssertionError(r.stderr.decode("utf-8", "replace")[-1500:])
        cls.out = json.loads(r.stdout.decode("utf-8"))

    def test_guess_from_the_product_text(self):
        self.assertEqual(self.out["guess"], [k for _, k in SAMPLES])
        self.assertEqual(self.out["names"], {"general": "通用", "fund": "基金", "bond": "海外債"})  # 原本叫「一般」，和「一般模式」撞名

    @unittest.skipUnless(NODE, "沒有安裝 node")
    def test_page_uses_the_same_rule(self):
        js = "const A=require('./web/arena.js');console.log(JSON.stringify(JSON.parse(process.argv[1]).map(A.guessRubric)))"
        r = subprocess.run(["node", "-e", js, json.dumps([s for s, _ in SAMPLES])], cwd=str(_util.ROOT), capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(r.stdout.decode("utf-8")), self.out["guess"])

    def test_linked_scenario_follows_the_template(self):
        """情境存的是 bond：範本是基金就用基金版，是 FCN 就用通用版；範本不見了才用情境自己存的。存的那一份不動。"""
        self.assertEqual(self.out["linked"], ["fund", "general", "bond", "bond"])

    def test_ai_is_asked_to_choose(self):
        self.assertEqual(self.out["schema"], [["general", "fund", "bond"], True, "fund", True])


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
        st, _, set_cookie = cls.req(cls, "GET", "/api/status")
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
        return r.status, json.loads(data.decode("utf-8")), r.getheader("Set-Cookie") or ""

    def post(self, path, body):
        st, js, _ = self.req("POST", path, body, self.cookie)
        return st, js

    def gm_control(self, knobs):
        conn = http.client.HTTPConnection("127.0.0.1", int(self.gm.url.rsplit(":", 1)[1]), timeout=10)
        conn.request("POST", "/__control", body=json.dumps(knobs).encode("utf-8"), headers={"Content-Type": "application/json"})
        conn.getresponse().read()
        conn.close()

    def draft(self, product, ai_choice):
        """假 AI 回一份情境草稿（商品和評分標準照指定的），看伺服器最後選哪一版。"""
        st, js = self.post("/api/editor/draft", {"description": "六十五歲退休老師，想把定存轉到每月配息的商品"})
        self.assertEqual(st, 200, js)
        base = js["scenario"]
        made = dict(base, product=product)
        made.pop("rubric_type", None)
        if ai_choice is not None:
            made["rubric_type"] = ai_choice
        self.gm_control({"json": made})
        try:
            st, js = self.post("/api/editor/draft", {"description": "六十五歲退休老師，想把定存轉到每月配息的商品"})
        finally:
            self.gm_control({"json": None})
        self.assertEqual(st, 200, js)
        return js["scenario"]["rubric_type"]

    def test_draft_without_template_picks_by_itself(self):
        # 商品那行字看得出來：照它，AI 選錯也不管
        self.assertEqual(self.draft("海外投資級債券與美元短票", "general"), "bond")
        self.assertEqual(self.draft("高收益債券基金", None), "fund")
        # 看不出來：用 AI 判斷的；AI 沒給或亂給就是通用
        self.assertEqual(self.draft("00981D 募集", "fund"), "fund")
        self.assertEqual(self.draft("理財帳戶服務", None), "general")
        self.assertEqual(self.draft("理財帳戶服務", "stock"), "general")

    def test_linked_scenario_is_scored_with_the_template_type(self):
        st, js = self.post("/api/template/save", {"template": FUND})
        self.assertEqual(st, 200, js)
        tid = js["id"]
        # AI 寫情境：有選範本就跟著範本，AI 選什麼都不算
        st, js = self.post("/api/editor/draft", {"template_id": tid, "description": ""})
        self.assertEqual((st, js["scenario"]["rubric_type"]), (200, "fund"))
        # 存的時候硬選海外債：首頁拿到的還是基金（跟著範本）；拿掉範本才用情境自己存的
        sc = {
            "title": "賠過本金的客戶開口要保本", "product": "隨便寫的商品", "template_id": tid, "rubric_type": "bond",
            "difficulty_level": 3, "duration_min": 10, "call_direction": "outgoing", "initial_trust": 30,
            "customer": {"name": "蘇小姐", "age": 45, "job": "外商財務經理", "avatar": "lin", "summary": "之前買的商品虧到本金，對新商品很防備。"},
            "learner_task": "先處理客戶要保本的期待，再問清資金期限。", "opening_line": "喂，你們有保本的嗎？",
            "checklist": [{"label": "同理虧損經驗", "hint": ""}, {"label": "完整揭露必揭露風險", "hint": ""}],
            "server_only": {"persona": "你是蘇小姐，45 歲，外商公司財務經理，做事有條理，問題很尖銳。", "hidden_info": "有五百萬定存快到期。",
                            "reveal_rules": "業務問資金來源 → 回答：定存快到期。", "behavior": "聽到很安全就冷掉。", "coach_notes": "先同理，再問虧損經過。"},
        }
        st, js = self.post("/api/editor/save", {"scenario": sc})
        self.assertEqual(st, 200, js)
        sid = js["id"]
        pub = next(s for s in self.req("GET", "/api/scenarios", None, self.cookie)[1]["scenarios"] if s["id"] == sid)
        self.assertEqual(pub["rubric_type"], "fund")
        self.assertEqual(self.post("/api/template/delete", {"id": tid})[0], 200)
        pub = next(s for s in self.req("GET", "/api/scenarios", None, self.cookie)[1]["scenarios"] if s["id"] == sid)
        self.assertEqual(pub["rubric_type"], "bond")


if __name__ == "__main__":
    unittest.main()
