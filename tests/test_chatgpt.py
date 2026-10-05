"""chatgpt_brain.py：PKCE、授權網址參數、RS256（固定測試向量）、ID token 檢查、SSE 解析、錯誤碼對應、續期。
全部對假的 OpenAI（tools/fakes/fake_openai.py）測，不會連到真的服務。"""

import contextlib
import hashlib
import io
import json
import tempfile
import threading
import time
import unittest

import _util
import chatgpt_brain as cb
import common
import fake_openai
import testkeys
from chatgpt_brain import BrainError, ChatGPT, ChatGPTConfig
from storage import FileStore

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False

# 用 cryptography 套件（另一套實作）簽好的固定 JWT：key = testkeys.N / E，kid = test-key-1
VECTOR = (
    "eyJhbGciOiJSUzI1NiIsImtpZCI6InRlc3Qta2V5LTEiLCJ0eXAiOiJKV1QifQ."
    "eyJpc3MiOiJodHRwczovL2F1dGguZXhhbXBsZS50ZXN0IiwiYXVkIjoib2FpYXBwX3Rlc3R2ZWN0b3IiLCJzdWIiOiJ1c2VyLTEyMyIsImVtYWlsIjoicmVw"
    "QGV4YW1wbGUudGVzdCIsIm5vbmNlIjoibi0wUzZfV3pBMk1qIiwiaWF0IjoxNzkwMDMyNTMyLCJleHAiOjE3OTAwMzYxMzJ9."
    "GrOQJ0aKGyqAztZVpTanJQe-bWT2uApYaYgGgh0JovVPgQTqDE2UccxWGs02D1QELfZ7N0Th9GrXT-7pn7zl6RDqttXvtlkvfMzNTiDrWlmNMBllIAFKdfJVvy"
    "PScvOnWXhf4E_CaGE4IdIEkn5VmDOfcqosTatpMi8pyOSMZsu4nysGCeKAF28DqES9AZ4_A6_BOlM8hfqNSpKTaRHoXtGXQNeheOsC1coknPqjWiMeFUVeCQP"
    "MmoyPkJ2dNavJ68NJ_kw_HtHDVY15nJ1yBK_eVT9vWM_5qvTnYEnQldD7ZwsVPeb4nyRN5NPXYn9bNrLqqOunkDnr-K8I3G2n_w"
)
SCHEMA = {"type": "object", "properties": {
    "checklist": {"type": "object", "properties": {"empathy": {"type": "boolean"}}, "required": ["empathy"]},
    "trust": {"type": "integer", "minimum": 0, "maximum": 100},
    "flags": {"type": "array", "items": {"type": "object", "properties": {"quote": {"type": "string"}}}},
    "tip": {"type": "string"},
}, "required": ["checklist", "trust", "flags", "tip"]}
ALLOWED_BODY_KEYS = {"model", "input", "store", "stream", "instructions", "text", "reasoning"}


def sse(*events):
    out = []
    for e in events:
        out.append(f"event: {e['type']}\n".encode())
        out.append(("data: " + json.dumps(e, ensure_ascii=False) + "\n").encode("utf-8"))
        out.append(b"\n")
    return out


def delta(t):
    return {"type": "response.output_text.delta", "delta": t}


COMPLETED = {"type": "response.completed", "response": {"status": "completed", "output": []}}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = fake_openai.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def setUp(self):
        self.srv.state.reset()
        self.tmp = tempfile.mkdtemp(prefix="cg-")
        self.store = FileStore(self.tmp)
        self.g = self.brain()

    def env(self, extra=None):
        e = _util.fake_openai_env(self.srv.url)
        e.update(extra or {})
        return e

    def brain(self, extra=None, on_cloud=False, store=None):
        return ChatGPT(ChatGPTConfig.from_env(self.env(extra), on_cloud), store or self.store)

    def knobs(self, **kw):
        with self.srv.state.lock:
            self.srv.state.knobs.update(kw)
            self.srv.state.pre_error_used = 0

    def authorize(self, g=None, uid="local", target="local", host_id=None):
        g = g or self.g
        url = g.begin_login(uid, g.redirect_uri(9150), "http://127.0.0.1:9150", host_id=host_id, target=target)
        loc = _util.location(url, self.srv.url)
        return url, loc

    def login(self, g=None, uid="local"):
        g = g or self.g
        url, loc = self.authorize(g, uid)
        res = g.finish_login(uid, _util.query_of(loc))
        self.assertTrue(res["ok"], res)
        return res

    def record(self, uid="local"):
        return self.store.get_secret(f"chatgpt-account-{uid}")

    def patch_record(self, uid="local", **fields):
        rec = self.record(uid)
        rec.update(fields)
        self.store.put_secret(f"chatgpt-account-{uid}", rec)
        self.g._tokens.clear()

    def text(self, g=None, **kw):
        g = g or self.g
        args = dict(model="gpt-6.1-sol")
        args.update(kw)
        return "".join(g.stream("local", "你是客戶", [{"role": "user", "content": "你好"}], **args))

    def refresh_calls(self):
        return [c for c in self.srv.state.token_calls if c[0] == "refresh_token"]


