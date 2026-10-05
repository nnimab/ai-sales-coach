"""storage.py：本機檔案格式要和以前一模一樣；Redis 版對假的 Upstash 測。"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import _util  # noqa: F401  （設定 sys.path）
import fake_upstash
import storage
from storage import FileStore, MemoryStore, RedisStore, StoreError

try:
    import cryptography  # noqa: F401
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False


# ---- 以前 server.py 的寫法（原封不動抄過來當標準答案）


def legacy_atomic_write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + "-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def legacy_set_env_value(path, name, value):
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    out, found = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == name:
            if not found:
                out.append(f"{name}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{name}={value}")
    legacy_atomic_write_text(path, "\n".join(out) + "\n")


VOICES = {"provider": "gemini", "assign": {"gemini": {"chen": {"voice_id": "voice_1", "name": "陳先生 · 10/05", "brief": "台灣腔"}}},
          "gemini_model_note": "自動設計聲音失敗"}
PROFILE = {"version": 1, "surname": "林", "given_name": "小明", "address": "", "role": "wealth", "custom_title": "",
           "company": "永豐", "branch": "", "years": 3, "products": ["ETF", "基金"], "knowledge": "第一行\n第二行"}
ENV_TEXT = "﻿# 註解\r\nLLM_PROVIDER=ollama\r\nGEMINI_API_KEY=abc # 金鑰\r\n\r\nLLM_PROVIDER=gemini\r\nX='q'\r\n"


class FileStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="store-"))
        self.s = FileStore(self.tmp)

    def test_kind(self):
        self.assertEqual((self.s.kind, self.s.durable), ("file", True))

    def test_env_value_byte_identical(self):
        a, b = self.tmp / "a.env", self.tmp / "b.env"
        for p in (a, b):
            p.write_bytes(ENV_TEXT.encode("utf-8"))
        for name, value in (("LLM_PROVIDER", "chatgpt"), ("LIVE_ENABLED", "1"), ("GEMINI_API_KEY", "k" * 20)):
            legacy_set_env_value(a, name, value)
            storage.set_env_value(b, name, value)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        c = self.tmp / "none.env"
        storage.set_env_value(c, "LLM_PROVIDER", "gemini")
        self.assertEqual(c.read_bytes(), b"LLM_PROVIDER=gemini\n")

    def test_set_setting_writes_env(self):
        (self.tmp / ".env").write_bytes(ENV_TEXT.encode("utf-8"))
        ref = self.tmp / "ref.env"
        ref.write_bytes(ENV_TEXT.encode("utf-8"))
        self.s.set_setting("LLM_PROVIDER", "chatgpt")
        legacy_set_env_value(ref, "LLM_PROVIDER", "chatgpt")
        self.assertEqual((self.tmp / ".env").read_bytes(), ref.read_bytes())
        self.assertEqual(self.s.settings(), {"LLM_PROVIDER": "chatgpt"})
        with self.assertRaises(ValueError):
            self.s.set_setting("GEMINI_API_KEY", "x")
        with self.assertRaises(ValueError):
            self.s.set_setting("CHATGPT_MODEL", "a\nGEMINI_API_KEY=evil")

    def test_settings_only_runtime_keys_and_refresh(self):
        self.assertEqual(self.s.settings(), {})
        (self.tmp / ".env").write_text('LLM_PROVIDER="gemini" # 選擇\nGEMINI_API_KEY=secret\nLIVE_ENABLED=0\n', encoding="utf-8")
        self.assertEqual(self.s.settings(), {"LLM_PROVIDER": "gemini", "LIVE_ENABLED": "0"})
        time.sleep(0.02)
        (self.tmp / ".env").write_text("LLM_PROVIDER=chatgpt\nCHATGPT_MODEL=gpt-x\n", encoding="utf-8")
        self.assertEqual(self.s.settings(), {"LLM_PROVIDER": "chatgpt", "CHATGPT_MODEL": "gpt-x"})

    def test_voices_byte_identical(self):
        self.assertIsNone(self.s.get_doc("voices"))
        self.assertIsNone(self.s.doc_stamp("voices"))
        self.s.put_doc("voices", VOICES)
        want = json.dumps(VOICES, ensure_ascii=False, indent=2).encode("utf-8")
        self.assertEqual((self.tmp / "voices.json").read_bytes(), want)
        self.assertEqual(self.s.get_doc("voices"), VOICES)
        self.assertEqual(self.s.doc_stamp("voices"), (self.tmp / "voices.json").stat().st_mtime_ns)
        got = self.s.get_doc("voices")
        got["provider"] = "changed"  # 拿到的是複本
        self.assertEqual(self.s.get_doc("voices")["provider"], "gemini")

    def test_profile_byte_identical_and_uid_ignored(self):
        ref = self.tmp / "ref.json"
        legacy_atomic_write_text(ref, json.dumps(PROFILE, ensure_ascii=False, indent=2))
        self.s.put_doc("profile:local", PROFILE)
        self.assertEqual((self.tmp / "profile.json").read_bytes(), ref.read_bytes())
        self.assertEqual(self.s.get_doc("profile:someone-else"), PROFILE)
        self.s.delete_doc("profile:local")
        self.assertFalse((self.tmp / "profile.json").exists())
        self.assertIsNone(self.s.get_doc("profile:local"))
        self.s.delete_doc("profile:local")  # 沒有檔案也不會錯

    def test_corrupt_doc_raises_value_error(self):
        (self.tmp / "profile.json").write_text("{壞掉", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.s.get_doc("profile:local")
        (self.tmp / "voices.json").write_text("[1, 2]", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.s.get_doc("voices")
        with self.assertRaises(ValueError):
            self.s.get_doc("unknown")

    def test_profile_with_bom_reads(self):
        (self.tmp / "profile.json").write_bytes("﻿".encode("utf-8") + json.dumps(PROFILE).encode("utf-8"))
        self.assertEqual(self.s.get_doc("profile:local")["surname"], "林")

    def test_custom_scenarios(self):
        text = json.dumps({"id": "custom-abc123", "title": "測試"}, ensure_ascii=False, indent=2) + "\n"
        self.assertEqual(self.s.list_custom(), [])
        ref = self.s.save_custom("custom-abc123", text)
        path = self.tmp / "scenarios" / "custom" / "custom-abc123.json"
        self.assertEqual(Path(ref), path)
        self.assertEqual(path.read_bytes(), text.encode("utf-8"))
        self.assertTrue(self.s.custom_exists("custom-abc123"))
        self.assertFalse(self.s.custom_exists("custom-zzz"))
        self.assertFalse(self.s.custom_exists("../x"))
        self.assertTrue(self.s.is_custom_ref(ref))
        self.assertFalse(self.s.is_custom_ref(self.tmp / "scenarios" / "01.json"))
        self.assertFalse(self.s.is_custom_ref("custom-abc123"))
        # 檔名和 id 不一樣的舊檔：存到原本的檔案
        odd = self.tmp / "scenarios" / "custom" / "My Old.json"
        odd.write_text("{}", encoding="utf-8")
        self.assertEqual(self.s.save_custom("dca-volatility", "X", ref=odd), odd)
        self.assertEqual(odd.read_text(encoding="utf-8"), "X")
        # 不是講師資料夾的 ref 不理它，改用新檔名
        builtin = self.tmp / "scenarios" / "01.json"
        self.assertEqual(self.s.save_custom("dca", "Y", ref=builtin), self.tmp / "scenarios" / "custom" / "dca.json")
        with self.assertRaises(ValueError):
            self.s.save_custom("../evil", "Z")
        rows = self.s.list_custom()
        legacy = [f.name for f in sorted((self.tmp / "scenarios" / "custom").glob("*.json"))]  # 以前的排序（依系統）
        self.assertEqual([r[0] for r in rows], legacy)
        self.assertEqual(sorted(r[0] for r in rows), sorted(["My Old.json", "custom-abc123.json", "dca.json"]))
        row = next(r for r in rows if r[0] == "custom-abc123.json")
        self.assertEqual(row[1], text)
        self.assertIsInstance(row[2], Path)

    def test_list_custom_ignores_tmp_and_trash(self):
        d = self.tmp / "scenarios" / "custom"
        (d / ".trash").mkdir(parents=True)
        (d / ".trash" / "old-1.json").write_text("{}", encoding="utf-8")
        (d / ".b.json-x.tmp").write_text("{}", encoding="utf-8")
        (d / "b.json").write_text("{}", encoding="utf-8")
        (d / "a.json").write_text("{}", encoding="utf-8")
        self.assertEqual([r[0] for r in self.s.list_custom()], ["a.json", "b.json"])

    def test_trash_names_like_before(self):
        d = self.tmp / "scenarios" / "custom"
        d.mkdir(parents=True)
        with mock.patch("storage.time.strftime", return_value="20261005-101010"):
            names = []
            for _ in range(3):
                p = d / "custom-abc.json"
                p.write_text("{}", encoding="utf-8")
                names.append(self.s.trash_custom(p))
        self.assertEqual(names, ["custom-abc-20261005-101010.json", "custom-abc-20261005-101010-2.json",
                                 "custom-abc-20261005-101010-3.json"])
        self.assertFalse((d / "custom-abc.json").exists())
        self.assertEqual(sorted(x.name for x in (d / ".trash").iterdir()), sorted(names))
        with self.assertRaises(ValueError):
            self.s.trash_custom(self.tmp / "scenarios" / "01.json")

    def test_tts_cache_paths(self):
        digest = "a" * 40
        self.assertIsNone(self.s.tts_get(digest))
        self.s.tts_put(digest, b"RIFFwav", "audio/wav")
        self.assertEqual((self.tmp / "tts_cache" / (digest + ".wav")).read_bytes(), b"RIFFwav")
        self.assertEqual(self.s.tts_get(digest), (b"RIFFwav", "audio/wav"))
        d2 = "b" * 40
        self.s.tts_put(d2, b"ID3", "audio/mpeg")
        self.assertEqual(self.s.tts_get(d2), (b"ID3", "audio/mpeg"))
        d3 = "c" * 40
        self.s.tts_put(d3, b"??", "audio/ogg")  # 以前也存成 .bin、但讀不回來
        self.assertTrue((self.tmp / "tts_cache" / (d3 + ".bin")).exists())
        self.assertIsNone(self.s.tts_get(d3))
        self.s.tts_put("../../x", b"bad", "audio/wav")  # 不會寫到外面，也不會丟例外
        self.assertIsNone(self.s.tts_get("../../x"))

    def test_secrets_private_files(self):
        self.assertIsNone(self.s.get_secret("chatgpt-host"))
        self.s.put_secret("chatgpt-host", {"host_id": "urn:uuid:x"})
        path = self.tmp / ".chatgpt" / "chatgpt-host.json"
        self.assertTrue(path.exists())
        self.assertEqual(self.s.get_secret("chatgpt-host"), {"host_id": "urn:uuid:x"})
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        for bad in ("../x", "a/b", "c:d", ""):
            with self.assertRaises(ValueError):
                self.s.put_secret(bad, {})
        path.write_text("壞掉", encoding="utf-8")
        self.assertIsNone(self.s.get_secret("chatgpt-host"))
        self.s.delete_secret("chatgpt-host")
        self.assertFalse(path.exists())
        self.s.delete_secret("chatgpt-host")

    def test_eph_with_expiry(self):
        clock = [1000.0]
        self.s._now = lambda: clock[0]
        self.s.eph_set("a", {"x": 1}, 10)
        self.assertEqual(self.s.eph_get("a"), {"x": 1})
        self.assertEqual(self.s.eph_incr("n", 5), 1)
        self.assertEqual(self.s.eph_incr("n", 5), 2)
        clock[0] += 6
        self.assertEqual(self.s.eph_incr("n", 5), 1)  # 時間窗過了重新算
        self.assertEqual(self.s.eph_pop("a"), {"x": 1})
        self.assertIsNone(self.s.eph_pop("a"))
        self.s.eph_set("b", "v", 1)
        clock[0] += 2
        self.assertIsNone(self.s.eph_get("b"))

    def test_lock_timeout(self):
        held, release = threading.Event(), threading.Event()

        def holder():
            with self.s.lock("x"):
                held.set()
                release.wait(5)

        t = threading.Thread(target=holder)
        t.start()
        held.wait(5)
        t0 = time.monotonic()
        with self.assertRaises(StoreError):
            with self.s.lock("x", wait=0.2):
                pass
        self.assertLess(time.monotonic() - t0, 2)
        with self.s.lock("other", wait=0.2):  # 不同名稱不互相擋
            pass
        release.set()
        t.join()
        with self.s.lock("x", wait=0.2):
            pass

    def test_local_mode_never_imports_cryptography(self):
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); import storage, chatgpt_brain, websession;"
            "s = storage.open_store(sys.argv[2], {}, False); s.put_secret('t', {'a': 1}); s.get_secret('t');"
            "g = chatgpt_brain.ChatGPT(chatgpt_brain.ChatGPTConfig.from_env({}, False), s); g.account_status('local');"
            "print('cryptography' in sys.modules)"
        )
        out = subprocess.run([sys.executable, "-c", code, str(_util.ROOT), str(self.tmp)], capture_output=True, timeout=60)
        self.assertEqual(out.stdout.decode().strip(), "False", out.stderr.decode("utf-8", "replace"))


class OpenStoreTests(unittest.TestCase):
    def test_choice(self):
        tmp = tempfile.mkdtemp()
        self.assertIsInstance(storage.open_store(tmp, {}, False), FileStore)
        m = storage.open_store(tmp, {}, True)
        self.assertIsInstance(m, MemoryStore)
        self.assertFalse(m.durable)
        self.assertTrue(m.warning)
        r = storage.open_store(tmp, {"KV_REST_API_URL": "https://x.upstash.io", "KV_REST_API_TOKEN": "t", "STORE_PREFIX": "p:"}, True)
        self.assertIsInstance(r, RedisStore)
        self.assertEqual(r.prefix, "p:")
        r2 = storage.open_store(tmp, {"UPSTASH_REDIS_REST_URL": "https://y.upstash.io", "UPSTASH_REDIS_REST_TOKEN": "t"}, False)
        self.assertIsInstance(r2, RedisStore)
        self.assertEqual(r2.prefix, "aicoach:")
        self.assertIsInstance(storage.open_store(tmp, {"KV_REST_API_URL": "https://x.upstash.io"}, True), MemoryStore)
        bad = storage.open_store(tmp, {"KV_REST_API_URL": "not a url", "KV_REST_API_TOKEN": "t"}, True)
        self.assertIsInstance(bad, MemoryStore)
        self.assertIn("KV_REST_API_URL", bad.warning)


class MemoryStoreTests(unittest.TestCase):
    def test_basics(self):
        s = MemoryStore()
        s.set_setting("LLM_PROVIDER", "gemini")
        self.assertEqual(s.settings(), {"LLM_PROVIDER": "gemini"})
        self.assertIsNone(s.doc_stamp("voices"))
        s.put_doc("voices", VOICES)
        st1 = s.doc_stamp("voices")
        s.put_doc("voices", dict(VOICES, provider="x"))
        self.assertNotEqual(st1, s.doc_stamp("voices"))
        ref = s.save_custom("custom-1", "{}")
        self.assertEqual(ref, "custom-1")
        self.assertTrue(s.is_custom_ref(ref) and s.custom_exists("custom-1"))
        self.assertEqual(s.list_custom(), [("custom-1.json", "{}", "custom-1")])
        name = s.trash_custom(ref)
        self.assertTrue(name.startswith("custom-1-"))
        self.assertEqual(s.list_custom(), [])
        s.tts_put("d" * 40, b"a", "audio/wav")
        self.assertEqual(s.tts_get("d" * 40), (b"a", "audio/wav"))
        s.put_secret("chatgpt-owner", {"a": 1})
        self.assertEqual(s.get_secret("chatgpt-owner"), {"a": 1})
        with s.lock("x", wait=0.1):
            pass


class RedisStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = fake_upstash.start(token="tok-123")

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def setUp(self):
        self.srv.state.c_flushall()
        self.srv.state.fail_eval = False
        self.srv.state.down = False
        self.s = self.store()

    def store(self, **kw):
        args = dict(prefix="t:", cache_ttl=10, app_secret="s" * 40)
        args.update(kw)
        return RedisStore(self.srv.url, "tok-123", **args)

    def raw(self, *cmd):
        return self.srv.state.run(list(cmd))

    def test_settings_cache_and_invalidation(self):
        self.s.set_setting("LLM_PROVIDER", "gemini")
        self.assertEqual(self.s.settings(), {"LLM_PROVIDER": "gemini"})
        self.assertEqual(self.raw("HGET", "t:settings", "LLM_PROVIDER"), "gemini")
        other = self.store()  # 另一個 Vercel 執行個體
        other.set_setting("LLM_PROVIDER", "chatgpt")
        self.assertEqual(self.s.settings()["LLM_PROVIDER"], "gemini")  # 還在快取時間內
        quick = self.store(cache_ttl=0.2)
        self.assertEqual(quick.settings()["LLM_PROVIDER"], "chatgpt")
        other.set_setting("LLM_PROVIDER", "ollama")
        time.sleep(0.3)
        self.assertEqual(quick.settings()["LLM_PROVIDER"], "ollama")
        self.raw("HSET", "t:settings", "GEMINI_API_KEY", "x")
        self.assertNotIn("GEMINI_API_KEY", self.store().settings())
        with self.assertRaises(ValueError):
            self.s.set_setting("APP_SECRET", "x")

    def test_docs_and_stamp(self):
        self.assertIsNone(self.s.get_doc("profile:u1"))
        self.assertIsNone(self.s.doc_stamp("profile:u1"))
        self.s.put_doc("profile:u1", PROFILE)
        self.assertEqual(json.loads(self.raw("GET", "t:doc:profile:u1")), PROFILE)
        self.assertEqual(self.store().get_doc("profile:u1"), PROFILE)
        stamp = self.s.doc_stamp("profile:u1")
        self.assertEqual(stamp, self.s.doc_stamp("profile:u1"))
        self.s.put_doc("profile:u1", dict(PROFILE, surname="王"))
        self.assertNotEqual(stamp, self.s.doc_stamp("profile:u1"))
        self.assertIsNone(self.s.get_doc("profile:u2"))  # 每個 uid 分開
        self.s.delete_doc("profile:u1")
        self.assertIsNone(self.s.get_doc("profile:u1"))
        self.raw("SET", "t:doc:voices", "{壞")
        with self.assertRaises(ValueError):
            self.store().get_doc("voices")

    def test_custom_and_trash_transaction(self):
        self.s.save_custom("custom-b", '{"id":"custom-b"}')
        ref = self.s.save_custom("custom-a", '{"id":"custom-a"}')
        self.assertEqual(ref, "custom-a")
        self.assertEqual([r[0] for r in self.s.list_custom()], ["custom-a.json", "custom-b.json"])
        self.assertTrue(self.s.custom_exists("custom-a"))
        self.assertFalse(self.s.custom_exists("nope"))
        self.assertTrue(self.s.is_custom_ref("custom-a"))
        self.assertFalse(self.s.is_custom_ref(Path("x")))
        with mock.patch("storage.time.strftime", return_value="20261005-101010"):
            self.assertEqual(self.s.trash_custom("custom-a"), "custom-a-20261005-101010")
            self.s.save_custom("custom-a", "again")
            self.assertEqual(self.s.trash_custom("custom-a"), "custom-a-20261005-101010-2")
        self.assertEqual(self.raw("HGET", "t:scenarios:trash", "custom-a-20261005-101010"), '{"id":"custom-a"}')
        self.assertEqual([r[0] for r in self.s.list_custom()], ["custom-b.json"])
        with self.assertRaises(ValueError):
            self.s.trash_custom("custom-a")

    def test_tts(self):
        d = "f" * 40
        self.assertIsNone(self.s.tts_get(d))
        self.s.tts_put(d, b"\x00\x01wav", "audio/wav")
        self.assertEqual(self.s.tts_get(d), (b"\x00\x01wav", "audio/wav"))
        self.assertGreater(self.raw("TTL", "t:tts:" + d), 29 * 86400)
        self.s.tts_put("e" * 40, b"x" * (storage.TTS_MAX_BYTES + 1), "audio/wav")
        self.assertIsNone(self.s.tts_get("e" * 40))
        self.srv.state.down = True
        self.s.tts_put(d, b"x", "audio/wav")  # 資料庫掛了也不丟例外
        self.assertIsNone(self.s.tts_get(d))

    @unittest.skipUnless(HAVE_CRYPTO, "需要 cryptography")
    def test_secrets_are_encrypted(self):
        self.s.put_secret("chatgpt-owner", {"refresh_token": "rt_super_secret", "email": "a@b"})
        raw = self.raw("GET", "t:secret:chatgpt-owner")
        self.assertNotIn("rt_super_secret", raw)
        self.assertNotIn("a@b", raw)
        self.assertEqual(self.store().get_secret("chatgpt-owner")["refresh_token"], "rt_super_secret")
        self.assertIsNone(self.store(app_secret="t" * 40).get_secret("chatgpt-owner"))  # APP_SECRET 換過：解不開
        nokey = self.store(app_secret="")
        self.assertIsNone(nokey.get_secret("chatgpt-owner"))
        with self.assertRaises(StoreError):
            nokey.put_secret("chatgpt-owner", {"a": 1})
        self.s.delete_secret("chatgpt-owner")
        self.assertIsNone(self.s.get_secret("chatgpt-owner"))

    def test_eph(self):
        self.s.eph_set("cg-login:abc", {"v": 1}, 600)
        self.assertEqual(self.store().eph_get("cg-login:abc"), {"v": 1})
        self.assertGreater(self.raw("TTL", "t:eph:cg-login:abc"), 590)
        calls = len(self.srv.state.calls)
        self.assertEqual(self.s.eph_pop("cg-login:abc"), {"v": 1})
        self.assertEqual(self.srv.state.calls[calls:], ["GET", "DEL"])
        self.assertIsNone(self.s.eph_pop("cg-login:abc"))
        self.assertEqual([self.s.eph_incr("rl:1.2.3.4", 600) for _ in range(3)], [1, 2, 3])
        self.assertGreater(self.raw("TTL", "t:eph:rl:1.2.3.4"), 590)

    def test_lock_and_fresh_reads(self):
        a, b = self.store(), self.store()
        a.put_doc("voices", {"n": 1})
        self.assertEqual(b.get_doc("voices"), {"n": 1})  # b 快取起來
        a.put_doc("voices", {"n": 2})
        self.assertEqual(b.get_doc("voices"), {"n": 1})
        with b.lock("voices"):
            self.assertEqual(b.get_doc("voices"), {"n": 2})  # 拿著鎖時一定讀最新的
            self.assertIsNotNone(self.raw("GET", "t:lock:voices"))
            with self.assertRaises(StoreError):
                with a.lock("voices", wait=0.3):
                    pass
        self.assertIsNone(self.raw("GET", "t:lock:voices"))
        self.srv.state.fail_eval = True
        with a.lock("voices", wait=1):
            pass
        self.assertIsNone(self.raw("GET", "t:lock:voices"))  # EVAL 不能用時也會解鎖

    def test_lock_serializes_threads(self):
        order, inside = [], [0]

        def work(i):
            with self.store().lock("cg-refresh:owner", wait=10):
                inside[0] += 1
                order.append(inside[0])
                time.sleep(0.05)
                inside[0] -= 1

        ts = [threading.Thread(target=work, args=(i,)) for i in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(order, [1, 1, 1, 1])

    def test_errors_do_not_leak_token(self):
        bad = RedisStore(self.srv.url, "wrong-token", prefix="t:")
        with self.assertRaises(StoreError) as cm:
            bad.settings()
        self.assertNotIn("wrong-token", str(cm.exception))
        with self.assertRaises(StoreError):
            self.s.cmd("NOPE")
        dead = RedisStore(f"http://127.0.0.1:{_util.free_port()}", "tok-123", timeout=1)  # 沒人在聽的埠
        t0 = time.monotonic()
        with self.assertRaises(StoreError):
            dead.get_doc("voices")
        self.assertLess(time.monotonic() - t0, 6)
        self.srv.state.down = True
        with self.assertRaises(StoreError):
            self.s.settings()


if __name__ == "__main__":
    unittest.main()
