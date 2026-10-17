# Pod-side end-to-end test of the face engine. LiveKit tokens are minted HERE from the pod env (secrets never
# leave the pod and are never printed). Starts a pod-side subscriber recorder (lk_record.py), opens a session,
# drives the LITE WebSocket with a wav (0.2 s chunks, 2x real time), runs interrupt / expression / robustness
# checks, stops the session and computes latency, fps and A/V offset from the engine's publish timeline
# (FACE_DEBUG_TIMELINE=1) and the recorder's receive timeline (same monotonic clock).
# Usage: python e2e_test.py <wav> [--viewer-token-out PATH] [--wait-viewer 90] [--speed 2] [--chunk 0.2]
import asyncio, json, os, sys, time, hmac, hashlib, base64, secrets, subprocess, argparse, wave, uuid
import numpy as np
import aiohttp

HERE = os.path.dirname(os.path.abspath(__file__))
ENG = os.path.dirname(HERE)
ap = argparse.ArgumentParser()
ap.add_argument("wav")
ap.add_argument("--viewer-token-out", default=None)
ap.add_argument("--wait-viewer", type=float, default=90)
ap.add_argument("--speed", type=float, default=2.0)
ap.add_argument("--chunk", type=float, default=0.2)
ap.add_argument("--base", default="http://127.0.0.1:8010")
ap.add_argument("--short", action="store_true", help="only one utterance + interrupt (smoke test)")
ap.add_argument("--no-pod-rec", action="store_true", help="no pod-side subscriber (keeps the pod CPU for the engine)")
args = ap.parse_args()


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


def write_private(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)


