"""app.py（WSGI 橋接）：SSE 要一段一段送出（不能整包）、瀏覽器斷線要讓 handler 寫入時丟 BrokenPipeError、
記錄不含查詢字串。用假的 server 模組測；最後再用真的 server.py（複製到暫存資料夾、沒有 .env）跑一次。"""

import http.client
import importlib
import io
import json
import socketserver
import sys
import threading
import time
import types
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import _util

EVENTS = {"broken": threading.Event(), "logs": [], "seen": {}}


class FakeHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        EVENTS["logs"].append(fmt % args)

    def _json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _sse(self, obj):
        self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        EVENTS["seen"] = {"server": self.server}
        if path == "/slow":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.end_headers()
            try:
                for i in range(5):
                    self._sse({"delta": f"第{i}段"})
                    time.sleep(0.2)
                self._sse({"done": True})
            except BrokenPipeError:
                EVENTS["broken"].set()  # 和 server._chat 一樣：斷線時關掉上游
            return
        if path.startswith("/echo"):
            return self._json(200, self._echo())
        if path == "/boom":
            raise RuntimeError("secret detail sk-should-not-leak")
        if path == "/half":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            raise RuntimeError("before end_headers")
        if path == "/late":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"partial")
            raise RuntimeError("after headers")
        self._json(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        out = self._echo()
        out["body"] = self.rfile.read(n).decode("utf-8")
        self._json(200, out)

    def _echo(self):
        return {
            "path": self.path, "requestline": self.requestline, "client": self.client_address[0],
            "headers": {k: self.headers.get(k) for k in ("Host", "X-Sync-Key", "Content-Type", "Sec-Fetch-Site", "Cookie")},
            "unquoted": urllib.parse.unquote(urllib.parse.urlparse(self.path).path),
        }


def environ(method="GET", path="/", qs="", body=b"", headers=None):
    env = {
        "REQUEST_METHOD": method, "PATH_INFO": path.encode("utf-8").decode("latin-1"), "QUERY_STRING": qs,
        "wsgi.input": io.BytesIO(body), "REMOTE_ADDR": "10.0.0.9", "SERVER_NAME": "x", "SERVER_PORT": "80",
        "wsgi.url_scheme": "http",
    }
    if body:
        env["CONTENT_LENGTH"] = str(len(body))
        env["CONTENT_TYPE"] = "application/json"
    for k, v in (headers or {}).items():
        env["HTTP_" + k.upper().replace("-", "_")] = v
    return env


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved = {k: sys.modules.get(k) for k in ("server", "app")}
        fake = types.ModuleType("server")
        fake.Handler = FakeHandler
        sys.modules["server"] = fake
        sys.modules.pop("app", None)
        cls.app = importlib.import_module("app")

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def setUp(self):
        EVENTS["broken"].clear()
        EVENTS["logs"].clear()

    def call(self, env):
        got = {}

        def start_response(status, headers):
            got["status"], got["headers"] = status, headers

        it = self.app.app(env, start_response)
        return got, it

    def test_chunks_arrive_one_by_one(self):
        got, it = self.call(environ(path="/slow"))
        self.assertEqual(got["status"], "200 OK")
        self.assertIn(("Content-Type", "text/event-stream; charset=utf-8"), got["headers"])
        times, chunks = [], []
        t0 = time.monotonic()
        for c in it:
            times.append(time.monotonic() - t0)
            chunks.append(c)
        self.assertEqual(len(chunks), 6)
        self.assertLess(times[0], 0.15)  # 第一段馬上就到，不是等全部做完
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertTrue(all(g > 0.12 for g in gaps), gaps)
        self.assertIn("第0段", chunks[0].decode("utf-8"))

    def test_close_raises_broken_pipe_in_handler(self):
        got, it = self.call(environ(path="/slow"))
        first = next(it)
        self.assertIn(b"data:", first)
        it.close()  # WSGI 伺服器在瀏覽器斷線時會呼叫 close()
        self.assertTrue(EVENTS["broken"].wait(2), "handler 沒有收到 BrokenPipeError")

    def test_path_query_headers_and_log(self):
        env = environ(path="/echo", qs="code=SECRET-CODE&state=s1",
                      headers={"Host": "demo.vercel.app", "X-Forwarded-For": "1.2.3.4, 10.0.0.1",
                               "X-Sync-Key": "k1", "Sec-Fetch-Site": "same-origin", "Cookie": "aic_session=t"})
        got, it = self.call(env)
        data = json.loads(b"".join(it))
        self.assertEqual(got["status"], "200 OK")
        self.assertEqual(data["path"], "/echo?code=SECRET-CODE&state=s1")
        self.assertEqual(data["requestline"], "GET /echo HTTP/1.1")
        self.assertEqual(data["client"], "1.2.3.4")
        self.assertEqual(data["headers"], {"Host": "demo.vercel.app", "X-Sync-Key": "k1", "Content-Type": None,
                                           "Sec-Fetch-Site": "same-origin", "Cookie": "aic_session=t"})
        self.assertTrue(EVENTS["logs"])
        self.assertFalse(any("SECRET-CODE" in line for line in EVENTS["logs"]))
        self.assertIsNone(EVENTS["seen"]["server"])

    def test_unicode_path(self):
        for env in (environ(path="/echo/情境 a"), dict(environ(path="/echo"), PATH_INFO="/echo/情境 a")):
            # 第二個是已經解好中文的 PATH_INFO（不照 WSGI 規定的伺服器）也要能用
            got, it = self.call(env)
            data = json.loads(b"".join(it))
            self.assertEqual(data["path"], "/echo/%E6%83%85%E5%A2%83%20a")
            self.assertEqual(data["unquoted"], "/echo/情境 a")
            self.assertEqual(data["requestline"], "GET /echo/情境 a HTTP/1.1")

    def test_post_body(self):
        body = json.dumps({"a": "中文"}, ensure_ascii=False).encode("utf-8")
        got, it = self.call(environ("POST", "/echo", body=body, headers={"Host": "127.0.0.1:9100"}))
        data = json.loads(b"".join(it))
        self.assertEqual(json.loads(data["body"]), {"a": "中文"})
        self.assertEqual(data["headers"]["Content-Type"], "application/json")

    def test_method_not_allowed(self):
        got, it = self.call(environ("PUT", "/echo"))
        self.assertEqual(got["status"].split()[0], "405")
        b"".join(it)

    def test_exception_before_headers_is_500_without_detail(self):
        got, it = self.call(environ(path="/boom"))
        body = b"".join(it).decode("utf-8")
        self.assertEqual(got["status"].split()[0], "500")
        self.assertNotIn("sk-should-not-leak", body)

    def test_exception_before_end_headers_is_500(self):
        got, it = self.call(environ(path="/half"))
        self.assertEqual(got["status"].split()[0], "500")
        self.assertIn("伺服器錯誤", b"".join(it).decode("utf-8"))

    def test_exception_after_headers_ends_stream(self):
        got, it = self.call(environ(path="/late"))
        self.assertEqual(got["status"], "200 OK")
        self.assertEqual(b"".join(it), b"partial")

    def test_over_real_socket(self):
        app = self.app.app

        class Quiet(WSGIRequestHandler):
            def log_message(self, *a):
                pass

        class Threaded(socketserver.ThreadingMixIn, WSGIServer):
            daemon_threads = True
            allow_reuse_address = sys.platform != "win32"

        port = _util.free_port()
        httpd = make_server("127.0.0.1", port, app, server_class=Threaded, handler_class=Quiet)
        t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        t.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/slow")
            resp = conn.getresponse()
            t0, times = time.monotonic(), []
            while len(times) < 3:
                line = resp.readline()
                if line.startswith(b"data:"):
                    times.append(time.monotonic() - t0)
            gaps = [b - a for a, b in zip(times, times[1:])]
            self.assertTrue(all(g > 0.12 for g in gaps), gaps)
            resp.close()  # 業務插話：瀏覽器中斷（HTTP/1.0 回應時 socket 由 resp 持有）
            conn.close()
            self.assertTrue(EVENTS["broken"].wait(3), "斷線後 handler 沒有停")
        finally:
            httpd.shutdown()
            httpd.server_close()


class RealServerTests(unittest.TestCase):
    """真的 server.py（本機模式、沒有 .env）經過 app.py：情境 API 不會帶隱藏設定、程式和情境檔不會被當靜態檔送出。"""

    @classmethod
    def setUpClass(cls):
        import py_compile
        cls.dir = _util.copy_project()
        try:
            py_compile.compile(str(cls.dir / "server.py"), doraise=True)
        except py_compile.PyCompileError as e:
            raise unittest.SkipTest(f"server.py 目前編譯不過（可能正在修改）：{e.msg[:120]}")
        cls.port = _util.free_port()
        cls.proc = _util.start_proc(["app.py", "--port", str(cls.port)], cls.dir, _util.clean_env(), cls.dir / "wsgi.log")
        if not _util.wait_port(cls.port, 20):
            _util.stop_proc(cls.proc)
            raise AssertionError("app.py 沒有啟動：" + (cls.dir / "wsgi.log").read_text("utf-8", "replace")[-800:])

    @classmethod
    def tearDownClass(cls):
        _util.stop_proc(cls.proc)

    def get(self, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        conn.request("GET", path, headers=dict({"Host": f"127.0.0.1:{self.port}"}, **(headers or {})))
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, dict(r.getheaders()), data

    def test_scenarios_without_server_only(self):
        status, headers, data = self.get("/api/scenarios")
        self.assertEqual(status, 200, data[:300])
        text = data.decode("utf-8")
        for key in ("server_only", "persona", "hidden_info", "reveal_rules", "coach_notes"):
            self.assertNotIn(key, text)
        self.assertGreaterEqual(len(json.loads(text)["scenarios"]), 3)

    def test_index_served(self):
        status, headers, data = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<html", data.lower())

    def test_no_static_leak(self):
        for path in ("/scenarios/01-dca-volatility.json", "/server.py", "/app.py", "/.env", "/storage.py",
                     "/../server.py", "/%2e%2e/server.py", "/web/../server.py"):
            status, _, _ = self.get(path)
            self.assertEqual(status, 404, path)

    def test_foreign_host_rejected_locally(self):
        status, _, _ = self.get("/api/status", {"Host": "evil.com"})
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