class PkceAndAuthorizeTests(Base):
    def test_pkce_pair(self):
        v, c = cb.pkce_pair()
        self.assertRegex(v, r"^[A-Za-z0-9_-]{43}$")
        self.assertEqual(c, common.b64u(hashlib.sha256(v.encode()).digest()))
        self.assertNotEqual(v, cb.pkce_pair()[0])

    def test_first_authorize_parameters(self):
        url, loc = self.authorize()
        q = {k: v[0] for k, v in _util.query_of(url).items()}
        self.assertTrue(url.startswith(self.srv.url + "/api/accounts/authorize?"))
        self.assertEqual(q["client_id"], "dynamic_agent_client")
        self.assertEqual(q["response_type"], "code")
        self.assertEqual(q["redirect_uri"], "http://127.0.0.1:9150/auth/callback")
        self.assertEqual(q["scope"], "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct")
        self.assertEqual(q["resource"], "https://api.openai.com/v1")
        self.assertEqual(q["code_challenge_method"], "S256")
        self.assertRegex(q["code_challenge"], r"^[A-Za-z0-9_-]{43}$")
        self.assertRegex(q["state"], r"^[A-Za-z0-9_-]{43}$")
        self.assertNotEqual(q["state"], q["nonce"])
        self.assertRegex(q["ext_agent_host_id"], r"^urn:uuid:[0-9a-f-]{36}$")
        self.assertEqual(q["agent_name_hint"], "AI 理專對練教練")
        for k in ("login_hint", "id_token_hint", "prompt"):
            self.assertNotIn(k, q)
        self.assertEqual(self.store.get_secret("chatgpt-host")["host_id"], q["ext_agent_host_id"])
        url2, _ = self.authorize()
        self.assertEqual(_util.query_of(url2)["ext_agent_host_id"][0], q["ext_agent_host_id"])  # 主機代號不變
        self.assertNotEqual(_util.query_of(url2)["state"][0], q["state"])
        self.assertIn("client_id=oaiapp_", loc)

    def test_returning_sign_in_uses_issued_client_and_login_hint(self):
        self.login()
        client_id = self.record()["client_id"]
        self.assertTrue(client_id.startswith("oaiapp_"))
        self.assertTrue(self.g.logout("local"))
        url, loc = self.authorize()  # 假 OpenAI 會拒絕帶 agent_name_hint 的重新授權
        q = {k: v[0] for k, v in _util.query_of(url).items()}
        self.assertEqual(q["client_id"], client_id)
        self.assertEqual(q["login_hint"], "rep@example.test")
        self.assertNotIn("agent_name_hint", q)
        self.assertNotIn("client_id", _util.query_of(loc))  # 重新授權時回呼不帶 client_id
        res = self.g.finish_login("local", _util.query_of(loc))
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.record()["client_id"], client_id)

    def test_redirect_rules(self):
        for bad in ("http://localhost:9150/auth/callback", "http://127.0.0.1:9150/callback",
                    "https://127.0.0.1:9150/auth/callback", "http://127.0.0.1/auth/callback",
                    "http://127.0.0.1:9150/auth/callback?x=1"):
            with self.assertRaises(BrainError) as cm:
                self.g.begin_login("local", bad, "")
            self.assertEqual(cm.exception.code, "bad_redirect")
        with self.assertRaises(BrainError):
            self.g.redirect_uri(None)
        custom = self.brain({"CHATGPT_REDIRECT_PATH": "/oauth/cb"})
        self.assertEqual(custom.redirect_uri(9151), "http://127.0.0.1:9151/oauth/cb")

    def test_cloud_target_needs_other_host_id(self):
        own = self.g.host_id()
        with self.assertRaises(BrainError):
            self.g.begin_login("local", self.g.redirect_uri(9150), "", target="cloud")
        with self.assertRaises(BrainError):
            self.g.begin_login("local", self.g.redirect_uri(9150), "", host_id=own, target="cloud")
        with self.assertRaises(BrainError):
            self.g.begin_login("local", self.g.redirect_uri(9150), "", host_id="not-a-host", target="cloud")
        cloud_host = "urn:uuid:12345678-1234-4234-8234-123456789abc"
        url, loc = self.authorize(host_id=cloud_host, target="cloud")
        self.assertEqual(_util.query_of(url)["ext_agent_host_id"][0], cloud_host)
        res = self.g.finish_login("local", _util.query_of(loc))
        self.assertTrue(res["ok"] and res["target"] == "cloud", res)
        self.assertIsNone(self.record())  # 本機自己的登入沒有被動到
        cloud = self.store.get_secret("chatgpt-cloud-client")
        self.assertEqual(cloud["ext_agent_host_id"], cloud_host)
        self.assertTrue(cloud["refresh_token"])

    def test_modes_that_cannot_sign_in(self):
        off = self.brain(on_cloud=True)
        self.assertEqual(off.cfg.mode, "off")
        self.assertTrue(off.cfg.blocked_reason)
        synced = self.brain({"CHATGPT_SYNC_KEY": "k" * 32, "APP_SECRET": "x"}, on_cloud=True)
        self.assertEqual(synced.cfg.mode, "synced")
        self.assertFalse(synced.cfg.can_sign_in)
        self.assertTrue(synced.cfg.available and synced.cfg.sync_enabled)
        for g in (off, synced):
            with self.assertRaises(BrainError) as cm:
                g.begin_login("x", "http://127.0.0.1:9150/auth/callback", "")
            self.assertEqual(cm.exception.code, "blocked")


