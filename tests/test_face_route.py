"""通話畫面的客戶底片和照片（/face/…）：只送 face/ 裡固定檔名的檔案、不能跳出資料夾、沒有就 404，影片支援 Range。
用複製到暫存資料夾的 server.py（沒有 .env），face/ 放假的檔案。"""

import http.client
import unittest

import _util

CLIP = bytes(range(256)) * 40  # 10240 bytes
PHOTO = b"RIFF\x00\x00\x00\x00WEBPfake"


class FaceRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = _util.copy_project()
        face = cls.dir / "face"
        face.mkdir()
        (face / "wang_idle.mp4").write_bytes(CLIP)
        (face / "wang.webp").write_bytes(PHOTO)
        (face / "lin.jpg").write_bytes(b"\xff\xd8jpeg")
        (face / "chen.png").write_bytes(b"\x89PNGfake")
        (face / "secret.txt").write_bytes(b"no")
        (face / "wang.mp4").write_bytes(b"no")
        (face / "wang_idle.webm").write_bytes(b"no")
        cls.port = _util.free_port()
        env = _util.clean_env({"PORT": str(cls.port)})
        cls.proc = _util.start_proc(["server.py", "--no-browser"], cls.dir, env, cls.dir / "face.log")
        if not _util.wait_port(cls.port, 25):
            _util.stop_proc(cls.proc)
            raise AssertionError("server.py 沒有啟動：" + (cls.dir / "face.log").read_text("utf-8", "replace")[-800:])

    @classmethod
    def tearDownClass(cls):
        _util.stop_proc(cls.proc)

    def get(self, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        conn.request("GET", path, headers=dict({"Host": f"127.0.0.1:{self.port}"}, **(headers or {})))
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, data

    def test_allowed_files(self):
        for path, body, mime in (("/face/wang_idle.mp4", CLIP, "video/mp4"), ("/face/wang.webp", PHOTO, "image/webp"),
                                 ("/face/lin.jpg", b"\xff\xd8jpeg", "image/jpeg"), ("/face/chen.png", b"\x89PNGfake", "image/png")):
            status, headers, data = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertEqual(data, body, path)
            self.assertEqual(headers["content-type"], mime, path)
            self.assertEqual(headers["accept-ranges"], "bytes", path)

    def test_range(self):
        status, headers, data = self.get("/face/wang_idle.mp4", {"Range": "bytes=0-3"})
        self.assertEqual(status, 206)
        self.assertEqual(data, CLIP[:4])
        self.assertEqual(headers["content-range"], f"bytes 0-3/{len(CLIP)}")
        status, headers, data = self.get("/face/wang_idle.mp4", {"Range": "bytes=10000-"})
        self.assertEqual((status, data), (206, CLIP[10000:]))
        status, headers, data = self.get("/face/wang_idle.mp4", {"Range": "bytes=-16"})
        self.assertEqual((status, data), (206, CLIP[-16:]))
        self.assertEqual(headers["content-range"], f"bytes {len(CLIP) - 16}-{len(CLIP) - 1}/{len(CLIP)}")
        status, headers, _ = self.get("/face/wang_idle.mp4", {"Range": f"bytes={len(CLIP)}-"})
        self.assertEqual(status, 416)
        self.assertEqual(headers["content-range"], f"bytes */{len(CLIP)}")
        # 看不懂的範圍（多段）就整個送
        status, _, data = self.get("/face/wang_idle.mp4", {"Range": "bytes=0-1,4-5"})
        self.assertEqual((status, data), (200, CLIP))

    def test_other_names_not_served(self):
        for path in ("/face/secret.txt", "/face/wang.mp4", "/face/wang_idle.webm", "/face/Wang.webp", "/face/wang.WEBP",
                     "/face/wang.gif", "/face/", "/face/wang.webp/", "/face/x/wang.webp", "/face/_idle.mp4",
                     "/face/wang_idle_idle.mp4", "/face/.env", "/face/../server.py", "/face/..%2fserver.py",
                     "/face/%2e%2e/server.py", "/face/%77ang.webp", "/face/wang.webp%00.txt", "/face/..\\server.py",
                     "/face/" + "a" * 33 + ".png"):
            status, _, data = self.get(path)
            self.assertEqual(status, 404, path)
            self.assertNotEqual(data, b"no", path)

    def test_missing_is_404(self):
        self.assertEqual(self.get("/face/chen_idle.mp4")[0], 404)
        self.assertEqual(self.get("/face/lin.webp")[0], 404)
        # 雲端那份沒有 face/ 資料夾：一樣是 404，網頁就改用畫的頭像
        moved = self.dir / "face-away"
        (self.dir / "face").rename(moved)
        try:
            self.assertEqual(self.get("/face/wang_idle.mp4")[0], 404)
            self.assertEqual(self.get("/face/wang.webp")[0], 404)
        finally:
            moved.rename(self.dir / "face")

    def test_web_files_unchanged(self):
        status, _, data = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<html", data.lower())
        self.assertEqual(self.get("/face.log")[0], 404)


if __name__ == "__main__":
    unittest.main()
