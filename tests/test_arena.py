"""「新增情境」三步驟（web/arena.js）和情境裡的劇本（playbook）：
1. 網頁的純邏輯：node 跑 tests/js/arena_test.js（沒裝 node 就略過）。
2. 三種人設 × 四種商品（FCN、海外債、基金、自訂）× 三種刁難度轉出來的 36 個情境，真的送去伺服器存（雲端 app.py、假的 Gemini），每一個都要收得下。
3. 劇本跟著情境存、清單和編輯畫面拿得到；沒送就沿用、送 null 就拿掉；太大或格式不對會被擋；隱藏設定照樣不外洩。
4. 上傳商品檔：AI 把文件的文字整理成欄位（/api/editor/product）；代號、風險等級、條件裡的數字在文件裡找不到就不收。
全部只連 127.0.0.1，不會用到任何真的金鑰。"""

import http.client
import json
import re
import shutil
import subprocess
import sys
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

    def test_5_ai_reads_product_text(self):
        """上傳商品檔：瀏覽器送文字上來，AI（這裡是假的 Gemini）整理成欄位。"""
        doc = "商品名稱：美元計價 6 個月期 FCN\n連結標的：NVDA、TSLA\n固定配息率：年化 12%\n風險等級 RR4"
        st, js, data = self.post("/api/editor/product", {"text": doc, "filename": "FCN條件書.pdf"})
        self.assertEqual(st, 200, data[:300])
        p = js["product"]
        self.assertEqual(set(p), set(FIELD_KEYS) | {"type"})  # 欄位和網頁的一樣，不多不少
        self.assertEqual(p["type"], "fcn")
        self.assertTrue(p["name"])
        self.assertEqual(p["code"], "")  # 假 AI 回的代號不在文件裡：不收
        self.assertEqual(p["risk"], "")
        self.assertTrue(all(isinstance(v, str) for v in p.values()))
        self.assertTrue(js["model"])
        # 新格式：products 是清單（假 AI 回的四筆名稱都一樣，同名的只留第一筆）；product 是第一筆，給還開著舊版網頁的人用
        self.assertEqual(js["products"], [p])
        # 文字太少、沒給文字：不問 AI
        for body in ({"text": "太短", "filename": "a.txt"}, {"filename": "a.txt"}, {"text": ["x"] * 50}, {"text": " \n " * 200}):
            st, js, data = self.post("/api/editor/product", body)
            self.assertEqual(st, 400, data[:200])
        # 很長的文件：只送前面一段，照樣有結果
        st, js, data = self.post("/api/editor/product", {"text": doc + "字" * 60000, "filename": "x" * 500})
        self.assertEqual(st, 200, data[:300])
        # AI 塞車：回報原因，不是當掉
        self.gm_control({"busy": 503})
        try:
            st, js, data = self.post("/api/editor/product", {"text": doc, "filename": "a.txt"})
            self.assertEqual(st, 502, data[:300])
            self.assertTrue(js.get("error"))
        finally:
            self.gm_control({"busy": 0})

    def gm_control(self, knobs):
        u = http.client.HTTPConnection("127.0.0.1", int(self.gm.url.rsplit(":", 1)[1]), timeout=10)
        u.request("POST", "/__control", body=json.dumps(knobs).encode("utf-8"), headers={"Content-Type": "application/json"})
        u.getresponse().read()
        u.close()


FIELD_KEYS = re.search(r"const FK = \[([^\]]+)\]", (_util.ROOT / "web" / "arena.js").read_text("utf-8")).group(1)
FIELD_KEYS = re.findall(r"'([a-z]+)'", FIELD_KEYS)

