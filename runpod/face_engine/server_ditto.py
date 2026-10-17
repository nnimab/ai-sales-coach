# Face engine, Ditto mode: same HTTP/WS contract as server.py (MuseTalk), but every frame comes from one continuously
# running Ditto stream (/workspace/ditto_live/ditto_stream.py). The stream hands back each video frame together with
# the 40 ms of audio that belongs to it, so lips and voice stay in sync; when nobody speaks it keeps producing idle
# frames (blinks, small head motion). run.sh starts this file instead of server.py when /workspace/face_engine/ENGINE
# says "ditto" (MuseTalk stays the default and the fallback).
import os, sys, time, json, asyncio, logging, threading, collections, base64, binascii

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import types  # noqa: E402
_stub = types.ModuleType("renderer")  # server.py imports the MuseTalk renderer; Ditto mode never uses it
_stub.Renderer = _stub.Job = None
sys.modules.setdefault("renderer", _stub)
import server as S  # noqa: E402  (logging, auth, WS handler, constants; its MuseTalk renderer is never started here)
import numpy as np  # noqa: E402
import cv2  # noqa: E402
from aiohttp import web  # noqa: E402
from livekit import rtc  # noqa: E402

DITTO_DIR = os.environ.get("DITTO_LIVE_DIR", "/workspace/ditto_live")
DITTO_SOURCE = os.environ.get("DITTO_SOURCE") or None  # None = ditto_live/assets/wang_idle_f0.png (704x896)
FPS, TICK, SR, SPF = S.FPS, S.TICK, S.SR, S.SPF
SILENCE = S.SILENCE
log = logging.getLogger("face")
jlog = S.jlog
backend = None


class DittoBackend:
    """Stands in for server.Renderer (health / faces / ready) and owns the one Ditto stream.
    A reader thread pulls frames at 25 Hz and hands them to the active session (or drops them when idle)."""

    def __init__(self):
        self.ready = False
        self.error = None
        self.gpu_name = ""
        self.faces = {}
        self.stream = None
        self.W = self.H = 0
        self.sink = None  # callable(item) on the session's loop, or None
        self.lock = threading.Lock()
        self.stats = collections.Counter()

    def start(self):
        threading.Thread(target=self._main, name="ditto-reader", daemon=True).start()

    def _load(self):
        t0 = time.time()
        if DITTO_DIR not in sys.path:
            sys.path.insert(0, DITTO_DIR)
        from ditto_stream import DittoStream
        import torch
        self.gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "GPU"
        self.stream = DittoStream(DITTO_SOURCE)
        self.stream.start()
        rgb, pcm, uid, last = self.stream.get_frame(timeout=60.0)
        self.H, self.W = rgb.shape[:2]
        self.faces = {"wang": {"neutral": None}}
        jlog(logging.INFO, "ditto_ready", s=round(time.time() - t0, 1), size=[self.W, self.H],
             latency_s=round(float(getattr(self.stream, "latency_s", 0) or 0), 3))

    def _main(self):
        try:
            self._load()
            import gc
            gc.collect()
            gc.freeze()  # long-lived model objects out of full GC passes (pauses would stall the 25 Hz reader)
            self.ready = True
        except Exception as e:
            log.exception("ditto load failed")
            self.error = str(e)[:300]
            return
        t_next = time.monotonic()
        while True:
            try:
                item = self.stream.get_frame(timeout=1.0)
            except Exception:
                log.exception("ditto get_frame failed")
                self.stats["get_errors"] += 1
                time.sleep(0.2)
                continue
            if item is None:
                self.stats["empty"] += 1
                continue
            now = time.monotonic()
            if now < t_next:
                time.sleep(t_next - now)
            elif now - t_next > 0.25:
                self.stats["resync"] += 1
                t_next = now
            t_next += TICK
            with self.lock:
                sink = self.sink
            if sink is not None:
                sink(item)

    def attach(self, sink):
        with self.lock:
            self.sink = sink

    def detach(self, sink):
        with self.lock:
            if self.sink is sink:
                self.sink = None