class CallbackTests(Base):
    def test_success_saves_record_without_leaking_tokens(self):
        res = self.login()
        self.assertEqual(res, {"ok": True, "reason": "", "return_to": "http://127.0.0.1:9150", "plan_usage": True,
                               "target": "local", "email": "rep@example.test"})
        rec = self.record()
        for k in ("client_id", "access_token", "refresh_token", "id_token", "expires_at", "earliest_refresh_at", "scope",
                  "plan_usage", "needs_login", "email", "sub", "ext_agent_host_id"):
            self.assertIn(k, rec)
        self.assertGreater(rec["expires_at"], time.time() + 3000)
        st = self.g.account_status("local")
        self.assertTrue(st["signed_in"] and st["plan_usage"] and st["configured"])
        dump = json.dumps(st)
        for secret in (rec["access_token"], rec["refresh_token"], rec["id_token"]):
            self.assertNotIn(secret, dump)

    def test_state_is_single_use_and_bound(self):
        url, loc = self.authorize()
        q = _util.query_of(loc)
        self.assertEqual(self.g.finish_login("someone-else", q)["reason"], "bad_state")
        url, loc = self.authorize()
        q = _util.query_of(loc)
        self.assertTrue(self.g.finish_login("local", q)["ok"])
        self.assertEqual(self.g.finish_login("local", q)["reason"], "expired")
        self.assertEqual(self.g.finish_login("local", {"state": "short"})["reason"], "bad_state")
        self.assertEqual(self.g.finish_login("local", {})["reason"], "bad_state")

    def test_denied(self):
        self.knobs(consent="deny")
        url, loc = self.authorize()
        res = self.g.finish_login("local", _util.query_of(loc))
        self.assertEqual((res["ok"], res["reason"]), (False, "denied"))
        self.assertIsNone(self.record())

    def test_missing_issued_client_id(self):
        self.knobs(omit_client_id=True)
        url, loc = self.authorize()
        self.assertEqual(self.g.finish_login("local", _util.query_of(loc))["reason"], "no_client_id")

    def test_client_mismatch_and_no_code(self):
        self.login()
        url, loc = self.authorize()
        q = _util.query_of(loc)
        q["client_id"] = ["oaiapp_someoneelse"]
        self.assertEqual(self.g.finish_login("local", q)["reason"], "client_mismatch")
        url, loc = self.authorize()
        q = _util.query_of(loc)
        q.pop("code")
        self.assertEqual(self.g.finish_login("local", q)["reason"], "no_code")

    def test_no_plan_scope(self):
        self.knobs(plan_scope=False)
        url, loc = self.authorize()
        res = self.g.finish_login("local", _util.query_of(loc))
        self.assertEqual((res["ok"], res["reason"], res["plan_usage"]), (True, "no_plan", False))
        st = self.g.account_status("local")
        self.assertFalse(st["configured"])
        self.assertIn("方案", st["message"])
        with self.assertRaises(BrainError) as cm:
            self.text()
        self.assertEqual(cm.exception.code, "no_plan_scope")
        self.assertTrue(cm.exception.fallback)
        url, _ = self.authorize()
        self.assertEqual(_util.query_of(url).get("prompt"), ["consent"])  # 再登入時要重新請使用者同意

    def test_bad_id_token_signature_or_issuer(self):
        for knob in ({"sign_with_other_key": True}, {"sign_with_other_key": False, "id_token_iss": "https://evil.test"}):
            self.knobs(**knob)
            url, loc = self.authorize()
            res = self.g.finish_login("local", _util.query_of(loc))
            self.assertEqual(res["reason"], "bad_id_token", knob)
            self.assertIsNone(self.record())

    def test_verify_knob_skips_only_signature(self):
        g = self.brain({"CHATGPT_VERIFY_ID_TOKEN": "0"})
        self.knobs(sign_with_other_key=True)
        self.login(g)
        self.knobs(sign_with_other_key=False, id_token_iss="https://evil.test")
        url, loc = self.authorize(g)
        self.assertEqual(g.finish_login("local", _util.query_of(loc))["reason"], "bad_id_token")


