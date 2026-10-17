# Session lifecycle checks (pod-side, tokens minted here): WS reconnect, auto-stop on max_seconds,
# 60 s after the WS closed, and 60 s when no WS ever connects. Prints only results.
import asyncio, json, os, sys, time, hmac, hashlib, secrets
import aiohttp

import base64


def penv():
    e = {}
    try:
        for kv in open("/proc/1/environ", "rb").read().split(b"\0"):
            if b"=" in kv:
                k, v = kv.split(b"=", 1)
                e[k.decode()] = v.decode()
    except OSError:
        pass
    e.update({k: v for k, v in os.environ.items() if k.startswith(("LIVEKIT_", "FACE_SECRET"))})
    return e


def _b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def lk_token(key, secret, identity, room, ttl, pub, sub, data):
    now = int(time.time())
    pl = {"iss": key, "sub": identity, "nbf": now - 10, "exp": now + ttl, "name": identity,
          "video": {"roomJoin": True, "room": room, "canPublish": pub, "canSubscribe": sub, "canPublishData": data}}
    si = _b64u(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode()) + "." + \
        _b64u(json.dumps(pl, separators=(",", ":")).encode())
    return si + "." + _b64u(hmac.new(secret.encode(), si.encode(), hashlib.sha256).digest())



BASE = "http://127.0.0.1:8010"


async def wait_stopped(http, t0, limit):
    while time.monotonic() - t0 < limit:
        async with http.get(BASE + "/face/health") as r:
            if (await r.json())["sessions"] == 0:
                return round(time.monotonic() - t0, 1)
        await asyncio.sleep(1)
    return None


async def main():
    E = penv()
    hdr = {"X-Face-Secret": E["FACE_SECRET"]}
    res = {}
    async with aiohttp.ClientSession() as http:
        for case, max_s in (("reconnect_then_max_seconds", 20), ("ws_closed_grace", 600), ("never_connected", 600)):
            sid = "life-" + secrets.token_hex(3)
            room = "face-life-" + secrets.token_hex(3)
            tok = lk_token(E["LIVEKIT_API_KEY"], E["LIVEKIT_API_SECRET"], f"face-{sid}", room, 600, True, False, True)
            async with http.post(BASE + "/face/session", json={"session_id": sid, "face": "wang", "livekit_url": E["LIVEKIT_URL"],
                                                              "livekit_token": tok, "max_seconds": max_s}, headers=hdr) as r:
                ok = (await r.json()).get("ok")
            t0 = time.monotonic()
            exp = int(time.time()) + 300
            sig = hmac.new(E["FACE_SECRET"].encode(), f"{sid}.{exp}".encode(), hashlib.sha256).hexdigest()
            url = f"{BASE}/face/ws?session={sid}&exp={exp}&sig={sig}"
            out = {"post_ok": ok}
            if case != "never_connected":
                ws = await http.ws_connect(url)
                m = await ws.receive_json(timeout=5)
                out["first_event"] = m.get("type") + ":" + str(m.get("state"))
                await ws.close()
                if case == "reconnect_then_max_seconds":
                    await asyncio.sleep(2)
                    ws = await http.ws_connect(url)
                    m = await ws.receive_json(timeout=5)
                    out["reconnect_event"] = m.get("type") + ":" + str(m.get("state"))
                    closed = None
                    try:  # the engine should send session.state_updated closed at max_seconds
                        while True:
                            m = await ws.receive_json(timeout=40)
                            if m.get("type") == "session.state_updated" and m.get("state") == "closed":
                                closed = m.get("reason")
                                break
                    except Exception:
                        pass
                    out["closed_event_reason"] = closed
                    await ws.close()
            out["stopped_after_s"] = await wait_stopped(http, t0, 90)
            res[case] = out
            print(case, json.dumps(out), flush=True)
    return res


if __name__ == "__main__":
    asyncio.run(main())