class DittoSession:
    """One LiveKit room + one control WebSocket, LiveAvatar-LITE events (same as server.Session)."""

    def __init__(self, sid, face, lk_url, lk_token, max_seconds):
        self.sid, self.face = sid, face
        self.lk_url, self.lk_token = lk_url, lk_token
        self.max_seconds = max_seconds
        self.loop = asyncio.get_running_loop()
        self.t0 = time.monotonic()
        self.room = self.asrc = self.vsrc = self.vtr = None
        self.tasks = []
        self.ws = None
        self.out_q = asyncio.Queue()
        self.in_q = asyncio.Queue(maxsize=50)
        self.ws_timer = None
        self.closing = False
        self.closed = asyncio.Event()
        self.open_uid = None
        self.open_ms = 0.0
        self.live_uids = collections.OrderedDict()  # uid -> {"t_first", "started"} still to be played
        self.dead_uids = collections.deque(maxlen=256)  # interrupted / finished: late audio and frames are muted
        self.listening = True
        self.talking = False
        self.state = "listening"
        self.present_q = collections.deque()
        self.present_evt = asyncio.Event()
        self.st = collections.Counter()
        self.lat = []
        self._sink = lambda item: self.loop.call_soon_threadsafe(self._put, item)

    # ---------------------------------------------------------------------------------------------- lifecycle
    async def start(self):
        W, H = backend.W, backend.H
        self.room = rtc.Room(loop=self.loop)
        self.room.on("disconnected", lambda *a: self.loop.create_task(self.stop("room_disconnected")))
        await asyncio.wait_for(self.room.connect(self.lk_url, self.lk_token, rtc.RoomOptions(auto_subscribe=False)), 15)
        self.vsrc = rtc.VideoSource(W, H)
        self.asrc = rtc.AudioSource(SR, 1, queue_size_ms=200, loop=self.loop)
        self.vtr = rtc.LocalVideoTrack.create_video_track("face-video", self.vsrc)
        atr = rtc.LocalAudioTrack.create_audio_track("face-audio", self.asrc)
        await self.room.local_participant.publish_track(self.vtr, rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_CAMERA, simulcast=False,
            video_encoding=rtc.VideoEncoding(max_framerate=FPS, max_bitrate=S.FACE_VIDEO_BPS)))
        await self.room.local_participant.publish_track(atr, rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_MICROPHONE, dtx=False))
        backend.stream.interrupt()  # start from silence
        backend.attach(self._sink)
        for c in (self.tick_loop(), self.presenter(), self.ws_writer(), self.watchdog()):
            self.tasks.append(self.loop.create_task(c))
        self.arm_ws_timer()
        jlog(logging.INFO, "session_started", sid=self.sid, face=self.face, engine="ditto", room=self.room.name,
             max_seconds=self.max_seconds, connect_s=round(time.monotonic() - self.t0, 2))

    async def stop(self, reason):
        if self.closing:
            await self.closed.wait()
            return
        self.closing = True
        try:
            backend.detach(self._sink)
            try:
                backend.stream.interrupt()
            except Exception:
                pass
            self.emit({"type": "session.state_updated", "state": "closed", "reason": reason})
            if self.ws is not None and not self.ws.closed:
                try:
                    await asyncio.wait_for(self._flush_out(), 1.0)
                    await self.ws.close(code=1000, message=b"closed")
                except Exception:
                    pass
            for t in self.tasks:
                t.cancel()
            if self.ws_timer:
                self.ws_timer.cancel()
            if self.room is not None:
                try:
                    await asyncio.wait_for(self.room.disconnect(), 5)
                except Exception:
                    pass
            for s in (self.asrc, self.vsrc):
                try:
                    if s is not None:
                        await s.aclose()
                except Exception:
                    pass
        finally:
            self.present_q.clear()
            S.sessions.pop(self.sid, None)
            jlog(logging.INFO, "session_stopped", sid=self.sid, reason=reason, engine="ditto",
                 secs=round(time.monotonic() - self.t0, 1), stats=dict(self.st), speak_latency_ms=self.lat[-20:])
            self.closed.set()

    async def _flush_out(self):
        while not self.out_q.empty():
            await asyncio.sleep(0.01)

    async def watchdog(self):
        await asyncio.sleep(self.max_seconds)
        self.loop.create_task(self.stop("max_seconds"))

    def arm_ws_timer(self):
        if self.ws_timer:
            self.ws_timer.cancel()
        self.ws_timer = self.loop.call_later(S.WS_GRACE_S, lambda: self.loop.create_task(self.stop("ws_gone")))

    # ---------------------------------------------------------------------------------------------- websocket
    async def attach(self, ws):
        old = self.ws
        self.ws = ws
        if self.ws_timer:
            self.ws_timer.cancel()
            self.ws_timer = None
        if old is not None and not old.closed:
            self.loop.create_task(old.close(code=4000, message=b"replaced"))
        self.emit({"type": "session.state_updated", "state": "connected"})
        self.emit({"type": "agent.state_updated", "previous_state": "idle", "new_state": self.state})

    def detach(self, ws):
        if self.ws is ws:
            self.ws = None
            if not self.closing:
                self.arm_ws_timer()

    def emit(self, obj, source=None):
        m = {"type": obj.pop("type"), "event_id": S.new_id(), "source_event_id": source}
        m.update(obj)
        self.out_q.put_nowait(m)

    async def ws_writer(self):
        while True:
            m = await self.out_q.get()
            ws = self.ws
            if ws is None or ws.closed:
                continue
            try:
                await ws.send_str(json.dumps(m, ensure_ascii=False))
            except Exception:
                pass

    def warn(self, typ, msg, source=None):
        self.emit({"type": "warning", "warning": {"type": typ, "message": msg}}, source)

    def error(self, typ, msg, source=None):
        self.emit({"type": "error", "error": {"type": typ, "message": msg}}, source)

    def set_state(self):
        new = "talking" if self.talking else ("listening" if self.listening else "idle")
        if new != self.state:
            prev, self.state = self.state, new
            self.emit({"type": "agent.state_updated", "previous_state": prev, "new_state": new})

    def handle(self, m):
        t = m.get("type")
        eid = m.get("event_id")
        if eid is not None and not isinstance(eid, str):
            eid = str(eid)
        if t == "agent.speak":
            self.on_speak(m, eid)
        elif t == "agent.speak_end":
            uid = self.open_uid
            if uid is None:
                return
            self.open_uid = None
            backend.stream.end_utterance(uid)
            self.emit({"type": "agent.audio_buffer_committed", "task": {"id": uid}, "duration_ms": round(self.open_ms)}, uid)
            if self.open_ms <= 0:
                self.live_uids.pop(uid, None)
                self.emit({"type": "agent.speak_ended", "task": {"id": uid}}, uid)
        elif t == "agent.interrupt":
            self.interrupt(eid)
        elif t in ("agent.start_listening", "agent.stop_listening"):
            self.listening = t == "agent.start_listening"
            self.set_state()
        elif t == "session.keep_alive":
            self.st["keep_alive"] += 1
        elif t == "agent.set_expression":
            pass  # Ditto mode has one look; the expression follows the voice
        elif t is None:
            self.warn("invalid_event", "訊息缺少 type", eid)
        else:
            self.warn("unsupported_event", f"不支援的指令：{str(t)[:40]}", eid)

    def on_speak(self, m, eid):
        a = m.get("audio")
        if not isinstance(a, str) or not a:
            self.error("invalid_audio", "agent.speak 缺少 audio（base64 PCM16 24kHz）", eid)
            return
        try:
            pcm = base64.b64decode("".join(a.split()), validate=True)
        except (binascii.Error, ValueError):
            self.error("invalid_audio", "audio 不是正確的 base64", eid)
            return
        if len(pcm) % 2:
            pcm = pcm[:-1]
        if not pcm:
            return
        uid = self.open_uid
        if uid is None:
            uid = eid or S.new_id()
            if uid in self.dead_uids:
                self.st["stale_audio"] += 1
                self.warn("stale_audio", "這句已經結束或被打斷，多出來的聲音不播", eid)
                return
            self.open_uid, self.open_ms = uid, 0.0
            self.live_uids[uid] = {"t_first": time.monotonic(), "started": False}
        if self.open_ms > S.MAX_UTT_SEC * 1000:
            self.warn("utterance_too_long", "一句話太長，後面的聲音不播", eid)
            return
        self.open_ms += len(pcm) / 48.0
        backend.stream.push_audio(pcm, uid)
        self.emit({"type": "agent.audio_buffer_appended", "task": {"id": uid}, "duration_ms": round(len(pcm) / 48)}, eid)

    def interrupt(self, eid):
        try:
            backend.stream.interrupt()
        except Exception:
            log.exception("ditto interrupt failed")
        cleared = list(self.live_uids)
        self.live_uids.clear()
        self.open_uid = None
        self.dead_uids.extend(cleared)
        if self.asrc is not None:
            self.asrc.clear_queue()
        dropped = len(self.present_q)
        self.present_q.clear()
        self.present_evt.set()
        for uid in cleared:
            self.emit({"type": "agent.speak_interrupted", "task": {"id": uid}}, uid)
        if cleared:
            self.emit({"type": "agent.audio_buffer_cleared"}, eid)
        self.talking = False
        self.set_state()
        self.st["interrupts"] += 1
        jlog(logging.INFO, "interrupt", sid=self.sid, utts=len(cleared), dropped_frames=dropped)

    # ---------------------------------------------------------------------------------------------- playout
    def _put(self, item):
        if self.closing:
            return
        try:
            self.in_q.put_nowait(item)
        except asyncio.QueueFull:
            self.st["in_q_drop"] += 1

    async def tick_loop(self):
        try:
            while True:
                rgb, pcm, uid, last = await self.in_q.get()
                ev = []
                if uid is not None and uid in self.dead_uids:
                    pcm, uid, last = SILENCE, None, False  # interrupted speech still in the pipeline: mute it
                    self.st["muted_frames"] += 1
                if uid is not None:
                    info = self.live_uids.get(uid)
                    if info is not None and not info["started"]:
                        info["started"] = True
                        ev.append(("started", uid, info["t_first"]))
                    if last:
                        self.live_uids.pop(uid, None)
                        self.dead_uids.append(uid)
                        ev.append(("ended", uid, None))
                if len(pcm) != S.BPF:
                    pcm = (bytes(pcm) + SILENCE)[:S.BPF]
                await self.asrc.capture_frame(rtc.AudioFrame(pcm, SR, 1, SPF))
                q = self.asrc.queued_duration
                at = time.monotonic() + max(0.0, q - TICK + S.AV_OFFSET)
                rgba = cv2.cvtColor(rgb, cv2.COLOR_RGB2RGBA)
                self.present_q.append((at, rgba, ev))
                self.present_evt.set()
                self.st["ticks"] += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("ditto tick loop crashed")
            self.loop.create_task(self.stop("internal_error"))

    async def presenter(self):
        W, H = backend.W, backend.H
        try:
            while True:
                if not self.present_q:
                    self.present_evt.clear()
                    await self.present_evt.wait()
                    continue
                at, fr, ev = self.present_q[0]
                d = at - time.monotonic()
                if d > 0.002:
                    await asyncio.sleep(d)
                    continue
                self.present_q.popleft()
                self.vsrc.capture_frame(rtc.VideoFrame(W, H, rtc.VideoBufferType.RGBA, memoryview(fr.reshape(-1))))
                self.st["frames_out"] += 1
                for kind, uid, t_first in ev:
                    if kind == "started":
                        lat = (time.monotonic() - t_first) * 1000
                        self.lat.append(round(lat))
                        self.talking = True
                        self.emit({"type": "agent.speak_started", "task": {"id": uid}}, uid)
                        jlog(logging.INFO, "speak_started", sid=self.sid, uid=uid[:12], first_chunk_to_started_ms=round(lat))
                    elif kind == "ended":
                        self.emit({"type": "agent.speak_ended", "task": {"id": uid}}, uid)
                        jlog(logging.INFO, "speak_ended", sid=self.sid, uid=uid[:12])
                        self.talking = any(v["started"] for v in self.live_uids.values())
                self.set_state()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("ditto presenter crashed")
            self.loop.create_task(self.stop("internal_error"))


