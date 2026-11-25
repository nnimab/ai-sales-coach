"""商品範本（一檔商品一份：商品資料＋給教練的標準＋審核狀態）：
1. 存、讀、改、刪（/api/template/*，雲端 app.py、假的 Gemini）：內容有改就回到「未審核」，審核通過要填審核人；刪掉是進垃圾桶。
2. AI 寫範本草稿（/api/template/draft）：講到的數字要在商品資料裡找得到，業務要說的話不能有保證獲利這類說法。
3. 欄位名稱要和網頁（web/arena.js 的 FIELDS）一樣；本機版存成 templates.json。
全部只連 127.0.0.1，不會用到任何真的金鑰。"""

import http.client
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _util
import fake_gemini

APP_SECRET = "app-secret-" + "a" * 40
NODE = shutil.which("node")

FUND = {
    "type": "fund",
    "p": {"name": "某某全球優選收益債券ETF基金", "issuer": "某某投信", "code": "00999B", "ccy": "新臺幣", "risk": "RR2",
          "coupon": "成立滿45日後每月配息，配息來源可能為收益平準金", "tenor": "指數股票型", "call": "經理費 0.20%，保管費 0.05%",
          "extra": "發行價格新臺幣10元", "strike": "基金不會有履約價", "risks": "1. 利率風險\n- 匯率風險\n\n匯率風險\n信用風險"},
    "points": ["把錢交給投信，買一籃子公司債，每個月配息一次", "債券價格會跟著利率漲跌，本金可能虧損"],
    "qa": [{"q": "配息是不是都是賺來的？", "a": "不一定，配息來源可能是收益平準金，配息不代表報酬。"}, {"q": "", "a": ""}],
    "donts": "不能說保本\n不能把配息說成獲利",
    "source": "基金簡報.pdf",
}


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
        st, js, _, _ = self.req("POST", path, body, self.cookie)
        return st, js

    def gm_control(self, knobs):
        conn = http.client.HTTPConnection("127.0.0.1", int(self.gm.url.rsplit(":", 1)[1]), timeout=10)
        conn.request("POST", "/__control", body=json.dumps(knobs).encode("utf-8"), headers={"Content-Type": "application/json"})
        conn.getresponse().read()
        conn.close()

    def test_1_save_get_list(self):
        st, js = self.post("/api/template/list", {})
        self.assertEqual((st, js["templates"]), (200, []))
        st, js = self.post("/api/template/save", {"template": FUND})
        self.assertEqual(st, 200, js)
        tid = js["id"]
        self.assertRegex(tid, r"^tpl-[a-z0-9]{6}$")
        t = js["template"]
        self.assertEqual(t["review"], {"status": "draft", "by": "", "at": ""})  # 新的一律未審核
        self.assertEqual(t["p"]["strike"], "")  # 基金沒有履約價這一格：不存
        self.assertEqual(t["p"]["risks"], "利率風險\n匯率風險\n信用風險")  # 編號拿掉、空的和重複的不要
        self.assertEqual(t["qa"], [FUND["qa"][0]])  # 兩邊都空的那一組不要
        self.assertEqual(t["donts"], ["不能說保本", "不能把配息說成獲利"])  # 一行一條的文字也收
        self.assertEqual(t["source"], "基金簡報.pdf")
        st, js = self.post("/api/template/get", {"id": tid})
        self.assertEqual(st, 200)
        self.assertEqual(js["template"]["id"], tid)
        self.assertEqual(js["template"]["points"], FUND["points"])
        st, js = self.post("/api/template/list", {})
        row = js["templates"][0]
        self.assertEqual((row["id"], row["name"], row["type"], row["risk"], row["code"]), (tid, FUND["p"]["name"], "fund", "RR2", "00999B"))
        self.assertEqual((row["points"], row["qa"], row["donts"], row["review"]["status"]), (2, 1, 2, "draft"))
        type(self).tid = tid

    def test_2_review_follows_content(self):
        tid = self.tid
        saved = self.post("/api/template/get", {"id": tid})[1]["template"]
        body = {k: saved[k] for k in ("type", "p", "points", "qa", "donts")}
        # 審核通過要填審核人
        st, js = self.post("/api/template/save", {"id": tid, "template": body, "approve": True, "by": "  "})
        self.assertEqual((st, js["field"]), (400, "by"))
        st, js = self.post("/api/template/save", {"id": tid, "template": body, "approve": True, "by": "王主管"})
        self.assertEqual(st, 200, js)
        review = js["template"]["review"]
        self.assertEqual((review["status"], review["by"]), ("approved", "王主管"))
        self.assertRegex(review["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        # 沒改內容再存一次：還是已審核
        st, js = self.post("/api/template/save", {"id": tid, "template": body})
        self.assertEqual(js["template"]["review"], review)
        # 改了一個字：回到未審核
        changed = dict(body, donts=body["donts"] + ["不能拿配息跟定存比"])
        st, js = self.post("/api/template/save", {"id": tid, "template": changed})
        self.assertEqual(js["template"]["review"], {"status": "draft", "by": "", "at": ""})
        self.assertEqual(self.post("/api/template/list", {})[1]["templates"][0]["review"]["status"], "draft")
        self.assertEqual(js["template"]["source"], "基金簡報.pdf")  # 沒再送來源檔名：沿用原本的

    def test_3_bad_input(self):
        for patch, field in (({"type": "stock"}, "type"), ({"p": {"name": "A"}}, "p.name"), ({"p": dict(FUND["p"], risk="RR9")}, "p.risk"),
                             ({"p": dict(FUND["p"], coupon="長" * 201)}, "p.coupon"), ({"qa": [{"q": "只有問題", "a": ""}]}, "qa.0.a"),
                             ({"points": ["短"] * 0 + ["長" * 121]}, "points.0"), ({"donts": [f"第 {i} 條" for i in range(11)]}, "donts")):
            st, js = self.post("/api/template/save", {"template": dict(FUND, **patch)})
            self.assertEqual((st, js.get("field")), (400, field), patch)
        self.assertEqual(self.post("/api/template/save", {"template": "x"})[0], 400)
        self.assertEqual(self.post("/api/template/save", {"id": "tpl-nothere", "template": FUND})[0], 404)
        self.assertEqual(self.post("/api/template/get", {"id": "../x"})[0], 404)
        # 大括號拿掉（放進情境時大括號是代稱專用）
        st, js = self.post("/api/template/save", {"template": dict(FUND, points=["請{業務稱呼}先說明{公司}的規定"])})
        self.assertEqual(js["template"]["points"], ["請業務稱呼先說明公司的規定"])
        type(self).extra = js["id"]

    def test_4_ai_draft(self):
        body = {"template": {"type": "fund", "p": FUND["p"]}, "text": "本基金成立日起滿45日(含)後，採每月配息。經理費 0.20%。"}
        # 假 AI 照 schema 只會回空清單：當成沒寫出來
        st, js = self.post("/api/template/draft", body)
        self.assertEqual(st, 502, js)
        self.gm_control({"json": {
            "points": ["這是把錢交給投信買一籃子公司債，每月配息一次", "保證獲利，放著一定會賺", "年化配息率 7.5% 很穩"],
            "qa": [{"q": "配息會不會配到本金？", "a": "有可能，配息來源可能是收益平準金，我不能保證配息金額。"},
                   {"q": "會不會賠？", "a": "這檔很穩，不會賠啦。"},
                   {"q": "費用多少？", "a": "經理費 0.20%，保管費 0.05%。"},
                   {"q": "費用多少？", "a": "經理費大概 1.5%。"},
                   {"q": "只有問題", "a": ""}],
            "donts": ["不能說保證獲利", "不能說配息率 8%", "不能把收益平準金的配息說成獲利"] + ["不能說的話" + c for c in "甲乙丙丁戊己庚辛壬癸子丑"],
            "p": {"name": "AI 想改商品名稱"}, "type": "bond",
        }})
        try:
            st, js = self.post("/api/template/draft", body)
            self.assertEqual(st, 200, js)
            d = js["draft"]
            self.assertEqual(set(d), {"points", "qa", "donts"})  # 只回這三樣，商品資料不會被 AI 改
            self.assertEqual(d["points"], ["這是把錢交給投信買一籃子公司債，每月配息一次"])  # 保證獲利、資料裡沒有的 7.5% 都不收
            self.assertEqual([x["q"] for x in d["qa"]], ["配息會不會配到本金？", "費用多少？"])  # 「不會賠」「1.5%」不收；「不能保證」是否定，可以
            self.assertEqual(d["qa"][1]["a"], "經理費 0.20%，保管費 0.05%。")
            self.assertEqual(d["donts"][:2], ["不能說保證獲利", "不能把收益平準金的配息說成獲利"])  # 不能說的話本來就會提到禁語；8% 資料裡沒有
            self.assertEqual(len(d["donts"]), 9)  # 先留前 10 條，再拿掉講到 8% 的那一條
            self.assertTrue(js["model"])
            self.assertEqual(self.post("/api/template/draft", {"template": {"type": "fund", "p": {"name": ""}}})[0], 400)
            self.gm_control({"busy": 503})
            st, js = self.post("/api/template/draft", body)
            self.assertEqual(st, 502)
            self.assertTrue(js["error"])
        finally:
            self.gm_control({"busy": 0, "json": None})

    def test_5_delete_goes_to_trash(self):
        st, js = self.post("/api/template/delete", {"id": self.extra})
        self.assertEqual((st, js), (200, {"ok": True}))
        self.assertEqual(self.post("/api/template/get", {"id": self.extra})[0], 404)
        self.assertEqual(self.post("/api/template/delete", {"id": self.extra})[0], 404)
        self.assertEqual([r["id"] for r in self.post("/api/template/list", {})[1]["templates"]], [self.tid])

    # ---- 對練情境連到範本

    def public(self, sid):
        st, js, _, _ = self.req("GET", "/api/scenarios", None, self.cookie)
        self.assertEqual(st, 200)
        return next(s for s in js["scenarios"] if s["id"] == sid)

    def test_6_ai_writes_scenario_for_a_template(self):
        tid = self.tid
        # 有選範本：描述可以不填；商品、評分標準、刁難度由範本和講師決定，不是 AI
        st, js = self.post("/api/editor/draft", {"template_id": tid, "description": "", "difficulty_level": 3})
        self.assertEqual(st, 200, js)
        sc = js["scenario"]
        self.assertEqual((sc["template_id"], sc["product"], sc["rubric_type"]), (tid, FUND["p"]["name"][:30], "fund"))
        self.assertEqual((sc["difficulty_level"], sc["difficulty"]), (3, "困難"))
        self.assertEqual(self.post("/api/editor/draft", {"template_id": "tpl-nothere", "description": ""})[0], 404)
        self.assertEqual(self.post("/api/editor/draft", {"description": "太短"})[0], 400)  # 沒選範本：照舊要寫描述
        self.assertEqual(self.post("/api/editor/draft", {"template_id": tid, "description": "太短"})[0], 400)  # 有寫就要寫夠
        st, js = self.post("/api/editor/draft", {"description": "六十五歲退休老師，想把定存轉到每月配息的商品"})
        self.assertEqual(st, 200)
        self.assertNotIn("template_id", js["scenario"])

    def test_7_scenario_follows_its_template(self):
        tid = self.tid
        sc = {
            "title": "賠過本金的客戶開口要保本", "product": "隨便寫的商品", "template_id": tid, "rubric_type": "fund",
            "difficulty_level": 3, "duration_min": 10, "call_direction": "outgoing", "initial_trust": 30,
            "customer": {"name": "蘇小姐", "age": 45, "job": "外商財務經理", "avatar": "lin", "summary": "之前買的商品虧到本金，對新商品很防備。"},
            "learner_task": "先處理客戶要保本的期待，再問清資金期限。", "opening_line": "喂，你們有保本的嗎？",
            "checklist": [{"label": "同理虧損經驗", "hint": ""}, {"label": "完整揭露必揭露風險", "hint": ""}],
            "server_only": {"persona": "你是蘇小姐，45 歲，外商公司財務經理，做事有條理，問題很尖銳。", "hidden_info": "有五百萬定存快到期。",
                            "reveal_rules": "業務問資金來源 → 回答：定存快到期。", "behavior": "聽到很安全就冷掉。", "coach_notes": "先同理，再問虧損經過。"},
            "script": [{"s": "同理破冰", "w": "A", "t": "蘇小姐您好，我是{業務稱呼}。"}, {"s": "亂寫的階段", "w": "X", "t": "你們有保本的嗎？"}, {"w": "A", "t": ""}, "x"],
        }
        st, js = self.post("/api/editor/save", {"scenario": sc})
        self.assertEqual(st, 200, js)
        sid = js["id"]
        pub = self.public(sid)
        self.assertEqual(pub["template"], {"id": tid, "name": FUND["p"]["name"], "status": "draft"})  # 範本還沒審核：首頁會標
        self.assertEqual(pub["script"][1], {"s": "", "w": "C", "t": "你們有保本的嗎？"})  # 不認得的階段清掉、不是業務就是客戶、空的不要
        self.assertEqual(len(pub["script"]), 2)
        self.assertNotIn("{", pub["script"][0]["t"])  # 首頁拿到的已經換成實際的稱呼
        self.assertNotIn("server_only", pub)
        row = next(r for r in self.post("/api/editor/list", {})[1]["scenarios"] if r["id"] == sid)
        self.assertEqual((row["template_id"], row["script"]), (tid, True))
        self.assertEqual(self.post("/api/template/list", {})[1]["templates"][0]["scenarios"], 1)
        # 範本審核通過：底下的情境馬上跟著變，不用重存
        saved = self.post("/api/template/get", {"id": tid})[1]["template"]
        body = {k: saved[k] for k in ("type", "p", "points", "qa", "donts")}
        self.assertEqual(self.post("/api/template/save", {"id": tid, "template": body, "approve": True, "by": "王主管"})[0], 200)
        self.assertEqual(self.public(sid)["template"]["status"], "approved")
        # 用進階編輯改別的欄位（沒送 template_id 和 script）：兩樣都還在；送空的就是拿掉
        st, js = self.post("/api/editor/save", {"id": sid, "scenario": {k: v for k, v in sc.items() if k not in ("template_id", "script")}})
        self.assertEqual(st, 200, js)
        self.assertEqual((self.public(sid)["template_id"], len(self.public(sid)["script"])), (tid, 2))
        type(self).sid, type(self).sc = sid, sc

    def test_8_ideas_and_script(self):
        tid, sid, sc = self.tid, self.sid, self.sc
        self.assertEqual(self.post("/api/editor/ideas", {"template_id": "tpl-nothere"})[0], 404)
        self.assertEqual(self.post("/api/editor/ideas", {"template_id": tid})[0], 502)  # 假 AI 只會回「好」：描述太短，不算
        self.gm_control({"json": {"ideas": [
            {"label": "賠過本金、開口要保本", "desc": "蘇小姐，45 歲外商財務經理，定存五百萬快到期，之前虧過本金。", "difficulty": "困難"},
            {"label": "賠過本金、開口要保本", "desc": "同一個類型再來一次，這一筆不要。", "difficulty": "困難"},
            {"label": "剛退休想月月領息", "desc": "張老師，65 歲退休教師，想把退休金的一部分換成每月配息。", "difficulty": "亂寫"},
            {"label": "", "desc": "沒有類型名稱的這一筆不要。", "difficulty": "簡單"}, "x",
        ], "rows": [
            {"stage": "同理破冰", "who": "A", "text": "蘇小姐您好，耽誤您三分鐘可以嗎？"}, {"stage": "同理破冰", "who": "C", "text": "你可以保證不會賠嗎？"},
            {"stage": "白話說明", "who": "A", "text": "我不能保證。這檔每月配息，經理費 0.20%，配息來源可能是收益平準金。"},
            {"stage": "痛點迎擊", "who": "C", "text": "那我再想想。"}, {"stage": "不存在的階段", "who": "A", "text": "好，資料我寄給您。"},
        ]}})
        try:
            st, js = self.post("/api/editor/ideas", {"template_id": tid})
            self.assertEqual(st, 200, js)
            self.assertEqual([(x["label"], x["difficulty_level"]) for x in js["ideas"]], [("賠過本金、開口要保本", 3), ("剛退休想月月領息", 2)])
            # 範本對話：客戶可以問「保證不會賠嗎」，業務說「我不能保證」也可以；經理費 0.20% 範本裡有
            body = {"id": sid, "scenario": {k: v for k, v in sc.items() if k != "server_only"}}  # 隱藏設定沒送：用存好的補上
            st, js = self.post("/api/editor/script", body)
            self.assertEqual(st, 200, js)
            self.assertEqual([(r["s"], r["w"]) for r in js["script"]], [("同理破冰", "A"), ("同理破冰", "C"), ("白話說明", "A"), ("痛點迎擊", "C"), ("", "A")])
            for bad, word in (("這檔很穩，保證獲利。", "不能說的話"), ("年化配息率 7.5%，很不錯。", "數字")):
                self.gm_control({"json": {"rows": [{"stage": "白話說明", "who": "A", "text": bad}] * 5}})
                st, js = self.post("/api/editor/script", body)
                self.assertEqual(st, 502, bad)
                self.assertIn(word, js["error"])
            self.assertEqual(self.post("/api/editor/script", {"scenario": dict(sc, template_id="")})[0], 400)  # 沒選範本
            self.assertEqual(self.post("/api/editor/script", {"scenario": dict(sc, server_only={})})[0], 400)  # 客戶人設還沒寫
        finally:
            self.gm_control({"json": None})

    def test_9_template_deleted_scenario_keeps_working(self):
        self.assertEqual(self.post("/api/template/delete", {"id": self.tid})[0], 200)
        self.assertEqual(self.public(self.sid)["template"], {"id": self.tid, "missing": True})  # 情境還在，只是標成範本不見了


class EmptyAppTests(unittest.TestCase):
    """內建情境已經從 App 拿掉：一個情境都沒有的時候，網站要照樣開得起來，可以從範本開始建。"""

    @classmethod
    def setUpClass(cls):
        cls.dir = _util.copy_project()
        for f in (cls.dir / "scenarios").glob("*.json"):  # 測試用的專案會帶 samples 當內建情境：這裡拿掉，跟線上一樣
            f.unlink()
        cls.port = _util.free_port()
        env = _util.clean_env({"APP_MODE": "cloud", "AUTH_MODE": "open", "APP_SECRET": APP_SECRET, "ALLOWED_HOSTS": "127.0.0.1",
                               "LLM_PROVIDER": "gemini", "LIVE_ENABLED": "0", "GEMINI_API_KEY": "fake-gemini-key"})
        cls.proc = _util.start_proc(["app.py", "--port", str(cls.port)], cls.dir, env, cls.dir / "cloud.log")
        if not _util.wait_port(cls.port, 25):
            cls.tearDownClass()
            raise AssertionError("app.py 沒有啟動：" + (cls.dir / "cloud.log").read_text("utf-8", "replace")[-800:])

    @classmethod
    def tearDownClass(cls):
        _util.stop_proc(getattr(cls, "proc", None))

    def call(self, method, path, body=None, cookie=""):
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
        return r.status, json.loads(data.decode("utf-8")), (r.getheader("Set-Cookie") or "").split(";", 1)[0]

    def test_site_works_with_no_scenarios(self):
        st, js, cookie = self.call("GET", "/api/status")
        self.assertEqual(st, 200, js)
        self.assertEqual(self.call("GET", "/api/scenarios", None, cookie)[:2], (200, {"scenarios": []}))
        st, js, _ = self.call("POST", "/api/editor/list", {}, cookie)
        self.assertEqual((st, js["scenarios"]), (200, []))
        self.assertEqual(self.call("POST", "/api/template/list", {}, cookie)[:2], (200, {"templates": []}))
        st, js, _ = self.call("POST", "/api/template/save", {"template": FUND}, cookie)
        self.assertEqual(st, 200, js)

    def test_repo_ships_no_builtin_scenarios(self):
        self.assertEqual([f.name for f in (_util.ROOT / "scenarios").glob("*.json")], [])  # 這一層放了就會變成線上的內建情境
        self.assertGreaterEqual(len(list((_util.ROOT / "scenarios" / "samples").glob("*.json"))), 15)  # 測試用的還在
        self.assertNotIn("samples", (_util.ROOT / "vercel.json").read_text("utf-8"))  # samples 不部署


PROBE = r'''
import json, sys
sys.path.insert(0, ".")
import server

out = {"labels": {k: [list(x) for x in v] for k, v in server.PRODUCT_LABELS.items()}, "types": server.PRODUCT_TYPE_LABELS}
t = server.sanitize_template({
    "type": "fcn",
    "p": {"name": "美元計價 6 個月期 FCN", "issuer": "某某銀行", "under": "NVDA、TSLA", "coupon": "年化 12%，每月配息", "ki": "期初價格的 65%",
          "risk": "rr 4", "risks": ["非保本", "發行機構信用風險"]},
    "points": "1. 固定配息，但本金不保本\n2) 到期可能拿到股票", "qa": [{"q": "跌破會怎樣？", "a": "可能要用履約價承接股票。"}], "donts": ["不能說保本"],
})
out["facts"] = server.template_facts(t)
out["coach"] = server.template_coach(t)
out["risk"] = t["p"]["risk"]
out["points"] = t["points"]
out["banned"] = [server.says_banned(s) for s in (
    "這檔保證獲利", "我不能保證獲利", "放著一定會賺", "不會賠啦", "這不是零風險的商品", "跟定存一樣安全", "不能說跟定存一樣安全", "配息不代表報酬")]
loose = server.sanitize_template({"type": "nope", "p": {"name": "某某商品", "coupon": "長" * 300, "risk": "高"}, "points": ["短"] * 3 + ["長" * 200],
                                  "qa": [{"q": "只有問題"}, "x", {"q": "問", "a": "答"}], "donts": [str(i) + " 號" for i in range(20)]}, strict=False)
out["loose"] = [loose["type"], len(loose["p"]["coupon"]), loose["p"]["risk"], [len(s) for s in loose["points"]], loose["qa"], len(loose["donts"])]
sc = {"template_id": "tpl-abc123", "server_only": {"coach_notes": "先同理。", "product_facts": "情境自己存的舊資料"}}
linked = server.with_template(sc, {"tpl-abc123": t})
out["linked"] = [linked["server_only"]["product_facts"] == server.template_facts(t), linked["server_only"]["coach_notes"],
                 sc["server_only"]["product_facts"], server.with_template(sc, {}) is sc]
store = server.storage.FileStore(sys.argv[1])
store.put_doc("templates", {"items": {"tpl-abc123": t}, "trash": {}})
out["file"] = sorted(p.name for p in __import__("pathlib").Path(sys.argv[1]).iterdir())
out["read_back"] = store.get_doc("templates")["items"]["tpl-abc123"]["p"]["name"]
print(json.dumps(out, ensure_ascii=False))
'''


class LogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = _util.copy_project()
        cls.tmp = tempfile.mkdtemp(prefix="tpl-", dir=str(cls.dir))
        r = subprocess.run([sys.executable, "-c", PROBE, cls.tmp], cwd=str(cls.dir), env=_util.clean_env({}), capture_output=True, timeout=60)
        if r.returncode != 0:
            raise AssertionError(r.stderr.decode("utf-8", "replace")[-1500:])
        cls.out = json.loads(r.stdout.decode("utf-8"))

    @unittest.skipUnless(NODE, "沒有安裝 node")
    def test_labels_match_the_page(self):
        js = "const A=require('./web/arena.js');console.log(JSON.stringify({f:A.FIELDS,t:A.TYPES}))"
        r = subprocess.run(["node", "-e", js], cwd=str(_util.ROOT), capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        page = json.loads(r.stdout.decode("utf-8"))
        for ptype, rows in page["f"].items():
            self.assertEqual([x for x in rows if x[0] != "risks"], self.out["labels"][ptype], ptype)  # 必揭露風險另外放，其他要一模一樣
        self.assertEqual(page["t"], self.out["types"])

    def test_facts_and_coach_text(self):
        facts = self.out["facts"].split("\n")
        self.assertEqual(facts[0], "【商品】美元計價 6 個月期 FCN（FCN）")
        self.assertIn("連結標的：NVDA、TSLA", facts)
        self.assertIn("下限價（KI）：期初價格的 65%", facts)
        self.assertFalse([s for s in facts if s.startswith("履約價")])  # 沒填的欄位不列
        self.assertEqual(facts[facts.index("【必揭露風險】") + 1:facts.index("【白話重點】")], ["1. 非保本", "2. 發行機構信用風險"])
        self.assertEqual(self.out["points"], ["固定配息，但本金不保本", "到期可能拿到股票"])  # 開頭的編號拿掉
        self.assertEqual(self.out["risk"], "RR4")
        self.assertIn("- 客戶：跌破會怎樣？\n  建議回應：可能要用履約價承接股票。", self.out["coach"])
        self.assertIn("【這檔商品不能說的話】\n- 不能說保本", self.out["coach"])

    def test_linked_scenario_uses_the_template(self):
        """對練時：商品資料換成範本的，教練重點後面接上範本的建議回應和不能說的話；情境存的那一份不動。範本不在就用情境自己的。"""
        same_facts, coach, kept, untouched = self.out["linked"]
        self.assertTrue(same_facts)
        self.assertTrue(coach.startswith("先同理。\n【客戶常見的質疑和建議回應】"), coach)
        self.assertIn("【這檔商品不能說的話】", coach)
        self.assertEqual(kept, "情境自己存的舊資料")
        self.assertTrue(untouched)

    def test_banned_phrases(self):
        self.assertEqual(self.out["banned"], [True, False, True, True, False, True, False, False])

    def test_loose_mode_trims_instead_of_failing(self):
        self.assertEqual(self.out["loose"], ["custom", 200, "", [1, 120], [{"q": "問", "a": "答"}], 10])

    def test_local_store_keeps_templates_in_one_file(self):
        self.assertIn("templates.json", self.out["file"])
        self.assertEqual(self.out["read_back"], "美元計價 6 個月期 FCN")


if __name__ == "__main__":
    unittest.main()
