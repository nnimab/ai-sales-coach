"""假的 Gemini（只用來測試）：OpenAI 相容的 /v1beta/openai/chat/completions（SSE）、Live 通行證 /v1beta/auth_tokens、
語音 /v1beta/interactions、/v1beta/voices。
用法：python tools/fakes/fake_gemini.py --port 9103
server.py 要指到這裡：GEMINI_API_KEY=fake-key、GEMINI_OPENAI_URL=<url>/v1beta/openai、GEMINI_BASE_URL=<url>/v1beta
子程序測試可以用 POST /__control {"busy":503|429|0,"reply":"…","delay":0.1}、GET /__state。"""

import base64
import json
import math
import os
import secrets
import struct
import sys
import threading
import time
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import Handler, run_forever, sample, serve  # noqa: E402


def wav(freq=330, secs=0.3, rate=24000):
    n = int(secs * rate)
    pcm = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate))) for i in range(n))
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE" + b"fmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm)) + pcm)


class GeminiState:
    def __init__(self):
        self.lock = threading.Lock()
        self.base = ""
        self.knobs = {"busy": 0, "reply": "好，我了解了。那我再想一下，你可以再說明一次嗎？", "delay": 0.0, "chunk": 4}
        self.calls = []  # (路徑, 模型)
        self.voices = []

    def snapshot(self):
        with self.lock:
            return {"calls": list(self.calls), "knobs": dict(self.knobs), "voices": len(self.voices)}


def _err(code, message, status="INVALID_ARGUMENT"):
    return {"error": {"code": code, "message": message, "status": status}}


class H(Handler):
    def key_ok(self):
        key = self.headers.get("x-goog-api-key") or ""
        auth = self.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            key = key or auth[7:]
        if not key or key.startswith("bad"):
            self.reply(400, _err(400, "API key not valid. Please pass a valid API key."))
            return False
        return True

    def do_GET(self):
        st = self.state
        u = urlparse(self.path)
        if u.path == "/__state":
            return self.reply(200, st.snapshot())
        if not self.key_ok():
            return
        if u.path == "/v1beta/voices":
            return self.reply(200, {"voices": list(st.voices)})
        self.reply(404, _err(404, "not found", "NOT_FOUND"))

    def do_POST(self):
        st = self.state
        u = urlparse(self.path)
        if u.path == "/__control":
            body = self.body_json() or {}
            with st.lock:
                st.knobs.update(body)
            return self.reply(200, {"ok": True})
        body = self.body_json()
        if not self.key_ok():
            return
        if not isinstance(body, dict):
            return self.reply(400, _err(400, "bad json"))
        with st.lock:
            st.calls.append((u.path, body.get("model", "")))
            k = dict(st.knobs)
        if u.path == "/v1beta/openai/chat/completions":
            if k.get("busy"):
                code = int(k["busy"])
                msg = "The model is overloaded. Please try again later." if code == 503 else "Resource has been exhausted"
                return self.reply(code, [_err(code, msg, "UNAVAILABLE" if code == 503 else "RESOURCE_EXHAUSTED")])
            return self.chat(body, k)
        if u.path == "/v1beta/auth_tokens":
            return self.reply(200, {"name": "auth_tokens/fake-" + secrets.token_hex(6)})
        if u.path == "/v1beta/interactions":
            audio = base64.b64encode(wav()).decode()
            return self.reply(200, {"id": "int_" + secrets.token_hex(4), "status": "completed", "steps": [
                {"type": "model_output", "content": [{"type": "audio", "mime_type": "audio/wav", "data": audio}]}]})
        if u.path == "/v1beta/voices":
            vid = "voice_" + secrets.token_hex(6)
            v = body.get("voice") or {}
            row = {"id": vid, "display_name": v.get("display_name"), "type": "prompted", "gender": v.get("gender"),
                   "prompted": v.get("prompted")}
            with st.lock:
                st.voices.insert(0, row)
            return self.reply(200, row)
        self.reply(404, _err(404, "not found", "NOT_FOUND"))

    def chat(self, body, k):
        fmt = body.get("response_format") or {}
        if fmt.get("type") == "json_schema":
            text = json.dumps(sample((fmt.get("json_schema") or {}).get("schema") or {}), ensure_ascii=False)
        elif fmt.get("type") == "json_object":
            text = "{}"
        else:
            text = k["reply"]
        n = max(1, int(k.get("chunk") or 4))
        pieces = [text[i:i + n] for i in range(0, len(text), n)]
        if not body.get("stream"):
            return self.reply(200, {"choices": [{"index": 0, "message": {"role": "assistant", "content": text}}]})
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for p in pieces:
                chunk = {"choices": [{"index": 0, "delta": {"content": p}}], "model": body.get("model", "")}
                self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode("utf-8"))
                self.wfile.flush()
                if k.get("delay"):
                    time.sleep(float(k["delay"]))
            self.wfile.write(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def start(port=None):
    state = GeminiState()
    srv = serve(H, port, state)
    state.base = srv.url
    return srv


if __name__ == "__main__":
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1]) if "--port" in args else 9103
    run_forever(H, port, GeminiState(), "fake gemini")
