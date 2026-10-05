"""假的 Upstash Redis REST API（只用來測試）：POST /、/pipeline、/multi-exec，回 {"result": …} 或 {"error": …}。
用法：python tools/fakes/fake_upstash.py --port 9101 --token test-token
程式裡：srv = fake_upstash.start(token="t")；srv.url、srv.state（Upstash 物件）、srv.stop()。"""

import fnmatch
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import Handler, run_forever, serve  # noqa: E402

UNLOCK_HINT = ('redis.call("get"', 'redis.call("del"')


class RedisErr(Exception):
    pass


class Upstash:
    def __init__(self, token="test-token"):
        self.token = token
        self.lock = threading.RLock()
        self.data = {}  # 字串或 dict（hash）
        self.expiry = {}  # key → time.monotonic() 到期
        self.calls = []  # 每個指令的名稱（測快取用）
        self.fail_eval = False  # 模擬不支援 EVAL
        self.down = False  # 模擬資料庫掛掉（回 500）

    # ---- 指令

    def _alive(self, key):
        exp = self.expiry.get(key)
        if exp is not None and exp <= time.monotonic():
            self.data.pop(key, None)
            self.expiry.pop(key, None)
        return key in self.data

    def _str(self, key):
        if not self._alive(key):
            return None
        v = self.data[key]
        if isinstance(v, dict):
            raise RedisErr("WRONGTYPE Operation against a key holding the wrong kind of value")
        return v

    def _hash(self, key, create=False):
        if not self._alive(key):
            if not create:
                return {}
            self.data[key] = {}
        v = self.data[key]
        if not isinstance(v, dict):
            raise RedisErr("WRONGTYPE Operation against a key holding the wrong kind of value")
        return v

    def run(self, cmd):
        if not isinstance(cmd, list) or not cmd or not all(isinstance(x, (str, int, float)) for x in cmd):
            raise RedisErr("ERR bad command")
        cmd = [str(x) for x in cmd]
        name, args = cmd[0].upper(), cmd[1:]
        with self.lock:
            self.calls.append(name)
            fn = getattr(self, "c_" + name.lower().replace("-", "_"), None)
            if fn is None:
                raise RedisErr(f"ERR unknown command '{cmd[0]}'")
            try:
                return fn(*args)
            except TypeError:
                raise RedisErr(f"ERR wrong number of arguments for '{name.lower()}' command") from None

    def c_ping(self):
        return "PONG"

    def c_flushall(self):
        self.data.clear()
        self.expiry.clear()
        return "OK"

    def c_get(self, key):
        return self._str(key)

    def c_set(self, key, value, *opts):
        opts = [o.upper() for o in opts]
        ttl = None
        i = 0
        nx = xx = False
        while i < len(opts):
            o = opts[i]
            if o in ("EX", "PX"):
                if i + 1 >= len(opts):
                    raise RedisErr("ERR syntax error")
                n = float(opts[i + 1])
                if n <= 0:
                    raise RedisErr("ERR invalid expire time in 'set' command")
                ttl = n if o == "EX" else n / 1000.0
                i += 2
                continue
            if o == "NX":
                nx = True
            elif o == "XX":
                xx = True
            else:
                raise RedisErr("ERR syntax error")
            i += 1
        exists = self._alive(key)
        if (nx and exists) or (xx and not exists):
            return None
        self.data[key] = value
        self.expiry.pop(key, None)
        if ttl is not None:
            self.expiry[key] = time.monotonic() + ttl
        return "OK"

    def c_del(self, *keys):
        n = 0
        for k in keys:
            if self._alive(k):
                n += 1
                self.data.pop(k, None)
                self.expiry.pop(k, None)
        return n

    def c_exists(self, *keys):
        return sum(1 for k in keys if self._alive(k))

    def c_incr(self, key):
        v = self._str(key)
        try:
            n = int(v or 0) + 1
        except ValueError:
            raise RedisErr("ERR value is not an integer or out of range") from None
        self.data[key] = str(n)
        return n

    def c_expire(self, key, seconds, *opts):
        if not self._alive(key):
            return 0
        self.expiry[key] = time.monotonic() + float(seconds)
        return 1

    def c_pexpire(self, key, ms):
        return self.c_expire(key, float(ms) / 1000.0)

    def c_ttl(self, key):
        if not self._alive(key):
            return -2
        exp = self.expiry.get(key)
        return -1 if exp is None else max(0, int(round(exp - time.monotonic())))

    def c_hget(self, key, field):
        return self._hash(key).get(field)

    def c_hset(self, key, *pairs):
        if not pairs or len(pairs) % 2:
            raise RedisErr("ERR wrong number of arguments for 'hset' command")
        h = self._hash(key, create=True)
        new = 0
        for f, v in zip(pairs[0::2], pairs[1::2]):
            new += f not in h
            h[f] = v
        return new

    def c_hdel(self, key, *fields):
        h = self._hash(key)
        n = 0
        for f in fields:
            if f in h:
                del h[f]
                n += 1
        if not h and key in self.data:
            self.data.pop(key, None)
        return n

    def c_hgetall(self, key):
        out = []
        for f, v in self._hash(key).items():
            out += [f, v]
        return out

    def c_hexists(self, key, field):
        return 1 if field in self._hash(key) else 0

    def c_hlen(self, key):
        return len(self._hash(key))

    def c_keys(self, pattern):
        return sorted(k for k in list(self.data) if self._alive(k) and fnmatch.fnmatchcase(k, pattern))

    def c_dbsize(self):
        return sum(1 for k in list(self.data) if self._alive(k))

    def c_eval(self, script, numkeys, *rest):
        if self.fail_eval:
            raise RedisErr("ERR EVAL is disabled")
        n = int(numkeys)
        keys, argv = rest[:n], rest[n:]
        # 只支援程式用到的「比對後刪除」腳本
        if all(h in script for h in UNLOCK_HINT) and n == 1 and len(argv) == 1:
            return self.c_del(keys[0]) if self._str(keys[0]) == argv[0] else 0
        raise RedisErr("ERR unsupported script in fake")

    # ---- HTTP

    def handle(self, path, payload):
        """回傳 (HTTP 狀態, JSON)。"""
        if self.down:
            return 500, {"error": "ERR fake is down"}
        if path in ("", "/"):
            try:
                return 200, {"result": self.run(payload)}
            except RedisErr as e:
                return 400, {"error": str(e)}
        if path in ("/pipeline", "/multi-exec"):
            if not isinstance(payload, list) or not all(isinstance(c, list) for c in payload):
                return 400, {"error": "ERR bad pipeline"}
            out = []
            with self.lock:  # multi-exec：整批一起執行，中間不會插進別的指令
                for c in payload:
                    try:
                        out.append({"result": self.run(c)})
                    except RedisErr as e:
                        out.append({"error": str(e)})
            return 200, out
        return 404, {"error": "not found"}


class H(Handler):
    def do_POST(self):
        st = self.state
        if self.headers.get("Authorization") != "Bearer " + st.token:
            return self.reply(401, {"error": "Unauthorized"})
        payload = self.body_json()
        code, obj = st.handle(self.path.split("?", 1)[0].rstrip("/") or "/", payload)
        self.reply(code, obj)

    def do_GET(self):
        self.reply(405, {"error": "use POST"})


def start(token="test-token", port=None):
    state = Upstash(token)
    srv = serve(H, port, state)
    return srv


if __name__ == "__main__":
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1]) if "--port" in args else 9101
    token = args[args.index("--token") + 1] if "--token" in args else "test-token"
    run_forever(H, port, Upstash(token), "fake upstash")
