"""評分的總時限：大腦一直不回（加上換備援），也要在 EVAL_BUDGET_SEC 內回 504。
不然 Vercel 會在 300 秒把函式砍掉，畫面什麼都拿不到，只能一直轉圈。"""

import time
import unittest
from unittest import mock

import _util  # noqa: F401  把專案和假服務放進 sys.path
import server

MSGS = [{"role": "rep", "text": "王伯伯您好，我是理專小林。"}, {"role": "customer", "text": "你好。"}]


class FakeHandler:
    """_evaluate 只用到這兩個方法：拿情境、回 JSON。"""

    def __init__(self, sc):
        self.sc = sc
        self.out = None

    def _scenario(self, body):
        return self.sc

    def _json(self, status, data):
        self.out = (status, data)


def provider(pid, kind):
    return {"id": pid, "label": pid, "kind": kind}


class EvalDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.sc = server.load_scenarios()["structured-suitability"]
        self.timeouts = []

    def evaluate(self, brain, budget=0.6):
        h = FakeHandler(self.sc)
        chain = [provider("chatgpt", "responses"), provider("gemini", "openai")]
        with mock.patch.object(server, "EVAL_BUDGET_SEC", budget), \
                mock.patch.object(server, "brain_chain", lambda purpose: chain), \
                mock.patch.object(server, "complete_json", brain):
            t0 = time.monotonic()
            server.Handler._evaluate(h, {"scenario_id": self.sc["id"], "messages": MSGS})
            return h.out, time.monotonic() - t0

    def slow_brain(self, p, messages, schema, temperature=0.2, cancel=None, max_tokens=None, timeout=300, deadline=None):
        # 像真的串流一樣：一直沒有內容，過了 deadline 就丟「逾時」，讓 run_chain 換下一個
        self.timeouts.append((p["id"], timeout, deadline))
        while deadline is None or time.monotonic() < deadline:
            time.sleep(0.01)
        raise server.LLMError("教練逾時", fallback=True, code="timeout")

    def test_slow_brains_stop_at_budget(self):
        (status, data), took = self.evaluate(self.slow_brain)
        self.assertEqual(status, 504)
        self.assertIn("還沒完成", data["error"])
        self.assertLess(took, 2)  # 兩個大腦加起來也不會超過總時限太多
        # 每一次都帶著同一個總時限，等回應的秒數也不會超過剩下的時間
        self.assertEqual(len({d for _, _, d in self.timeouts}), 1)
        self.assertTrue(all(t <= 0.6 + 1e-6 for _, t, _ in self.timeouts), self.timeouts)

    def test_other_errors_keep_their_message(self):
        def broken(p, *a, **k):
            raise server.LLMError("Gemini 的用量到上限了", fallback=False)

        (status, data), _ = self.evaluate(broken)
        self.assertEqual(status, 502)
        self.assertIn("用量到上限", data["error"])


if __name__ == "__main__":
    unittest.main()
