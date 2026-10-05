"""Vercel 的進入點（WSGI）：把每個請求交給 server.Handler 處理，回應一段一段串流回去。
本機測試：python app.py --port 9123（用 wsgiref，只綁 127.0.0.1）。"""

import http.client
import queue
import sys
import threading
from urllib.parse import quote

import server

_DONE = object()


class _Out:
    """代替 socket 的 wfile：寫進佇列，WSGI 那邊一段一段拿出去送。"""

    def __init__(self):
        self.q = queue.Queue()
        self.closed = False

    def write(self, b):
        if self.closed:
            raise BrokenPipeError()  # 瀏覽器斷線（業務插話）：讓 _chat 原本的 except 關掉上游 AI
        if b:
            self.q.put(bytes(b))
        return len(b)

    def flush(self):
        if self.closed:
            raise BrokenPipeError()


class BridgeHandler(server.Handler):
    server = None  # 沒有 socket 伺服器；本機登入（要 127.0.0.1 的埠）看到 None 會拒絕

    def __init__(self, env):  # 不呼叫 BaseHTTPRequestHandler.__init__：它會去讀 socket
        raw = env.get("PATH_INFO") or "/"
        try:
            raw = raw.encode("latin-1").decode("utf-8", "replace")  # WSGI 規定 PATH_INFO 用 latin-1 裝原始位元組
        except UnicodeEncodeError:
            pass  # 已經是解好的中文
        qs = env.get("QUERY_STRING") or ""
        self.command = (env.get("REQUEST_METHOD") or "GET").upper()
        self.path = quote(raw, safe="/:@!$&'()*+,;=-._~") + ("?" + qs if qs else "")
        self.request_version, self.close_connection = "HTTP/1.1", True
        self.requestline = f"{self.command} {raw} HTTP/1.1"  # 不含查詢字串：OAuth 的 code 不會進記錄
        forwarded = env.get("HTTP_X_FORWARDED_FOR") or env.get("REMOTE_ADDR") or ""
        self.client_address = (forwarded.split(",")[0].strip(), 0)
        h = http.client.HTTPMessage()
        for k, v in env.items():
            if k.startswith("HTTP_"):
                h[k[5:].replace("_", "-").title()] = v
        for k, n in (("CONTENT_TYPE", "Content-Type"), ("CONTENT_LENGTH", "Content-Length")):
            if env.get(k):
                h[n] = env[k]
        self.headers, self.rfile, self.wfile = h, env["wsgi.input"], _Out()
        self._st, self._hd, self._ready = None, [], threading.Event()

    def send_response(self, code, message=None):
        self.log_request(code)
        self._st = f"{code} {message or self.responses.get(code, ('',))[0]}"
        self._hd = []

    def send_header(self, keyword, value):
        self._hd.append((keyword, str(value)))

    def end_headers(self):
        self._ready.set()


def app(environ, start_response):
    h = BridgeHandler(environ)

    def run():
        try:
            fn = getattr(h, "do_" + h.command, None)
            fn() if fn else h._json(405, {"error": "method not allowed"})
        except Exception as e:
            h.log_message("處理 %s 失敗：%r", h.requestline.split(" ", 1)[1].rsplit(" ", 1)[0], e)
            if not h._ready.is_set():  # 標頭還沒送出：改回 500（不帶錯誤細節）
                try:
                    h._json(500, {"error": "伺服器錯誤"})
                except Exception:
                    h._st, h._hd = None, []
        finally:
            h._ready.set()
            h.wfile.q.put(_DONE)

    threading.Thread(target=run, daemon=True).start()
    h._ready.wait()
    start_response(h._st or "500 Internal Server Error", h._hd)

    def body():
        try:
            while True:
                chunk = h.wfile.q.get()
                if chunk is _DONE:
                    break
                yield chunk
        finally:
            h.wfile.closed = True  # 不論正常結束或被中斷，之後的寫入都會丟 BrokenPipeError

    return body()


def _serve(port):
    """本機測試用：多執行緒的 wsgiref（SSE 串流時其他請求也能進來）。"""
    import socketserver
    from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

    class Quiet(WSGIRequestHandler):
        def log_request(self, code="-", size="-"):
            pass  # server.Handler 自己會記（不含查詢字串）；這裡再記會重複，還會帶到 OAuth 的 code

    class Threaded(socketserver.ThreadingMixIn, WSGIServer):
        daemon_threads = True
        allow_reuse_address = sys.platform != "win32"  # Windows 上才不會搶到別人正在用的埠

    httpd = make_server("127.0.0.1", port, app, server_class=Threaded, handler_class=Quiet)
    print(f"WSGI 測試伺服器：http://127.0.0.1:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    args = sys.argv[1:]
    _serve(int(args[args.index("--port") + 1]) if "--port" in args else 9123)