def read_wav24(path):
    with wave.open(path) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2, "need mono 16-bit wav"
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    if sr != 24000:
        from scipy.signal import resample_poly
        g = np.gcd(sr, 24000)
        x = np.clip(resample_poly(x.astype(np.float32), 24000 // g, sr // g), -32768, 32767).astype(np.int16)
    return x.tobytes()


E = penv()
for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "FACE_SECRET"):
    assert E.get(k), f"missing env {k}"
SID = "e2e-" + secrets.token_hex(4)
ROOM = "face-test-" + secrets.token_hex(4)
TMP = os.path.join(ENG, "tmp")
OUT = os.path.join(ENG, "logs", f"e2e_{SID}")
PCM = read_wav24(args.wav)
ev_log = []  # (t, msg)
waiters = []


def now():
    return time.monotonic()


async def main():
    lk_url = E["LIVEKIT_URL"]
    pub_tok = lk_token(E["LIVEKIT_API_KEY"], E["LIVEKIT_API_SECRET"], f"face-{SID}", ROOM, 3600, True, False, True)
    rec_tok = lk_token(E["LIVEKIT_API_KEY"], E["LIVEKIT_API_SECRET"], "probe-pod", ROOM, 900, False, True, False)
    write_private(os.path.join(TMP, f"rec_{SID}.json"), {"url": lk_url, "token": rec_tok})
    if args.viewer_token_out:
        vt = lk_token(E["LIVEKIT_API_KEY"], E["LIVEKIT_API_SECRET"], "viewer-win", ROOM, 600, False, True, False)
        write_private(args.viewer_token_out, {"url": lk_url, "token": vt})
    print(json.dumps({"room": ROOM, "sid": SID}), flush=True)
    if args.no_pod_rec:
        os.remove(os.path.join(TMP, f"rec_{SID}.json"))
    rec = None if args.no_pod_rec else await asyncio.create_subprocess_exec(
        sys.executable, os.path.join(HERE, "lk_record.py"), os.path.join(TMP, f"rec_{SID}.json"), OUT + "_pod", "400",
        "--rm-token", "--mouth", "362,448,468,516", stdout=asyncio.subprocess.PIPE)
    rec_ev = []
    viewer = asyncio.Event()
    first_video = asyncio.Event()

    async def rec_reader():
        while rec is not None:
            line = await rec.stdout.readline()
            if not line:
                return
            try:
                m = json.loads(line)
            except Exception:
                continue
            rec_ev.append((now(), m))
            if m.get("ev") == "participant" and m.get("identity") == "viewer-win":
                viewer.set()
            if m.get("ev") == "first_video":
                first_video.set()
    rr = asyncio.ensure_future(rec_reader())
    await asyncio.sleep(2.0)
    hdr = {"X-Face-Secret": E["FACE_SECRET"]}
    checks = {}
    async with aiohttp.ClientSession() as http:
        t = now()
        async with http.post(args.base + "/face/session", json={
                "session_id": SID, "face": "wang", "expression": "neutral", "livekit_url": lk_url,
                "livekit_token": pub_tok, "max_seconds": 600}, headers=hdr) as r:
            res = await r.json()
        checks["session_post_s"] = round(now() - t, 2)
        print(json.dumps({"session": res, "post_s": checks["session_post_s"]}), flush=True)
        assert res.get("ok"), res
        # robustness: second session rejected, bad secret, bad ws signature
        async with http.post(args.base + "/face/session", json={
                "session_id": SID + "x", "face": "wang", "livekit_url": lk_url, "livekit_token": "x"}, headers=hdr) as r:
            checks["second_session"] = [r.status, (await r.json()).get("error")]
        async with http.post(args.base + "/face/session", json={}, headers={"X-Face-Secret": "nope"}) as r:
            checks["bad_secret"] = r.status
        exp = int(time.time()) + 300
        try:
            async with http.ws_connect(f"{args.base}/face/ws?session={SID}&exp={exp}&sig={'0' * 64}") as w:
                checks["bad_sig"] = "accepted?!"
        except aiohttp.WSServerHandshakeError as e:
            checks["bad_sig"] = e.status
        try:
            await asyncio.wait_for(first_video.wait(), 20 if rec is not None else 0.01)
            checks["first_video_after_post_s"] = round(rec_ev[[m.get("ev") for _, m in rec_ev].index("first_video")][0] - t, 2)
        except asyncio.TimeoutError:
            checks["first_video_after_post_s"] = None
        if args.viewer_token_out:
            print(json.dumps({"waiting_for_viewer": args.wait_viewer}), flush=True)
            try:
                if rec is None:
                    await asyncio.sleep(args.wait_viewer)
                else:
                    await asyncio.wait_for(viewer.wait(), args.wait_viewer)
                checks["viewer_joined"] = True
                await asyncio.sleep(4.0)  # let the viewer subscribe
            except asyncio.TimeoutError:
                checks["viewer_joined"] = False
        sig = hmac.new(E["FACE_SECRET"].encode(), f"{SID}.{exp}".encode(), hashlib.sha256).hexdigest()
        ws = await http.ws_connect(f"{args.base}/face/ws?session={SID}&exp={exp}&sig={sig}")
        got_connected = asyncio.Event()

        async def ws_reader():
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    m = json.loads(msg.data)
                    ev_log.append((now(), m))
                    if m.get("type") == "session.state_updated" and m.get("state") == "connected":
                        got_connected.set()
                    for wt in list(waiters):
                        if wt[0](m):
                            wt[1].set()
        wr = asyncio.ensure_future(ws_reader())
        await asyncio.wait_for(got_connected.wait(), 5)

        def wait_for(pred, timeout):
            e = asyncio.Event()
            w = (pred, e)
            waiters.append(w)

            async def _w():
                try:
                    await asyncio.wait_for(e.wait(), timeout)
                    return True
                except asyncio.TimeoutError:
                    return False
                finally:
                    waiters.remove(w)
            return asyncio.ensure_future(_w())

        async def send(obj):
            await ws.send_str(json.dumps(obj))

        marks = {}

        async def speak(uid, pcm, end=True):
            step = int(args.chunk * 24000) * 2
            marks[uid] = {"first_chunk": None}
            for i in range(0, len(pcm), step):
                if marks[uid]["first_chunk"] is None:
                    marks[uid]["first_chunk"] = now()
                await send({"type": "agent.speak", "event_id": uid, "audio": base64.b64encode(pcm[i:i + step]).decode()})
                await asyncio.sleep(args.chunk / args.speed)
            if end:
                await send({"type": "agent.speak_end", "event_id": uid})
            marks[uid]["last_chunk"] = now()

        def is_ev(typ, uid):
            return lambda m: m.get("type") == typ and m.get("source_event_id") == uid

        dur = len(PCM) / 48000
        await send({"type": "agent.start_listening", "event_id": str(uuid.uuid4())})
        await asyncio.sleep(2.0)
        # U1: whole sample, normal end
        u1 = "u1-" + secrets.token_hex(3)
        st1 = wait_for(is_ev("agent.speak_started", u1), 10)
        en1 = wait_for(is_ev("agent.speak_ended", u1), dur + 15)
        await speak(u1, PCM)
        await st1
        await en1
        await asyncio.sleep(1.5)
        # U2: interrupt 3 s after it starts (sender keeps streaming until then, like the browser)
        u2 = "u2-" + secrets.token_hex(3)
        st2 = wait_for(is_ev("agent.speak_started", u2), 10)
        it2 = wait_for(is_ev("agent.speak_interrupted", u2), dur + 15)
        sp = asyncio.ensure_future(speak(u2, PCM))
        await st2
        t_st2 = now()
        # interrupt in the middle of continuous speech: first point >= 2.5 s in with 400 ms of loud source audio
        x = np.frombuffer(PCM, np.int16).astype(np.float32) / 32768
        r10 = np.sqrt((x[: len(x) // 240 * 240].reshape(-1, 240) ** 2).mean(1))
        pos = next((i for i in range(250, len(r10) - 40) if (r10[i:i + 40] > 0.03).all()), 300)
        marks[u2]["interrupt_pos_s"] = pos / 100
        await asyncio.sleep(max(0.0, t_st2 + pos / 100 + 0.05 - now()))
        sp.cancel()
        marks[u2]["interrupt"] = now()
        await send({"type": "agent.interrupt", "event_id": str(uuid.uuid4())})
        await it2
        # stale chunk for the interrupted utterance must be ignored
        await send({"type": "agent.speak", "event_id": u2, "audio": base64.b64encode(PCM[:9600]).decode()})
        await send({"type": "agent.start_listening", "event_id": str(uuid.uuid4())})
        await asyncio.sleep(2.0)
        uids = [u1, u2]
        if not args.short:
            for expr in ("worried", "happy"):
                await send({"type": "agent.set_expression", "expression": expr})
                await asyncio.sleep(2.5)
                u = f"u-{expr}-" + secrets.token_hex(2)
                part = PCM[:48000 * 4]
                st = wait_for(is_ev("agent.speak_started", u), 10)
                en = wait_for(is_ev("agent.speak_ended", u), 20)
                await speak(u, part)
                await st
                await en
                await asyncio.sleep(1.5)
                uids.append(u)
            await send({"type": "agent.set_expression", "expression": "neutral"})
            await asyncio.sleep(2.5)
        # robustness: bad messages must produce error/warning events, never kill the session
        await ws.send_str("not json")
        await send({"type": "agent.speak", "event_id": "bad", "audio": "@@@"})
        await send({"type": "agent.foo"})
        await send({"type": "agent.set_expression", "expression": "sleepy"})
        await send({"type": "session.keep_alive", "event_id": "ka"})
        await asyncio.sleep(1.0)
        async with http.get(args.base + "/face/health") as r:
            checks["health_during"] = await r.json()
        async with http.post(args.base + "/face/stop", json={"session_id": SID}, headers=hdr) as r:
            checks["stop"] = await r.json()
        async with http.post(args.base + "/face/stop", json={"session_id": SID}, headers=hdr) as r:
            checks["stop_again"] = await r.json()
        await asyncio.sleep(0.5)
        wr.cancel()
        await ws.close()
    try:
        if rec is not None:
            await asyncio.wait_for(rec.wait(), 30)
    except asyncio.TimeoutError:
        rec.kill()
    rr.cancel()
    return marks, uids, checks


def zoh(ts, vals, grid):
    ts = np.asarray(ts)
    idx = np.searchsorted(ts, grid, side="right") - 1
    out = np.asarray(vals, np.float64)[np.clip(idx, 0, len(vals) - 1)]
    out[idx < 0] = 0
    return out


def xcorr_lag(a, b, maxlag):
    """lag (bins) at which b best matches a delayed: b[t] ~ a[t-lag], lag in [0, maxlag]."""
    a = a - a.mean()
    b = b - b.mean()
    best, bl = -1e18, 0
    for L in range(0, maxlag + 1):
        c = float(np.dot(a[:len(a) - L], b[L:]))
        if c > best:
            best, bl = c, L
    return bl, best / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12)


def analyse(marks, uids, checks):
    rep = {"sid": SID, "room": ROOM, "checks": checks}
    ev = [(t, m) for t, m in ev_log]
    per = {}
    for u in uids:
        mk = marks.get(u, {})
        s = next((t for t, m in ev if m.get("type") == "agent.speak_started" and m.get("source_event_id") == u), None)
        e = next((t for t, m in ev if m.get("type") in ("agent.speak_ended", "agent.speak_interrupted")
                  and m.get("source_event_id") == u), None)
        per[u] = {"first_chunk_to_speak_started_ms": round((s - mk["first_chunk"]) * 1000) if s and mk.get("first_chunk") else None,
                  "end_event": next((m["type"] for t, m in ev if m.get("source_event_id") == u and m.get("type") in
                                     ("agent.speak_ended", "agent.speak_interrupted")), None)}
        if "interrupt" in mk and e:
            per[u]["interrupt_to_event_ms"] = round((e - mk["interrupt"]) * 1000)
    rep["utterances"] = per
    rep["events_count"] = {}
    for _, m in ev:
        rep["events_count"][m["type"]] = rep["events_count"].get(m["type"], 0) + 1
    rep["errors_warnings"] = [m.get("error") or m.get("warning") for _, m in ev if m["type"] in ("error", "warning")]
    rep["states"] = [m.get("new_state") for _, m in ev if m["type"] == "agent.state_updated"]
    # engine publish timeline vs pod recorder receive timeline (same CLOCK_MONOTONIC)
    tlp = os.path.join(ENG, "logs", f"timeline_{SID}.json")
    rcp = OUT + "_pod.timeline.json"
    for _ in range(50):
        if os.path.exists(tlp) and (os.path.exists(rcp) or args.no_pod_rec):
            break
        time.sleep(0.2)
    tl = json.load(open(tlp))
    rows = tl["rows"]
    tp = np.array([r[0] for r in rows])
    kind = [r[1] for r in rows]
    rms = np.array([r[4] for r in rows])
    mouth = np.array([r[6] for r in rows])
    d = np.diff(tp)
    rep["engine"] = {"frames": len(rows), "fps": round((len(tp) - 1) / (tp[-1] - tp[0]), 2),
                     "gaps_gt_60ms": int((d > 0.06).sum()), "max_gap_ms": round(float(d.max()) * 1000, 1),
                     "stall_ticks": kind.count("s"), "late_video_frames": kind.count("d"), "speech_frames": kind.count("u")}
    if args.no_pod_rec:
        lateness = (tp - np.array([r[5] for r in rows])) * 1000
        rep["engine"]["present_late_ms_p99_max"] = [round(float(np.percentile(lateness, 99)), 1), round(float(lateness.max()), 1)]
        for u in uids:
            mk = marks.get(u, {})
            if "interrupt" in mk:
                ti = mk["interrupt"]
                pub_last = [r[0] for r in rows if r[1] in ("u", "d") and r[2] == u[:8] and r[0] > ti - 1]
                per[u]["interrupt_to_last_published_speech_frame_ms"] = round((max(pub_last) - ti) * 1000) if pub_last else None
        return rep
    rc = json.load(open(rcp))
    vt = np.array(rc["video_t"])
    vm = np.array(rc["video_mouth"])
    dv = np.diff(vt)
    rep["subscriber"] = {"frames": len(vt), "fps": round((len(vt) - 1) / (vt[-1] - vt[0]), 2),
                         "gaps_gt_60ms": int((dv > 0.06).sum()), "gaps_gt_100ms": int((dv > 0.1).sum()),
                         "max_gap_ms": round(float(dv.max()) * 1000, 1)}
    # 10 ms grid over the common span
    g0 = max(tp[0], vt[0], rc["audio_t0"]) + 0.5
    g1 = min(tp[-1], vt[-1], rc["audio_t0"] + rc["audio_samples"] / 24000) - 0.5
    grid = np.arange(g0, g1, 0.01)
    # engine audio envelope: each tick's 40 ms of audio starts at its present time
    ea = zoh(tp, rms, grid)
    ra_t = rc["audio_t0"] + np.arange(len(rc["audio_rms10"])) * 0.01
    ra = zoh(ra_t, rc["audio_rms10"], grid)
    ev_m = zoh(tp, mouth, grid)
    rv_m = zoh(vt, vm, grid)
    la, ca = xcorr_lag(ea, ra, 150)
    lv, cv = xcorr_lag(ev_m, rv_m, 150)
    rep["transport"] = {"audio_latency_ms": la * 10, "audio_corr": round(ca, 3), "video_latency_ms": lv * 10,
                        "video_corr": round(cv, 3), "av_offset_ms_video_minus_audio": (lv - la) * 10}
    # first chunk -> audible at subscriber; interrupt -> silence at subscriber
    thr = 0.01
    for u in uids:
        mk = marks.get(u, {})
        fc = mk.get("first_chunk")
        if fc:
            k = np.where((ra_t > fc) & (np.array(rc["audio_rms10"]) > thr))[0]
            per[u]["first_chunk_to_audible_at_subscriber_ms"] = round((ra_t[k[0]] - fc) * 1000) if len(k) else None
            s_ev = per[u]["first_chunk_to_speak_started_ms"]
            per[u]["first_chunk_to_first_frame_at_subscriber_ms"] = (s_ev + lv * 10) if s_ev is not None else None
        if "interrupt" in mk:
            ti = mk["interrupt"]
            win = (ra_t > ti - 0.5) & (ra_t < ti + 3)
            loud = np.where(win & (np.array(rc["audio_rms10"]) > thr))[0]
            per[u]["interrupt_to_silence_at_subscriber_ms"] = round((ra_t[loud[-1]] + 0.01 - ti) * 1000) if len(loud) else None
            pub_last = [r[0] for r in rows if r[1] in ("u", "d") and r[2] == u[:8] and r[0] > ti - 1]
            per[u]["interrupt_to_last_published_speech_frame_ms"] = round((max(pub_last) - ti) * 1000) if pub_last else None
            idle_after = [r[0] for r in rows if r[1] == "i" and r[0] > ti]
            if idle_after:  # first idle (cross-fade) frame published after the interrupt; +4 more frames = pure base
                per[u]["interrupt_to_first_idle_frame_published_ms"] = round((idle_after[0] - ti) * 1000)
                per[u]["interrupt_to_idle_video_at_subscriber_ms"] = round((idle_after[0] - ti) * 1000 + lv * 10)
                per[u]["crossfade_ms"] = 200
    try:
        for line in open(os.path.join(ENG, "logs", "engine.log"), encoding="utf-8"):
            if '"session_stopped"' in line and SID in line:
                rep["engine_session_log"] = json.loads(line)["msg"]
    except Exception:
        pass
    return rep


marks, uids, checks = asyncio.run(main())
try:
    rep = analyse(marks, uids, checks)
except Exception as e:  # keep the raw data even if analysis fails
    import traceback
    traceback.print_exc()
    rep = {"analysis_error": str(e), "checks": checks}
json.dump({"report": rep, "events": [(round(t, 4), m) for t, m in ev_log]}, open(OUT + ".json", "w"), indent=1, default=str)
print(json.dumps(rep, indent=1, default=str), flush=True)
