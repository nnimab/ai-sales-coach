"""備援規則（server.run_chain、brain_chain）：用假的大腦測。server.py 在子程序裡、從沒有 .env 的暫存複本載入，
不會用到任何真的金鑰；server.py 還沒有這些函式時略過。"""

import json
import subprocess
import sys
import unittest

import _util

PROBE = r'''
import json, sys
sys.path.insert(0, ".")
import server
out = {}
LLMError = server.LLMError

def mk(i):
    return {"id": i, "label": i, "kind": "openai"}

chain = [mk("chatgpt"), mk("gemini"), mk("ollama")]

def run(errors):
    calls = []
    def fn(p, last):
        calls.append([p["id"], last])
        e = errors.get(p["id"])
        if e is not None:
            raise e
        return "ok-" + p["id"]
    try:
        res, used = server.run_chain(chain, fn)
        return {"result": res, "used": used["id"], "calls": calls}
    except LLMError as e:
        return {"error": str(e), "calls": calls}

out["first_ok"] = run({})
out["fallback_once"] = run({"chatgpt": LLMError("cg busy", fallback=True)})
out["no_fallback_flag"] = run({"chatgpt": LLMError("cg bad", fallback=False)})
out["all_fail_reports_main"] = run({k: LLMError(k + " down", fallback=True) for k in ("chatgpt", "gemini", "ollama")})

def chain_ids(purpose, **env):
    server.ENV.update(env)
    for k, v in list(server.ENV.items()):
        if v is None:
            server.ENV.pop(k)
    server.PROVIDERS = server.build_providers(server.ENV, server.ON_CLOUD)
    server.CHATGPT_CFG = server.chatgpt_brain.ChatGPTConfig.from_env(server.ENV, server.ON_CLOUD)
    server.CHATGPT = server.chatgpt_brain.ChatGPT(server.CHATGPT_CFG, server.STORE)
    return [p["id"] for p in server.brain_chain(purpose)]

out["on_cloud"] = server.ON_CLOUD
out["default"] = chain_ids("chat")
out["gemini_chat_cold"] = chain_ids("chat", LLM_PROVIDER="gemini", GEMINI_API_KEY="fake-key")
out["gemini_eval_cold"] = chain_ids("eval")
server.mark_local_warm(True)
out["gemini_chat_warm"] = chain_ids("chat")
out["chatgpt_not_signed_in"] = chain_ids("chat", LLM_PROVIDER="chatgpt")
out["azure_no_backup"] = chain_ids("eval", LLM_PROVIDER="azure", AZURE_OPENAI_ENDPOINT="https://x.example", AZURE_OPENAI_API_KEY="k")
out["ollama_main_no_cloud"] = chain_ids("eval", LLM_PROVIDER="ollama")
out["backups_only_gemini"] = chain_ids("eval", LLM_PROVIDER="gemini", BRAIN_BACKUPS="gemini")
print(json.dumps(out, ensure_ascii=False))
'''


def _has_chain():
    try:
        text = (_util.ROOT / "server.py").read_text(encoding="utf-8")
    except OSError:
        return False
    return "def run_chain(" in text and "def brain_chain(" in text


@unittest.skipUnless(_has_chain(), "server.py 還沒有 run_chain／brain_chain")
class ChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = _util.copy_project()
        cls.local = cls.probe({})
        cls.cloud = cls.probe({"APP_MODE": "cloud", "APP_SECRET": "x" * 40, "APP_PASSCODE": "p"})

    @classmethod
    def probe(cls, env):
        dead = f"http://127.0.0.1:{_util.free_port()}"  # Ollama 指到沒人在聽的埠：不會碰到真的本機模型
        r = subprocess.run([sys.executable, "-c", PROBE], cwd=str(cls.dir), capture_output=True, timeout=120,
                           env=_util.clean_env(dict(env, OLLAMA_BASE_URL=dead)))
        lines = [ln for ln in r.stdout.decode("utf-8", "replace").splitlines() if ln.startswith("{")]
        if r.returncode or not lines:
            raise AssertionError("探測程式失敗：" + r.stderr.decode("utf-8", "replace")[-1500:])
        return json.loads(lines[-1])

    def test_run_chain(self):
        o = self.local
        self.assertEqual((o["first_ok"]["used"], o["first_ok"]["calls"]), ("chatgpt", [["chatgpt", False]]))
        self.assertEqual(o["fallback_once"]["used"], "gemini")
        self.assertEqual(o["fallback_once"]["calls"], [["chatgpt", False], ["gemini", False]])
        self.assertEqual(o["no_fallback_flag"]["error"], "cg bad")  # 不能換的錯誤：不試下一個
        self.assertEqual(len(o["no_fallback_flag"]["calls"]), 1)
        self.assertEqual(o["all_fail_reports_main"]["error"], "chatgpt down")  # 全部失敗：回報主要大腦的原因
        self.assertEqual(o["all_fail_reports_main"]["calls"][-1], ["ollama", True])

    def test_local_rules(self):
        o = self.local
        self.assertFalse(o["on_cloud"])
        self.assertEqual(o["default"], ["ollama"])  # 沒有任何設定：和以前一樣用本機
        self.assertEqual(o["gemini_chat_cold"], ["gemini"])  # 本機模型還沒暖：客戶回話不等它
        self.assertEqual(o["gemini_eval_cold"], ["gemini", "ollama"])  # 評分照以前：Gemini 塞車改用本機
        self.assertEqual(o["gemini_chat_warm"], ["gemini", "ollama"])
        self.assertEqual(o["chatgpt_not_signed_in"][0], "gemini")  # 沒登入 ChatGPT：由 Gemini 接手
        self.assertNotIn("chatgpt", o["chatgpt_not_signed_in"])
        self.assertEqual(o["azure_no_backup"], ["azure"])
        self.assertEqual(o["ollama_main_no_cloud"], ["ollama"])  # 本機大腦不會偷偷改用雲端
        self.assertEqual(o["backups_only_gemini"], ["gemini"])

    def test_cloud_rules(self):
        o = self.cloud
        self.assertTrue(o["on_cloud"])
        self.assertEqual(o["default"][0], "gemini")  # 雲端預設大腦是 Gemini
        self.assertNotIn("ollama", o["gemini_eval_cold"])  # 雲端不拿 Ollama 當備援
        self.assertNotIn("ollama", o["gemini_chat_warm"])
        self.assertNotIn("chatgpt", o["chatgpt_not_signed_in"])


if __name__ == "__main__":
    unittest.main()