class Rs256Tests(Base):
    def test_fixed_vector(self):
        header, claims, signing_input, sig = cb.decode_jwt(VECTOR)
        self.assertEqual(header["kid"], "test-key-1")
        self.assertTrue(cb.rs256_verify(testkeys.N, testkeys.E, signing_input, sig))
        self.assertFalse(cb.rs256_verify(testkeys.N, testkeys.E, signing_input + b"x", sig))
        self.assertFalse(cb.rs256_verify(testkeys.OTHER_N, testkeys.E, signing_input, sig))
        self.assertFalse(cb.rs256_verify(testkeys.N, testkeys.E, signing_input, sig[:-1]))
        self.assertFalse(cb.rs256_verify(testkeys.N, testkeys.E, signing_input, (testkeys.N + 1).to_bytes(256, "big")))
        bad = bytearray(sig)
        bad[10] ^= 1
        self.assertFalse(cb.rs256_verify(testkeys.N, testkeys.E, signing_input, bytes(bad)))
        n, e = cb.jwk_numbers(common.jwk(testkeys.N, testkeys.E, "k"))
        self.assertEqual((n, e), (testkeys.N, testkeys.E))
        self.assertIsNone(cb.jwk_numbers({"kty": "EC"}))

    def test_fake_signer_matches_vector(self):
        h, p, _ = VECTOR.split(".")
        self.assertEqual(common.b64u(common.rs256_sign(testkeys.N, testkeys.D, (h + "." + p).encode())), VECTOR.split(".")[2])

    @unittest.skipUnless(HAVE_CRYPTO, "需要 cryptography")
    def test_cross_check_with_cryptography(self):
        p, q = rsa.rsa_recover_prime_factors(testkeys.N, testkeys.E, testkeys.D)
        priv = rsa.RSAPrivateNumbers(p, q, testkeys.D, rsa.rsa_crt_dmp1(testkeys.D, p), rsa.rsa_crt_dmq1(testkeys.D, q),
                                     rsa.rsa_crt_iqmp(p, q), rsa.RSAPublicNumbers(testkeys.E, testkeys.N)).private_key()
        for msg in (b"", b"abc", "中文".encode() * 100):
            sig = priv.sign(msg, padding.PKCS1v15(), hashes.SHA256())
            self.assertTrue(cb.rs256_verify(testkeys.N, testkeys.E, msg, sig))
            priv.public_key().verify(common.rs256_sign(testkeys.N, testkeys.D, msg), msg, padding.PKCS1v15(), hashes.SHA256())

    def claims(self, **kw):
        now = int(time.time())
        c = {"iss": self.srv.url, "aud": "oaiapp_x", "sub": "user-1", "email": "a@b", "nonce": "n1", "iat": now, "exp": now + 60}
        c.update(kw)
        return {k: v for k, v in c.items() if v is not None}

    def token(self, kid=testkeys.KID, alg="RS256", **kw):
        return common.make_jwt(self.claims(**kw), testkeys.N, testkeys.D, kid, alg)

    def test_check_id_token_rules(self):
        g = self.g
        self.assertEqual(g.check_id_token(self.token(), "oaiapp_x", nonce="n1")["sub"], "user-1")
        self.assertEqual(g.check_id_token(self.token(aud=["oaiapp_x", "other"]), "oaiapp_x", nonce="n1")["sub"], "user-1")
        g.check_id_token(self.token(exp=int(time.time()) - 3), "oaiapp_x", nonce="n1")  # 5 秒內的時差可以
        g.check_id_token(self.token(exp=int(time.time()) - 600), "oaiapp_x", check_exp=False)
        bad = [
            (self.token(aud="oaiapp_y"), "n1"), (self.token(iss="https://evil"), "n1"), (self.token(), "n2"),
            (self.token(exp=int(time.time()) - 30), "n1"), (self.token(sub=None), "n1"), (self.token(kid="unknown"), "n1"),
            (self.token(alg="HS256"), "n1"), ("not.a.jwt", "n1"), ("abc", "n1"),
        ]
        h, p, s = self.token().split(".")
        none_alg = common.b64u(json.dumps({"alg": "none"}).encode()) + "." + p + "."
        bad.append((none_alg, "n1"))
        for tok, nonce in bad:
            with self.assertRaises(BrainError):
                g.check_id_token(tok, "oaiapp_x", nonce=nonce)


