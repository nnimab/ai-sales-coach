"""假服務共用的小工具（只用標準函式庫）：在 127.0.0.1 的 9100～9199 開伺服器、簽 RS256 JWT、依 JSON schema 產生假資料。
那一段埠被別的程式占著時，可以用環境變數 FAKE_PORT_BASE 換一段（例如 9400 → 9400～9499）。"""

import base64
import hashlib
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

_PORT_BASE = int(os.environ.get("FAKE_PORT_BASE") or 9100)
PORT_RANGE = range(_PORT_BASE, _PORT_BASE + 100)
_DIGEST_INFO_SHA256 = bytes.fromhex("3031300d060960864801650304020105000420")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # Windows 的 SO_REUSEADDR 會讓兩個程式綁同一個埠：關掉才不會搶到別人正在用的埠
    allow_reuse_address = sys.platform != "win32"


def free_port(exclude=()):
    """PORT_RANGE（預設 9100～9199）裡第一個能綁的埠（給子程序用；自己開伺服器請用 serve）。"""
    for port in PORT_RANGE:
        if port in exclude:
            continue
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            continue
        finally:
            s.close()
        return port
    raise RuntimeError(f"{PORT_RANGE.start}～{PORT_RANGE.stop - 1} 沒有空的埠")


def serve(handler_cls, port=None, state=None):
    """在背景執行緒開伺服器；port 不給就從 PORT_RANGE 的開頭往上找。回傳 server（server.url、server.stop()）。"""
    ports = [port] if port else list(PORT_RANGE)
    last = None
    for p in ports:
        try:
            srv = Server(("127.0.0.1", p), handler_cls)
            break
        except OSError as e:
            last = e
    else:
        raise RuntimeError(f"開不了假服務：{last}")
    srv.state = state
    srv.url = f"http://127.0.0.1:{srv.server_port}"
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    t.start()

    def stop():
        srv.shutdown()
        srv.server_close()

    srv.stop = stop
    return srv


def run_forever(handler_cls, port, state, name):
    srv = Server(("127.0.0.1", port), handler_cls)
    srv.state = state
    srv.url = f"http://127.0.0.1:{srv.server_port}"
    if hasattr(state, "base"):
        state.base = srv.url
    print(f"{name}: {srv.url}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


class Handler(BaseHTTPRequestHandler):
    """共用的回應工具；子類別實作 do_GET / do_POST。"""

    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        pass  # 假服務不印記錄（網址裡可能有測試用的 code）

    @property
    def state(self):
        return self.server.state

    def body_bytes(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def body_json(self):
        try:
            return json.loads(self.body_bytes().decode("utf-8") or "null")
        except ValueError:
            return None

    def body_form(self):
        return {k: v[0] for k, v in parse_qs(self.body_bytes().decode("utf-8"), keep_blank_values=True).items()}

    def reply(self, code, obj, headers=None):
        data = b"" if obj is None else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            if obj is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()


# ---------------------------------------------------------------- JWT


def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def rs256_sign(n, d, signing_input):
    k = (n.bit_length() + 7) // 8
    t = _DIGEST_INFO_SHA256 + hashlib.sha256(signing_input).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")


def make_jwt(claims, n, d, kid, alg="RS256"):
    h = b64u(json.dumps({"alg": alg, "kid": kid, "typ": "JWT"}, separators=(",", ":")).encode())
    p = b64u(json.dumps(claims, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    sig = rs256_sign(n, d, (h + "." + p).encode("ascii"))
    return h + "." + p + "." + b64u(sig)


def jwk(n, e, kid):
    return {
        "kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256",
        "n": b64u(n.to_bytes((n.bit_length() + 7) // 8, "big")),
        "e": b64u(e.to_bytes((e.bit_length() + 7) // 8, "big")),
    }


# ---------------------------------------------------------------- 依 schema 產生假 JSON


def sample(schema, depth=0):
    if not isinstance(schema, dict) or depth > 8:
        return None
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    for key in ("anyOf", "oneOf"):
        if schema.get(key):
            return sample(schema[key][0], depth + 1)
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "null")
    if t == "object" or "properties" in schema:
        return {k: sample(v, depth + 1) for k, v in (schema.get("properties") or {}).items()}
    if t == "array":
        n = int(schema.get("minItems") or 0)
        return [sample(schema.get("items") or {}, depth + 1) for _ in range(n)]
    if t == "string":
        return "好" * max(1, int(schema.get("minLength") or 1))
    if t in ("integer", "number"):
        lo, hi = schema.get("minimum"), schema.get("maximum")
        if lo is not None and hi is not None:
            v = (lo + hi) / 2
        else:
            v = lo if lo is not None else 1
        return int(v) if t == "integer" else v
    if t == "boolean":
        return True
    return None
