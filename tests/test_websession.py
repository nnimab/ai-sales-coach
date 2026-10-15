"""websession.py：簽章、過期、cookie 格式、通行碼比對。"""

import hashlib
import time
import unittest

import _util  # noqa: F401
import websession as ws

SECRET = "x" * 40


class WebSessionTests(unittest.TestCase):
    def payload(self, **kw):
        now = int(time.time())
        p = {"uid": ws.new_uid(), "name": "小林", "role": "user", "pv": ws.passcode_version("1234", SECRET), "iat": now, "exp": now + 60}
        p.update(kw)
        return p

    def test_sign_verify_roundtrip(self):
        p = self.payload()
        tok = ws.sign(p, SECRET)
        self.assertEqual(ws.verify(tok, SECRET), p)
        self.assertNotIn("小林", tok)  # cookie 內容是 base64url，不會有中文
        self.assertRegex(tok, r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")

    def test_tampered_or_wrong_secret(self):
        tok = ws.sign(self.payload(), SECRET)
        body, sig = tok.split(".")
        forged = ws.sign(self.payload(role="admin"), SECRET).split(".")[0] + "." + sig
        self.assertIsNone(ws.verify(forged, SECRET))
        self.assertIsNone(ws.verify(tok, "y" * 40))
        self.assertIsNone(ws.verify(tok, ""))
        for junk in ("", "abc", "a.b.c", ".", "x" * 5000, None):
            self.assertIsNone(ws.verify(junk, SECRET))
        with self.assertRaises(ValueError):
            ws.sign(self.payload(), "")

    def test_expiry(self):
        tok = ws.sign(self.payload(exp=int(time.time()) - 1), SECRET)
        self.assertIsNone(ws.verify(tok, SECRET))
        self.assertIsNotNone(ws.verify(tok, SECRET, check_exp=False))
        self.assertIsNone(ws.verify(ws.sign(self.payload(exp="later"), SECRET), SECRET))

    def test_cookies(self):
        self.assertEqual(ws.parse_cookies('a=1; aic_session="tok"; b=; aic_session=second; bad'),
                         {"a": "1", "aic_session": "tok", "b": ""})
        self.assertEqual(ws.parse_cookies(None), {})
        c = ws.set_cookie("tok", True, 3600)
        for part in ("aic_session=tok", "Max-Age=3600", "Path=/", "HttpOnly", "SameSite=Lax", "Secure"):
            self.assertIn(part, c)
        self.assertNotIn("Secure", ws.set_cookie("tok", False, 10))
        self.assertIn("Max-Age=0", ws.clear_cookie(True))

    def test_passcode(self):
        self.assertTrue(ws.passcode_ok("1234", "1234"))
        self.assertFalse(ws.passcode_ok("12345", "1234"))
        self.assertFalse(ws.passcode_ok("", ""))
        self.assertFalse(ws.passcode_ok(None, "1234"))
        self.assertEqual(len(ws.new_uid()), 32)

    def test_passcode_version_is_keyed(self):
        pv = ws.passcode_version("1234", SECRET)
        self.assertEqual(len(pv), 12)
        self.assertEqual(pv, ws.passcode_version("1234", SECRET))
        self.assertNotEqual(pv, ws.passcode_version("5678", SECRET))
        # 用 APP_SECRET 當鑰匙：cookie 裡看得到 pv，沒有 APP_SECRET 也沒辦法拿通行碼的雜湊去比對猜
        self.assertNotEqual(pv, ws.passcode_version("1234", "y" * 40))
        self.assertNotEqual(pv, hashlib.sha256(b"1234").hexdigest()[:12])  # 舊版：沒有鑰匙的雜湊
        with self.assertRaises(ValueError):
            ws.passcode_version("1234", "")


if __name__ == "__main__":
    unittest.main()