PROBE = r'''
import json, sys
sys.path.insert(0, ".")
import server

doc = """美元計價 6 個月期 FCN 條件書
ISIN：XS1234567890   風險等級：RR 4
連結標的：NVDA、TSLA
配息率：年化 12.00%，每月配息
天期：6 個月（到期日 2027年5月20日）
履約價：期初價格的 85%　下限價：期初價格的 ６５％
最低申購金額 USD 10,000"""
ai = {
    "type": "fcn", "name": "美元計價 6 個月期 FCN\n", "issuer": "", "code": "XS1234567890", "ccy": "美元 USD", "risk": "rr4",
    "under": "NVDA、TSLA", "coupon": "年化 12%，每月配息", "tenor": "6 個月（2027/05/20 到期）", "strike": "期初價格的 85%",
    "rating": "", "call": "每月觀察，全部標的高於期初價 100% 就提前出場", "ki": "期初價格的 65%", "extra": "最低申購金額 USD 10000",
    "risks": ["非保本", "非保本", "", 5, "匯率風險" * 60] + ["風險 %d" % i for i in range(20)],
}
out = {"keys": list(server.PRODUCT_KEYS) + ["risks"], "types": list(server.PRODUCT_TYPES)}
out["ok"] = server.clean_product(ai, "FCN.pdf\n" + doc)
out["made_up"] = server.clean_product(dict(ai, type="stock", code="US0000000000", risk="RR2", coupon="年化 15%", ki="期初價格的 60%",
                                       name={"x": 1}, risks="不是清單"), "FCN.pdf\n" + doc)
try:
    server.clean_product(["x"], doc)
    out["not_dict"] = "accepted"
except server.Invalid:
    out["not_dict"] = "rejected"
item = server.PRODUCT_ITEM_SCHEMA
out["schema"] = sorted(item["required"]) == sorted(item["properties"]) and server.PRODUCT_SCHEMA["properties"]["products"]["items"] is item
many = {"products": [
    dict(ai, name="主動測試動態入息"), dict(ai, name="主動測試複合收益", risk="RR2"), dict(ai, name="主動測試動態入息", issuer="重複的"),
    {"type": "fund", "name": "", "risks": []}, "x", None,
] + [dict(ai, name="第 %d 檔" % i) for i in range(9)]}
got = server.clean_products(many, "FCN.pdf\n" + doc)
out["many"] = [(p["name"], p["risk"]) for p in got]
out["old_shape"] = [p["name"] for p in server.clean_products(ai, "FCN.pdf\n" + doc)]
out["empty"] = server.clean_products({"products": []}, doc)
bad = []
for x in (["x"], {"products": "x"}, {"nothing": 1}):
    try:
        server.clean_products(x, doc)
        bad.append("accepted")
    except server.Invalid:
        bad.append("rejected")
out["bad"] = bad
out["limits"] = [server.PRODUCT_TEXT_MAX, server.PRODUCT_MAX_ITEMS]
fund_doc = """某某全球優選收益債券ETF基金 基金小檔案
基金類型 指數股票型　計價幣別 新臺幣　風險收益等級 RR2
收益分配 本基金成立日起滿45日(含)後，採每月配息，配息來源可能為收益平準金
經理費 100億以上 0.20%；100億(含)以下 0.25%　保管費 0.05%
本基金上市日前（不含當日），經理公司不接受本基金受益權單位數之買回。本基金上市後之買賣成交價格並無升降幅度限制。
本基金並無受存款保險、保險安定基金或其他相關保障機制之保障，基金投資可能發生部分或全部本金之損失，投資人須自負盈虧。
基金配息不代表基金實際報酬，且過去配息不代表未來配息；基金淨值可能因市場因素而上下波動。投資一定有風險，基金投資有賺有賠，申購前應詳閱公開說明書。"""
fund_ai = {
    "type": "fund", "name": "某某全球優選收益債券ETF基金", "ccy": "新臺幣", "risk": "RR2",
    "coupon": "成立滿45日後每月配息，配息不代表實際報酬",
    "tenor": "指數股票型，無閉鎖期（上市日前不含當日不接受買回）",
    "call": "上市日前不接受買回；經理費 0.20%／0.25%（依規模不同），保管費 0.05%，無升降幅度限制",
    "extra": "不保本", "risks": ["無存款保險保障", "不保本"],
}
p = server.clean_product(fund_ai, "基金.pdf\n" + fund_doc)
out["stated"] = [p["tenor"], p["call"], p["extra"], p["coupon"], p["risks"]]
out["stated_english"] = server.clean_product({"type": "fcn", "name": "USD FCN", "extra": "不保本，無閉鎖期", "risks": []},
                                             "USD 6-Month Fixed Coupon Note. Not principal protected. " * 5)["extra"]
out["stated_pieces"] = [server._drop_unstated(t, "非保本商品") for t in ("股票型（無閉鎖期）", "無提前贖回，但有閉鎖期", "（無下限價）月配", "甲，無乙丙，丁（無戊己，庚）", "不保本，無閉鎖期")]
out["prompt_rules"] = [s for s in ("無閉鎖期", "不保本", "經理費", "寧可空著") if s not in server.PRODUCT_PROMPT]
out["in_prompt"] =[k for k in out["keys"] + ["type"] if ("- " + k + "：") not in server.PRODUCT_PROMPT]
print(json.dumps(out, ensure_ascii=False))
'''