class SseTests(Base):
    def events(self, lines, json_schema=None, max_chars=None):
        return list(self.g._events(lines, json_schema, None, None, max_chars))

    def test_parse_sse(self):
        lines = [b": ping\n", b"event: a\r\n", b"data: {\"x\":\n", b"data: 1}\n", b"\n", b"data: last\n"]
        self.assertEqual(list(cb.parse_sse(lines)), [("a", '{"x":\n1}'), ("", "last")])

    def test_delta_and_completed(self):
        self.assertEqual(self.events(sse(delta("你"), delta("好"), COMPLETED, delta("不該出現"))), ["你", "好"])

    def test_completed_output_when_no_delta(self):
        done = {"type": "response.completed", "response": {"output": [{"content": [{"type": "output_text", "text": "整段"}]}]}}
        self.assertEqual(self.events(sse(done)), ["整段"])

    def test_failed_and_error_events(self):
        failed = {"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "x"}}}
        with self.assertRaises(cb._Fail) as cm:
            self.events(sse(delta("a"), failed))
        self.assertEqual(cm.exception.err["code"], "subscription_sharing_usage_limit_exceeded")
        with self.assertRaises(cb._Fail) as cm:
            self.events(sse({"type": "error", "code": "server_error", "message": "m", "param": None}))
        self.assertEqual(cm.exception.err["code"], "server_error")

    def test_incomplete(self):
        inc = {"type": "response.incomplete", "response": {"incomplete_details": {"reason": "max_output_tokens"}}}
        self.assertEqual(self.events(sse(delta("一句話"), inc)), ["一句話"])  # 一般對話：有內容就算講完
        with self.assertRaises(BrainError) as cm:
            self.events(sse(delta("{"), inc), json_schema=SCHEMA)
        self.assertEqual(cm.exception.code, "incomplete")
        with self.assertRaises(BrainError):
            self.events(sse(inc))

    def test_interrupted(self):
        with self.assertRaises(BrainError) as cm:
            self.events(sse(delta("a"), delta("b")))
        self.assertEqual(cm.exception.code, "stream_interrupted")

    def test_max_chars_stops_after_sentence(self):
        pieces = ["一二三。", "四五六七", "。八九十", "。"]
        self.assertEqual("".join(self.events(sse(*map(delta, pieces), COMPLETED), max_chars=5)), "一二三。四五六七。")
        self.assertEqual("".join(self.events(sse(*map(delta, pieces), COMPLETED), max_chars=100)), "".join(pieces))
        self.assertEqual("".join(self.events(sse(delta("短"), COMPLETED), max_chars=1)), "短")

    def test_strict_schema(self):
        s = cb.strict_schema(SCHEMA)
        self.assertFalse(s["additionalProperties"])
        self.assertFalse(s["properties"]["checklist"]["additionalProperties"])
        item = s["properties"]["flags"]["items"]
        self.assertEqual((item["additionalProperties"], item["required"]), (False, ["quote"]))
        self.assertNotIn("additionalProperties", SCHEMA)  # 原本的 schema 沒被改到

    def test_parse_models(self):
        rows = cb.parse_models({"models": [{"slug": "a", "display_name": "A", "visibility": "list"},
                                           {"slug": "h", "visibility": "hide"}, {"slug": "b", "visibility": "list"}]})
        self.assertEqual(rows, [{"slug": "a", "display_name": "A"}, {"slug": "b", "display_name": "b"}])
        self.assertEqual(cb.parse_models({"data": [{"id": "x"}]}), [{"slug": "x", "display_name": "x"}])
        self.assertEqual(cb.parse_models({}), [])

    def test_epoch_formats(self):
        self.assertEqual(cb._epoch(1790000000), 1790000000)
        self.assertEqual(cb._epoch(1790000000123), 1790000000)
        self.assertEqual(cb._epoch("1790000000"), 1790000000)
        for s in ("2026-10-05T00:00:00Z", "2026-10-05T00:00:00.5Z", "2026-10-05T00:00:00.123456789Z", "2026-10-05T08:00:00+08:00"):
            self.assertEqual(cb._epoch(s), 1791158400, s)
        self.assertEqual(cb._epoch("someday"), 0)
        self.assertEqual(cb._epoch(None), 0)


class StreamTests(Base):
    def setUp(self):
        super().setUp()
        self.login()

    def test_body_follows_docs(self):
        self.assertEqual(self.text(), fake_openai.DEFAULT_KNOBS["reply"])
        out = "".join(self.g.stream("local", "教練", [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
                                                     {"role": "assistant", "content": "a"}],
                                    model="gpt-5.6-luna", json_schema=SCHEMA, reasoning_effort="low"))
        self.assertEqual(set(json.loads(out)), {"checklist", "trust", "flags", "tip"})
        body = self.srv.state.requests[-1]
        self.assertLessEqual(set(body), ALLOWED_BODY_KEYS)
        self.assertIs(body["store"], False)
        self.assertIs(body["stream"], True)
        self.assertEqual(body["instructions"], "教練")
        self.assertEqual([i["role"] for i in body["input"]], ["developer", "user", "assistant"])
        fmt = body["text"]["format"]
        self.assertEqual((fmt["type"], fmt["name"], fmt["strict"]), ("json_schema", "result", True))
        self.assertFalse(fmt["schema"]["additionalProperties"])
        self.assertEqual(body["reasoning"], {"effort": "low"})
        for b in self.srv.state.requests:
            for f in cb.UNSUPPORTED_FIELDS:
                self.assertNotIn(f, b)

    def test_json_mode_none_and_no_effort(self):
        g = self.brain({"CHATGPT_JSON_MODE": "none"})
        "".join(g.stream("local", "x", [{"role": "user", "content": "u"}], model="m", json_schema=SCHEMA))
        body = self.srv.state.requests[-1]
        self.assertNotIn("text", body)
        self.assertNotIn("reasoning", body)

    def test_auto_model_and_model_cache(self):
        self.text(model="")
        self.assertEqual(self.srv.state.requests[-1]["model"], "gpt-6.1-sol")
        self.assertEqual(self.g.list_models("local")[0]["slug"], "gpt-6.1-sol")
        self.assertEqual(self.store.eph_get("cg-models:local")[1]["slug"], "gpt-5.6-luna")
        self.store.eph_pop("cg-models:local")
        self.knobs(models_shape="data")
        self.assertEqual([r["slug"] for r in self.g.list_models("local")], ["gpt-6.1-sol", "gpt-5.6-luna"])

    def test_usage_limit_sets_cooldown_and_skips(self):
        self.knobs(pre_error={"status": 429, "code": "subscription_sharing_usage_limit_exceeded"})
        with self.assertRaises(BrainError) as cm:
            self.text()
        e = cm.exception
        self.assertEqual((e.code, e.status, e.fallback, e.started), ("subscription_sharing_usage_limit_exceeded", 429, True, False))
        until = self.g.cooldown_until("local")
        self.assertAlmostEqual(until, time.time() + 900, delta=5)
        n = len(self.srv.state.requests)
        with self.assertRaises(BrainError) as cm:
            self.text()
        self.assertEqual(cm.exception.code, "cooldown")
        self.assertEqual(len(self.srv.state.requests), n)  # 冷卻中不再送出
        self.assertIn("上限", self.g.account_status("local")["message"])

    def test_usage_limit_mid_stream(self):
        self.knobs(fail_after={"after": 2, "code": "subscription_sharing_usage_limit_exceeded"})
        got = []
        with self.assertRaises(BrainError) as cm:
            for piece in self.g.stream("local", "x", [{"role": "user", "content": "u"}], model="m"):
                got.append(piece)
        self.assertEqual(len(got), 2)
        self.assertTrue(cm.exception.started and cm.exception.fallback)
        self.assertGreater(self.g.cooldown_until("local"), 0)

    def test_temporary_unavailable_codes(self):
        for code in ("subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable"):
            self.g._clear_cooldown("local")
            self.knobs(pre_error={"status": 503, "code": code})
            with self.assertRaises(BrainError) as cm:
                self.text()
            self.assertEqual(cm.exception.code, code)
            self.assertAlmostEqual(self.g.cooldown_until("local"), time.time() + 60, delta=5)

    def test_unsupported_field_dropped_and_remembered(self):
        for field in ("text", "reasoning"):
            self.srv.state.requests.clear()
            self.knobs(reject_params=[field])
            out = "".join(self.g.stream("local", "x", [{"role": "user", "content": "u"}], model="m-" + field,
                                        json_schema=SCHEMA, reasoning_effort="low"))
            self.assertTrue(out)
            self.assertEqual(len(self.srv.state.requests), 2)
            self.assertIn(field, self.srv.state.requests[0])
            self.assertNotIn(field, self.srv.state.requests[1])
            "".join(self.g.stream("local", "x", [{"role": "user", "content": "u"}], model="m-" + field,
                                  json_schema=SCHEMA, reasoning_effort="low"))
            self.assertEqual(len(self.srv.state.requests), 3)  # 記住了：不再先送一次被拒的
            self.assertNotIn(field, self.srv.state.requests[2])

    def test_unsupported_other_param_falls_back(self):
        self.knobs(pre_error={"status": 400, "code": "subscription_sharing_unsupported_capability", "param": "model", "times": -1})
        with self.assertRaises(BrainError) as cm:
            self.text()
        self.assertTrue(cm.exception.fallback)
        self.assertEqual(cm.exception.param, "model")
        self.assertEqual(len(self.srv.state.requests), 1)

    def test_invalid_user_refreshes_once(self):
        self.knobs(pre_error={"status": 401, "code": "subscription_sharing_invalid_user", "times": 1})
        old = self.record()["refresh_token"]
        self.assertTrue(self.text())
        self.assertEqual(len(self.refresh_calls()), 1)
        self.assertNotEqual(self.record()["refresh_token"], old)

    def test_invalid_user_twice_needs_login(self):
        self.knobs(pre_error={"status": 401, "code": "subscription_sharing_invalid_user", "times": -1})
        with self.assertRaises(BrainError) as cm:
            self.text()
        self.assertEqual(cm.exception.code, "needs_login")
        st = self.g.account_status("local")
        self.assertTrue(st["needs_login"])
        self.assertFalse(st["signed_in"])
        self.assertTrue(self.record()["client_id"])

    def test_scope_not_authorized_asks_consent(self):
        self.knobs(pre_error={"status": 403, "code": "chatpass_v2_scope_not_authorized"})
        with self.assertRaises(BrainError) as cm:
            self.text()
        self.assertTrue(cm.exception.fallback)
        self.assertTrue(self.g.account_status("local")["needs_login"])
        url, _ = self.authorize()
        self.assertEqual(_util.query_of(url).get("prompt"), ["consent"])

    def test_not_eligible_and_policy_codes(self):
        self.knobs(pre_error={"status": 403, "code": "subscription_sharing_user_not_eligible"})
        with self.assertRaises(BrainError):
            self.text()
        self.assertFalse(self.record()["plan_usage"])
        self.login()
        for code, secs in (("subscription_sharing_v2_client_not_enabled", 6 * 3600), ("subscription_sharing_route_not_supported", 6 * 3600),
                           ("", 900)):
            self.g._clear_cooldown("local")
            self.knobs(pre_error={"status": 403, "code": code})
            with self.assertRaises(BrainError) as cm:
                self.text()
            self.assertTrue(cm.exception.fallback)
            self.assertAlmostEqual(self.g.cooldown_until("local"), time.time() + secs, delta=5)

    def test_detail_503_and_5xx_no_cooldown(self):
        self.knobs(detail_503=True)
        with self.assertRaises(BrainError) as cm:
            self.text()
        self.assertEqual((cm.exception.status, cm.exception.fallback), (503, True))
        self.assertEqual(self.g.cooldown_until("local"), 0)
        self.knobs(detail_503=False, pre_error={"status": 500, "code": "server_error"})
        with self.assertRaises(BrainError) as cm:
            self.text()
        self.assertEqual(self.g.cooldown_until("local"), 0)

    def test_interrupt_incomplete_timeout_cancel(self):
        self.knobs(interrupt_after=2)
        with self.assertRaises(BrainError) as cm:
            self.text()
        self.assertEqual((cm.exception.code, cm.exception.started), ("stream_interrupted", True))
        self.knobs(interrupt_after=None, incomplete_after=0)
        with self.assertRaises(BrainError) as cm:
            self.text()
        self.assertEqual((cm.exception.code, cm.exception.started), ("incomplete", False))
        self.knobs(incomplete_after=None, delay=1.0)
        t0 = time.monotonic()
        with self.assertRaises(BrainError) as cm:
            self.text(timeout=5, deadline=time.monotonic() + 1.5)
        self.assertEqual(cm.exception.code, "timeout")
        self.assertLess(time.monotonic() - t0, 4)
        with self.assertRaises(BrainError) as cm:
            self.text(timeout=0.3)
        self.assertEqual(cm.exception.code, "timeout")
        self.knobs(delay=0.05)
        ev = threading.Event()
        ev.set()
        with self.assertRaises(BrainError) as cm:
            self.text(cancel=ev)
        self.assertEqual((cm.exception.code, cm.exception.fallback), ("cancelled", False))

    def test_network_error(self):
        g = self.brain({"CHATGPT_API_BASE": f"http://127.0.0.1:{_util.free_port()}/v1"})
        with self.assertRaises(BrainError) as cm:
            "".join(g.stream("local", "x", [{"role": "user", "content": "u"}], model="m"))
        self.assertEqual((cm.exception.code, cm.exception.fallback), ("network", True))

    def test_max_chars_closes_early(self):
        self.knobs(reply="嗯嗯嗯嗯嗯嗯。後面不要。")
        self.assertEqual(self.text(max_chars=3), "嗯嗯嗯嗯嗯嗯。")

    def test_close_generator_mid_stream(self):
        self.knobs(delay=0.1, reply="一段一段慢慢講出來的回覆內容")
        gen = self.g.stream("local", "x", [{"role": "user", "content": "u"}], model="m")
        self.assertTrue(next(gen))
        gen.close()  # 業務插話：伺服器關掉 generator，上游連線要跟著關
        deadline = time.time() + 3
        while time.time() < deadline and not self.srv.state.disconnects:
            time.sleep(0.05)
        self.assertGreaterEqual(self.srv.state.disconnects, 1)

    def test_logs_never_contain_tokens(self):
        err = io.StringIO()
        rec = self.record()
        with contextlib.redirect_stderr(err):
            self.knobs(pre_error={"status": 401, "code": "subscription_sharing_invalid_user", "times": -1})
            with self.assertRaises(BrainError):
                self.text()
            self.knobs(pre_error=None, refresh_mode="invalidated")
            self.login()
            self.patch_record(expires_at=0)
            with self.assertRaises(BrainError):
                self.text()
        log = err.getvalue()
        self.assertIn("[ChatGPT]", log)
        for s in (rec["access_token"], rec["refresh_token"], rec["id_token"], "rt_", "at_", "code_"):
            self.assertNotIn(s, log)


class RefreshTests(Base):
    def setUp(self):
        super().setUp()
        self.login()

    def test_refresh_rotates_and_saves(self):
        old = self.record()
        self.patch_record(expires_at=int(time.time()) + 30)  # 60 秒內到期就續
        self.assertTrue(self.text())
        new = self.record()
        self.assertEqual(len(self.refresh_calls()), 1)
        self.assertNotEqual(new["refresh_token"], old["refresh_token"])
        self.assertNotEqual(new["access_token"], old["access_token"])
        self.assertGreater(new["expires_at"], time.time() + 3000)
        self.assertTrue(self.text())
        self.assertEqual(len(self.refresh_calls()), 1)  # 新 token 還很久才到期，不用再續

    def test_waits_for_earliest_refresh(self):
        self.patch_record(expires_at=int(time.time()) + 30, earliest_refresh_at=int(time.time()) + 300)
        self.assertTrue(self.text())
        self.assertEqual(len(self.refresh_calls()), 0)

    def test_concurrent_refresh_only_once(self):
        self.patch_record(expires_at=0)
        self.knobs(refresh_delay=0.3)
        results, errors = [], []

        def run():
            try:
                results.append(self.text())
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        ts = [threading.Thread(target=run) for _ in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 6)
        self.assertEqual(len(self.refresh_calls()), 1)

    def test_dead_refresh_needs_login(self):
        for mode in ("invalidated", "nested"):
            self.login()
            self.knobs(refresh_mode=mode)
            if mode == "nested":
                self.patch_record(refresh_token="rt_not_known")
            self.patch_record(expires_at=0)
            with self.assertRaises(BrainError) as cm:
                self.text()
            self.assertEqual((cm.exception.code, cm.exception.fallback), ("needs_login", True))
            rec = self.record()
            self.assertTrue(rec["needs_login"])
            self.assertEqual((rec["access_token"], rec["refresh_token"]), ("", ""))
            self.assertTrue(rec["client_id"].startswith("oaiapp_"))
            st = self.g.account_status("local")
            self.assertTrue(st["needs_login"])
            self.assertIn("重新登入", st["message"])
            self.knobs(refresh_mode="ok")

    def test_refresh_network_error_keeps_tokens(self):
        g = self.brain({"CHATGPT_TOKEN_URL": f"http://127.0.0.1:{_util.free_port()}/token"})
        self.patch_record(expires_at=0)
        with self.assertRaises(BrainError) as cm:
            "".join(g.stream("local", "x", [{"role": "user", "content": "u"}], model="m"))
        self.assertEqual(cm.exception.code, "network")
        self.assertFalse(self.record()["needs_login"])
        self.assertTrue(self.record()["refresh_token"])

    def test_iso_earliest_refresh(self):
        self.knobs(earliest_refresh_iso=True, earliest_refresh=120)
        self.login()
        self.assertAlmostEqual(self.record()["earliest_refresh_at"], time.time() + 120, delta=5)


class LogoutTests(Base):
    def test_logout_revokes_and_keeps_client(self):
        self.login()
        rec = self.record()
        self.assertTrue(self.g.logout("local"))
        self.assertEqual(self.srv.state.revokes[-1], {"client_id": rec["client_id"], "hint": "refresh_token"})
        kept = self.record()
        self.assertEqual(kept["client_id"], rec["client_id"])
        self.assertEqual(kept["email"], rec["email"])
        for k in ("access_token", "refresh_token", "id_token"):
            self.assertNotIn(k, kept)
        self.assertFalse(self.g.account_status("local")["signed_in"])
        with self.assertRaises(BrainError) as cm:
            self.text()
        self.assertEqual(cm.exception.code, "not_signed_in")

    def test_logout_when_revoke_unreachable(self):
        self.login()
        g = self.brain({"CHATGPT_REVOKE_URL": f"http://127.0.0.1:{_util.free_port()}/revoke"})
        self.assertFalse(g.logout("local"))
        self.assertNotIn("refresh_token", self.record())


class ConfigTests(unittest.TestCase):
    def test_modes(self):
        C = ChatGPTConfig.from_env
        self.assertEqual(C({}, False).mode, "local")
        self.assertEqual(C({}, False).blocked_reason, "")
        self.assertEqual(C({}, True).mode, "off")
        self.assertIn("CHATGPT_SYNC_KEY", C({}, True).blocked_reason)
        s = C({"CHATGPT_SYNC_KEY": "k"}, True)
        self.assertEqual((s.mode, s.sync_enabled, s.can_sign_in, s.available), ("synced", True, False, True))
        self.assertFalse(C({"CHATGPT_SYNC_KEY": "k"}, False).sync_enabled)  # 本機有同步密碼也還是本機模式
        h = C({"CHATGPT_CLIENT_ID": "oaiapp_x"}, True)
        self.assertEqual(h.mode, "hosted")
        self.assertIn("CHATGPT_REDIRECT_URI", h.blocked_reason)
        h2 = C({"CHATGPT_CLIENT_ID": "oaiapp_x", "CHATGPT_CLIENT_SECRET": "s", "CHATGPT_REDIRECT_URI": "https://a.b/auth/callback"}, True)
        self.assertEqual((h2.blocked_reason, h2.token_auth, h2.can_sign_in), ("", "client_secret_basic", True))
        self.assertEqual(C({"CHATGPT_MODE": "local"}, True).blocked_reason[:2], "雲端")
        self.assertTrue(C({"CHATGPT_MODE": "weird"}, False).blocked_reason)
        self.assertEqual(C({"CHATGPT_MODE": "off"}, False).mode, "off")
        d = C({"CHATGPT_COACH_REASONING_EFFORT": "none", "CHATGPT_REASONING_EFFORT": "medium", "CHATGPT_CHAT_MAX_CHARS": "120"}, False)
        self.assertEqual((d.coach_reasoning_effort, d.reasoning_effort, d.chat_max_chars), ("", "medium", 120))
        self.assertEqual(C({}, False).coach_reasoning_effort, "low")

    def test_client_secret_basic_header(self):
        cfg = ChatGPTConfig.from_env({"CHATGPT_MODE": "hosted", "CHATGPT_CLIENT_ID": "oaiapp x", "CHATGPT_CLIENT_SECRET": "p:w",
                                      "CHATGPT_REDIRECT_URI": "https://a.b/auth/callback"}, True)
        g = ChatGPT(cfg, FileStore(tempfile.mkdtemp()))
        fields = {"client_id": "oaiapp x"}
        h = g._client_auth(fields)
        import base64
        self.assertEqual(base64.b64decode(h["Authorization"][6:]).decode(), "oaiapp+x:p%3Aw")
        self.assertNotIn("client_secret", fields)  # basic：祕密不放表單
        cfg.token_auth = "client_secret_post"
        fields = {"client_id": "oaiapp x"}
        self.assertEqual(g._client_auth(fields), {})
        self.assertEqual(fields["client_secret"], "p:w")
        local = ChatGPT(ChatGPTConfig.from_env({"CHATGPT_CLIENT_SECRET": "p"}, False), g.store)
        self.assertEqual(local._client_auth({"client_id": "x"}), {})


if __name__ == "__main__":
    unittest.main()