# ------------------------------------------------------------------------------------------------------ http
async def h_health(req):
    faces = sorted(backend.faces) if backend.ready else []
    return web.json_response({
        "ok": True, "gpu": backend.gpu_name or None, "faces": faces, "engine": "ditto",
        "expressions": {f: sorted(backend.faces[f]) for f in faces},
        "sessions": len(S.sessions), "ready": bool(backend.ready),
        **({"error": "引擎啟動失敗"} if backend.error else {})})


async def h_session(req):
    if not S.secret_ok(req):
        return S.err(401, "未授權")
    try:
        b = await req.json()
        assert isinstance(b, dict)
    except Exception:
        return S.err(400, "格式錯誤")
    sid, face = b.get("session_id"), b.get("face", "wang")
    url, tok = b.get("livekit_url"), b.get("livekit_token")
    try:
        max_s = int(b.get("max_seconds") or 900)
    except (TypeError, ValueError):
        return S.err(400, "max_seconds 格式錯誤")
    if not isinstance(sid, str) or not S.SID_RE.match(sid):
        return S.err(400, "session_id 格式錯誤")
    if not isinstance(url, str) or not url.startswith(("wss://", "ws://")) or not isinstance(tok, str) or not tok:
        return S.err(400, "缺少 LiveKit 連線資料")
    if not backend.ready:
        return S.err(503, "真人引擎還在啟動中，請稍後再試")
    if face not in backend.faces:
        return S.err(400, "沒有這個頭像")
    max_s = max(10, min(max_s, 4 * 3600))
    async with S.session_lock:
        if sid in S.sessions:
            return web.json_response({"ok": True, "ws_path": "/face/ws"})
        if S.sessions:
            return S.err(409, "真人畫面已經有一通在使用中，請稍後再試")
        s = DittoSession(sid, face, url, tok, max_s)
        S.sessions[sid] = s
        try:
            await s.start()
        except Exception as e:
            jlog(logging.WARNING, "session_start_failed", sid=sid, err=type(e).__name__, detail=str(e)[:200])
            await s.stop("start_failed")
            return S.err(502, "連不上 LiveKit 房間，請稍後再試")
    return web.json_response({"ok": True, "ws_path": "/face/ws"})


def main():
    global backend
    S.setup_logging()
    sys.setswitchinterval(0.002)
    S._gc_watch()
    if not S.FACE_SECRET:
        jlog(logging.ERROR, "no_face_secret", msg="FACE_SECRET is not set: /face/session and /face/ws will refuse all requests")
    backend = DittoBackend()
    backend.start()
    app = web.Application(middlewares=[S.guard], client_max_size=1024 * 1024)
    app.add_routes([web.get("/face/health", h_health), web.post("/face/session", h_session),
                    web.post("/face/stop", S.h_stop), web.get("/face/ws", S.h_ws)])
    app.on_shutdown.append(S.on_shutdown)
    port = int(os.environ.get("FACE_PORT", "8010"))
    jlog(logging.INFO, "listening", port=port, engine="ditto")
    web.run_app(app, host="0.0.0.0", port=port, access_log=None, handle_signals=True, print=None)


if __name__ == "__main__":
    main()