class ProductCleanTests(unittest.TestCase):
    """AI 整理出來的商品欄位：代號、風險等級、條件裡的數字，文件裡找不到就不收。"""

    @classmethod
    def setUpClass(cls):
        cls.dir = _util.copy_project()
        r = subprocess.run([sys.executable, "-c", PROBE], cwd=str(cls.dir), env=_util.clean_env({}), capture_output=True, timeout=60)
        if r.returncode != 0:
            raise AssertionError(r.stderr.decode("utf-8", "replace")[-1500:])
        cls.out = json.loads(r.stdout.decode("utf-8"))

    def test_fields_match_the_page(self):
        self.assertEqual(sorted(self.out["keys"]), sorted(FIELD_KEYS))
        self.assertEqual(self.out["types"], ["fcn", "bond", "fund", "custom"])
        self.assertTrue(self.out["schema"])
        self.assertEqual(self.out["in_prompt"], [])

    def test_values_found_in_document_are_kept(self):
        p = self.out["ok"]
        self.assertEqual(p["type"], "fcn")
        self.assertEqual(p["name"], "美元計價 6 個月期 FCN")
        self.assertEqual(p["code"], "XS1234567890")
        self.assertEqual(p["risk"], "RR4")  # 文件寫「RR 4」、AI 回「rr4」
        self.assertEqual(p["coupon"], "年化 12%，每月配息")  # 文件是 12.00%
        self.assertEqual(p["tenor"], "6 個月（2027/05/20 到期）")  # 文件是 2027年5月20日
        self.assertEqual(p["ki"], "期初價格的 65%")  # 文件是全形的 ６５％
        self.assertEqual(p["extra"], "最低申購金額 USD 10000")
        self.assertEqual(p["call"], "")  # 「100%」文件裡沒有：整格不收
        lines = p["risks"].split("\n")
        self.assertEqual(lines[0], "非保本")
        self.assertEqual(len(lines), 8)
        self.assertEqual(len(set(lines)), 8)
        self.assertTrue(all(len(x) <= 120 for x in lines))

    def test_several_products(self):
        """文件裡有好幾檔商品：每檔各一筆，同名的只留第一筆、空的不要、最多六筆；每一筆照樣檢查數字。"""
        self.assertEqual(self.out["many"][:2], [["主動測試動態入息", "RR4"], ["主動測試複合收益", ""]])  # RR2 文件裡沒有：不收
        self.assertEqual(len(self.out["many"]), 6)
        self.assertEqual(len({n for n, _ in self.out["many"]}), 6)
        self.assertEqual(self.out["old_shape"], ["美元計價 6 個月期 FCN"])  # AI 直接回一檔的欄位（舊格式）也收
        self.assertEqual(self.out["empty"], [])
        self.assertEqual(self.out["bad"], ["rejected", "rejected", "rejected"])
        self.assertEqual(self.out["limits"], [20000, 6])
        js = (_util.ROOT / "web" / "arena.js").read_text("utf-8")
        self.assertIn("const DOC_TEXT_MAX = 20000;", js)  # 網頁送的字數上限要和伺服器一樣

    def test_unstated_negations_are_dropped(self):
        """文件沒講的否定句（「無閉鎖期」「不保本」）：那一小句拿掉，其他照留。風險清單和英文文件不比對。"""
        tenor, call, extra, coupon, risks = self.out["stated"]
        self.assertEqual(tenor, "指數股票型（上市日前不含當日不接受買回）")
        self.assertEqual(call, "上市日前不接受買回；經理費 0.20%／0.25%（依規模不同），保管費 0.05%，無升降幅度限制")  # 文件有講的否定句、費用都留著
        self.assertEqual(extra, "")
        self.assertEqual(coupon, "成立滿45日後每月配息，配息不代表實際報酬")
        self.assertEqual(risks, "無存款保險保障\n不保本")  # 風險是 AI 用自己的話摘要的：不比對
        self.assertEqual(self.out["stated_english"], "不保本，無閉鎖期")
        self.assertEqual(self.out["stated_pieces"], ["股票型", "有閉鎖期", "月配", "甲，丁（庚）", "不保本"])  # 文件有「保本」：這一句留著
        self.assertEqual(self.out["prompt_rules"], [])

    def test_made_up_values_are_dropped(self):
        p = self.out["made_up"]
        self.assertEqual(p["type"], "")
        self.assertEqual(p["code"], "")
        self.assertEqual(p["risk"], "")
        self.assertEqual(p["coupon"], "")
        self.assertEqual(p["ki"], "")
        self.assertEqual(p["name"], "")
        self.assertEqual(p["risks"], "")
        self.assertEqual(p["strike"], "期初價格的 85%")
        self.assertEqual(self.out["not_dict"], "rejected")


if __name__ == "__main__":
    unittest.main()
