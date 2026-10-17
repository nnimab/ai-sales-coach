# Face engine: real-time talking face (MuseTalk 1.5 + v11 compositing) published to LiveKit,
# driven over a HeyGen-LiveAvatar-LITE-style WebSocket. One asyncio process, one GPU worker thread.
# Contract: POST /face/session, POST /face/stop, GET /face/health, WS /face/ws (see README.md).
import os, sys

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "4")
os.environ.setdefault("TORCHINDUCTOR_CACHE_DIR", "/workspace/face_engine/cache/inductor")
os.environ.setdefault("TRITON_CACHE_DIR", "/workspace/face_engine/cache/triton")
os.environ.setdefault("TORCHINDUCTOR_COMPILE_THREADS", "4")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import time, json, hmac, hashlib, base64, uuid, asyncio, logging, logging.handlers, re, collections, binascii  # noqa: E402
import numpy as np  # noqa: E402
import cv2  # noqa: E402
from aiohttp import web, WSMsgType  # noqa: E402
from livekit import rtc  # noqa: E402
from google.protobuf.json_format import MessageToDict  # noqa: E402

cv2.setNumThreads(2)
from renderer import Renderer, Job  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.environ.get("FACE_LOG_DIR", os.path.join(ROOT, "logs"))
os.makedirs(LOG_DIR, exist_ok=True)
log = logging.getLogger("face")


def setup_logging():
    fmt = logging.Formatter('{"t":"%(asctime)s","lvl":"%(levelname)s","src":"%(name)s","msg":%(message)s}')

    class Quote(logging.Filter):  # keep every line valid JSON even for plain-text messages / tracebacks
        def filter(self, r):
            if getattr(r, "_q", False):
                return True
            r._q = True
            m = r.getMessage()
            if not (m.startswith("{") and m.endswith("}")):
                if r.exc_info:
                    m = m + " | " + logging.Formatter().formatException(r.exc_info)
                    r.exc_info = None
                    r.exc_text = None
                r.msg, r.args = json.dumps(m), ()
            return True
    h = logging.handlers.RotatingFileHandler(os.path.join(LOG_DIR, "engine.log"), maxBytes=20_000_000, backupCount=5)
    h.setFormatter(fmt)
    h.addFilter(Quote())
    s = logging.StreamHandler(sys.stdout)
    s.setFormatter(fmt)
    s.addFilter(Quote())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(h)
    root.addHandler(s)
    logging.getLogger("livekit").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def jlog(level, ev, **kw):
    log.log(level, json.dumps({"ev": ev, **kw}, ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------------------------------- constants
FPS = 25
TICK = 1.0 / FPS
SR = 24000
SPF = SR // FPS  # 960 samples per video frame
BPF = SPF * 2
SILENCE = bytes(BPF)
LA_MS = float(os.environ.get("FACE_LOOKAHEAD_MS", "200"))  # audio right-context before a frame may be rendered
LEFT_MS = 4000  # whisper left context
RIGHT_MS = 1000  # extra right context used when already available
PAD_MS = 400  # silence assumed before an utterance / after its end (feature timeline)
MAX_AHEAD = 16  # rendered-but-not-played frames (memory / interrupt waste bound)
START_FRAMES = 4  # rendered frames needed before an utterance starts playing (= the first batch)
LEAD_FRAMES = 5  # initial guess of ticks between first job and playback start (adapted per session)
CATCHUP = 2  # when rendering falls behind playout, skip this many frames ahead (audio never waits for video)
RAMP_HALF = int(os.environ.get("FACE_XFADE_HALF", "3"))  # idle-bypass cross-fade = 2*RAMP_HALF+1 frames (280 ms)
PRE_ROLL = int(os.environ.get("FACE_XFADE_PREROLL", "2"))  # cross-fade into speech starts early (needs lookahead >= (RAMP_HALF+PRE_ROLL)*40 ms) ...
HOLD = int(os.environ.get("FACE_XFADE_HOLD", "10"))  # ... and generated lips are held 400 ms after the voice stops (fewer switches in pauses)
AUDIO_Q_TARGET = float(os.environ.get("FACE_AUDIO_QUEUE_MS", "100")) / 1000.0  # audio kept queued while idle (jitter margin)
XF_INTERRUPT = 5
XF_EXPR = 8
MAX_UTT_SEC = 180
WS_GRACE_S = 60
EXPRESSIONS = ("neutral", "worried", "annoyed", "happy")
SID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
DEBUG_TL = os.environ.get("FACE_DEBUG_TIMELINE", "0") == "1"  # per-frame publish timeline -> logs/timeline_<sid>.json
DEBUG_DUMP = int(os.environ.get("FACE_DEBUG_DUMP", "0"))  # save every Nth published speech frame (PNG) -> logs/dump_<sid>/
AV_OFFSET = float(os.environ.get("FACE_AV_OFFSET_MS", "40")) / 1000.0  # >0: show video later than its audio (receivers buffer audio more)

FACE_VIDEO_BPS = int(os.environ.get("FACE_VIDEO_BPS", "3500000"))
FACE_SECRET = os.environ.get("FACE_SECRET", "")
renderer = None
sessions = {}
session_lock = asyncio.Lock()


def new_id():
    return str(uuid.uuid4())


def err(status, msg):
    return web.json_response({"ok": False, "error": msg}, status=status)


def secret_ok(req):
    if not FACE_SECRET:
        return False
    got = req.headers.get("X-Face-Secret", "")
    return hmac.compare_digest(got.encode(), FACE_SECRET.encode())


def ws_sig_ok(sid, exp, sig):
    if not FACE_SECRET or not sid or not exp or not sig:
        return False
    try:
        e = int(exp)
    except ValueError:
        return False
    if e < time.time():
        return False
    want = hmac.new(FACE_SECRET.encode(), f"{sid}.{exp}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, sig.strip().lower())


# ---------------------------------------------------------------------------------------------------- utterance
class Utt:
    def __init__(self, uid, epoch):
        self.uid = uid
        self.epoch = epoch
        self.pcm = bytearray()
        self.rms = []  # per full 40 ms frame
        self.ended = False
        self.n_total = None
        self.frames = {}
        self.next_render = 0
        self.next_play = 0
        self.started = False
        self.base_pos0 = None
        self.clip = None
        self.ema = None  # owned by the GPU worker
        self.t_first = time.monotonic()
        self.t_render0 = None
        self.t_started = None
        self.stalls = 0
        self.late = 0
        self.chunks = 0
        self.tick_submit = None

    @property
    def n_samples(self):
        return len(self.pcm) // 2

    def dur_ms(self):
        return self.n_samples * 1000.0 / SR

    def append(self, pcm):
        self.pcm += pcm
        self.chunks += 1
        nf = self.n_samples // SPF
        if nf > len(self.rms):
            x = np.frombuffer(self.pcm, np.int16, count=(nf - len(self.rms)) * SPF, offset=len(self.rms) * BPF).astype(np.float32) / 32768.0
            x = x.reshape(-1, SPF)
            self.rms.extend(np.sqrt((x * x).mean(1) + 1e-12).tolist())

    def end(self):
        self.ended = True
        self.n_total = (self.n_samples + SPF - 1) // SPF
        if self.n_total > len(self.rms):  # partial last frame
            x = np.frombuffer(self.pcm, np.int16, offset=len(self.rms) * BPF).astype(np.float32) / 32768.0
            self.rms.append(float(np.sqrt((x * x).mean() + 1e-12)) if len(x) else 0.0)

    def n_avail(self):
        if self.ended:
            return self.n_total
        return max(0, int((self.dur_ms() - LA_MS) // 40))

    def audio(self, j):
        b = bytes(self.pcm[j * BPF:(j + 1) * BPF])
        return b + bytes(BPF - len(b)) if len(b) < BPF else b

    def audio_ready(self, j):
        return self.ended or (j + 1) * SPF <= self.n_samples

    def alpha(self, j0, j1):
        """Idle-bypass weights: activity = RMS > max(0.01, 0.08*p95) (phase-1 rule), pauses <= 6 frames bridged,
        activity extended PRE_ROLL frames earlier / HOLD frames later, then a (2*RAMP_HALF+1)-frame moving average,
        so the cross-fade into generated lips finishes about when the voice starts. Outside the utterance = silent."""
        r = np.asarray(self.rms, np.float32)
        K = len(r)
        if K == 0:
            return np.zeros(j1 - j0, np.float32)
        thr = max(0.01, 0.08 * float(np.percentile(r, 95)))
        act = r > thr
        idx = np.where(act)[0]
        for a, b in zip(idx[:-1], idx[1:]):
            if 1 < b - a <= 7:
                act[a:b] = True
        lo, hi = j0 - RAMP_HALF - HOLD, j1 + RAMP_HALF + PRE_ROLL
        n = hi - lo
        ext = np.zeros(n, bool)
        s0, s1 = max(lo, 0), min(hi, K)
        if s1 > s0:
            ext[s0 - lo:s1 - lo] = act[s0:s1]
        d = np.zeros(n, bool)
        for sh in range(-HOLD, PRE_ROLL + 1):  # d[i] = any(ext[i-HOLD .. i+PRE_ROLL])
            if sh >= 0:
                d[:n - sh] |= ext[sh:]
            else:
                d[-sh:] |= ext[:n + sh]
        k = 2 * RAMP_HALF + 1
        al = np.convolve(d.astype(np.float32), np.ones(k) / k, mode="same")
        off = RAMP_HALF + HOLD
        return al[off:off + (j1 - j0)].astype(np.float32)


# ---------------------------------------------------------------------------------------------------- session
class Session:
    def __init__(self, sid, face, expression, lk_url, lk_token, max_seconds):
        self.sid = sid
        self.face = face
        self.clips = renderer.faces[face]
        self.clip = self.clips.get(expression) or self.clips["neutral"]
        self.expr = self.clip.name
        self.lk_url, self.lk_token = lk_url, lk_token
        self.max_seconds = max_seconds
        self.loop = asyncio.get_running_loop()
        self.t0 = time.monotonic()
        self.room = None
        self.asrc = self.vsrc = None
        self.tasks = []
        self.ws = None
        self.out_q = asyncio.Queue()
        self.ws_timer = None
        self.closing = False
        self.closed = asyncio.Event()
        self.epoch = 0
        self.utts = collections.deque()
        self.open_utt = None
        self.closed_uids = collections.deque(maxlen=128)
        self.inflight = None
        self.clip_pos = 0
        self.last_frame = self.clip.rgba[0]
        self.shown = self.last_frame
        self.xf_from, self.xf_n, self.xf_i = None, 0, 0
        self.pending_expr = None
        self.listening = True  # LITE avatars start attentive (idle -> listening on connect)
        self.talking = False
        self.state = "listening"
        self.present_q = collections.deque()
        self.present_evt = asyncio.Event()
        self.st = collections.Counter()
        self.lat = []
        self.tl = [] if DEBUG_TL else None
        self.jobs_tl = [] if DEBUG_TL else None
        self.lead = LEAD_FRAMES
        self.vtr = None
        lm = self.clip.lms[0][48:68]
        self.mouth_rect = [int(lm[:, 0].min() - 10), int(lm[:, 1].min() - 8), int(lm[:, 0].max() + 10), int(lm[:, 1].max() + 25)]

    # -------------------------------------------------------------------------------------------- lifecycle
    async def start(self):
        W, H = self.clip.W, self.clip.H
        self.room = rtc.Room(loop=self.loop)
        self.room.on("disconnected", lambda *a: self.loop.create_task(self.stop("room_disconnected")))
        await asyncio.wait_for(self.room.connect(self.lk_url, self.lk_token, rtc.RoomOptions(auto_subscribe=False)), 15)
        self.vsrc = rtc.VideoSource(W, H)
        self.asrc = rtc.AudioSource(SR, 1, queue_size_ms=200, loop=self.loop)
        vtr = rtc.LocalVideoTrack.create_video_track("face-video", self.vsrc)
        self.vtr = vtr
        atr = rtc.LocalAudioTrack.create_audio_track("face-audio", self.asrc)
        await self.room.local_participant.publish_track(vtr, rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_CAMERA, simulcast=False,
            video_encoding=rtc.VideoEncoding(max_framerate=FPS, max_bitrate=FACE_VIDEO_BPS)))
        await self.room.local_participant.publish_track(atr, rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_MICROPHONE, dtx=False))
        for c in (self.tick_loop(), self.presenter(), self.ws_writer(), self.watchdog(), self.stats_loop()):
            self.tasks.append(self.loop.create_task(c))
        self.arm_ws_timer()
        jlog(logging.INFO, "session_started", sid=self.sid, face=self.face, expr=self.expr, room=self.room.name,
             max_seconds=self.max_seconds, connect_s=round(time.monotonic() - self.t0, 2))

    async def stop(self, reason):
        if self.closing:
            await self.closed.wait()
            return
        self.closing = True
        try:
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
            self.utts.clear()
            self.present_q.clear()
            sessions.pop(self.sid, None)
            if self.tl is not None:
                try:
                    json.dump({"sid": self.sid, "mouth_rect": self.mouth_rect, "cols": ["t_present", "kind", "uid", "j", "rms", "t_sched", "mouth"],
                               "rows": self.tl, "jobs": self.jobs_tl}, open(os.path.join(LOG_DIR, f"timeline_{self.sid}.json"), "w"))
                except Exception:
                    log.exception("timeline dump failed")
            jlog(logging.INFO, "session_stopped", sid=self.sid, reason=reason, secs=round(time.monotonic() - self.t0, 1),
                 stats=dict(self.st), speak_latency_ms=self.lat[-20:])
            self.closed.set()

    async def _flush_out(self):
        while not self.out_q.empty():
            await asyncio.sleep(0.01)

    async def watchdog(self):
        await asyncio.sleep(self.max_seconds)
        self.loop.create_task(self.stop("max_seconds"))

    async def stats_loop(self):
        """Every 10 s: encoder stats (resolution / fps / quality limitation) and process CPU, to the log."""
        import resource
        last = (time.monotonic(), sum(resource.getrusage(resource.RUSAGE_SELF)[:2]))
        while True:
            await asyncio.sleep(10)
            now = time.monotonic()
            cpu = sum(resource.getrusage(resource.RUSAGE_SELF)[:2])
            out = {"cpu_pct": round((cpu - last[1]) / (now - last[0]) * 100), "ticks": self.st["ticks"],
                   "late_video": self.st["late_video_frames"], "stalls": self.st["stall_ticks"]}
            last = (now, cpu)
            try:
                for x in await self.vtr.get_stats():
                    d = MessageToDict(x, preserving_proto_field_name=True)
                    if "outbound_rtp" in d:
                        o = d["outbound_rtp"].get("outbound", {})
                        out.update({k: o.get(k) for k in ("frame_width", "frame_height", "frames_per_second",
                                                           "quality_limitation_reason", "target_bitrate",
                                                           "encoder_implementation", "total_encode_time")})
            except Exception as e:
                out["stats_err"] = type(e).__name__
            jlog(logging.INFO, "pub_stats", sid=self.sid, **out)

    def arm_ws_timer(self):
        if self.ws_timer:
            self.ws_timer.cancel()
        self.ws_timer = self.loop.call_later(WS_GRACE_S, lambda: self.loop.create_task(self.stop("ws_gone")))

    # -------------------------------------------------------------------------------------------- websocket
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
        m = {"type": obj.pop("type"), "event_id": new_id(), "source_event_id": source}
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
            u = self.open_utt
            if u is None:
                return
            self.open_utt = None
            self.closed_uids.append(u.uid)
            u.end()
            self.emit({"type": "agent.audio_buffer_committed", "task": {"id": u.uid},
                       "duration_ms": round(u.dur_ms())}, u.uid)
            if u.n_total == 0:  # nothing to say
                self.utts.remove(u)
                self.emit({"type": "agent.speak_ended", "task": {"id": u.uid}}, u.uid)
            self.maybe_submit()
        elif t == "agent.interrupt":
            self.interrupt(eid)
        elif t in ("agent.start_listening", "agent.stop_listening"):
            self.listening = t == "agent.start_listening"
            self.set_state()
        elif t == "session.keep_alive":
            self.st["keep_alive"] += 1
        elif t == "agent.set_expression":
            e = m.get("expression")
            if not isinstance(e, str) or e not in self.clips:
                self.warn("unknown_expression", f"沒有「{str(e)[:20]}」這個表情，改用 neutral", eid)
                e = "neutral"
            self.pending_expr = e
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
        u = self.open_utt
        if u is None:
            uid = eid or new_id()
            if uid in self.closed_uids:
                self.st["stale_audio"] += 1
                self.warn("stale_audio", "這句已經結束或被打斷，多出來的聲音不播", eid)
                return
            u = Utt(uid, self.epoch)
            self.open_utt = u
            self.utts.append(u)
        if u.n_samples > MAX_UTT_SEC * SR:
            self.warn("utterance_too_long", "一句話太長，後面的聲音不播", eid)
            return
        u.append(pcm)
        self.emit({"type": "agent.audio_buffer_appended", "task": {"id": u.uid}, "duration_ms": round(len(pcm) / 48)}, eid)
        self.maybe_submit()

    def interrupt(self, eid):
        self.epoch += 1
        cleared = [u for u in self.utts]
        self.utts.clear()
        self.open_utt = None
        if self.asrc is not None:
            self.asrc.clear_queue()
        dropped = len(self.present_q)
        self.present_q.clear()
        self.present_evt.set()
        for u in cleared:
            self.closed_uids.append(u.uid)
            self.emit({"type": "agent.speak_interrupted", "task": {"id": u.uid}}, u.uid)
        if cleared:
            self.emit({"type": "agent.audio_buffer_cleared"}, eid)
            self.xf_from, self.xf_n, self.xf_i = self.shown, XF_INTERRUPT, 0
            self.clip_pos = max(self.clip_pos - dropped, 0)
        self.talking = False
        self.set_state()
        self.st["interrupts"] += 1
        jlog(logging.INFO, "interrupt", sid=self.sid, utts=len(cleared), dropped_frames=dropped)

    # -------------------------------------------------------------------------------------------- rendering
    def maybe_submit(self):
        if self.inflight is not None or self.closing:
            return
        buffered = sum(len(u.frames) for u in self.utts)
        prev = None
        for u in self.utts:
            if u.n_total is not None and u.next_render >= u.n_total:
                prev = u
                continue
            j0 = u.next_render
            if u.started and j0 <= u.next_play:  # rendering fell behind playout: skip ahead, audio keeps going
                self.st["catchup_skips"] += u.next_play + CATCHUP - j0
                j0 = u.next_render = u.next_play + CATCHUP
            cnt = u.n_avail() - j0
            room = MAX_AHEAD - buffered
            B = min(4, cnt, room)
            if B <= 0:
                return
            if B < 4 and not u.ended and u.started and len(u.frames) >= 6:
                return  # comfortably ahead: wait for a full batch
            if u.base_pos0 is None:
                u.clip = self.clip
                if prev is not None and prev.base_pos0 is not None:
                    u.base_pos0 = prev.base_pos0 + prev.n_total
                else:
                    u.base_pos0 = self.clip_pos + self.lead
                u.tick_submit = self.st["ticks"]
            self.submit(u, list(range(j0, j0 + B)))
            return

    def submit(self, u, js):
        j0, j1 = js[0], js[-1] + 1
        clip = u.clip
        kidx = [clip.index(u.base_pos0 + j) for j in js]
        # feature window (utterance time, ms): [ts, te) with PAD_MS of silence before the utterance / after its end
        ts = max(-PAD_MS, (j0 * 40 - LEFT_MS) // 20 * 20)
        dur = u.dur_ms()
        te = min(dur + (PAD_MS if u.ended else 0), j1 * 40 + RIGHT_MS)
        s0, s1 = int(round(ts * SR / 1000)), int(round(te * SR / 1000))
        seg = np.zeros(s1 - s0, np.float32)
        a0, a1 = max(s0, 0), min(s1, u.n_samples)
        if a1 > a0:
            seg[a0 - s0:a1 - s0] = np.frombuffer(u.pcm, np.int16, count=a1 - a0, offset=a0 * 2).astype(np.float32) / 32768.0
        c = (np.asarray(js) * 40 - ts) // 20
        cidx = c[:, None] + np.arange(-4, 6)[None, :]
        job_np = u.next_play
        job = Job(epoch=self.epoch, utt=u, clip=clip, js=js, kidx=kidx, alpha=u.alpha(j0, j1), wav16=seg, cidx=cidx,
                  t_submit=time.monotonic(), done_cb=lambda jb: self.loop.call_soon_threadsafe(self.on_done, jb))
        job.timing["np0"] = job_np
        self.inflight = job
        renderer.submit(job)

    def on_done(self, job):
        self.inflight = None
        if self.closing:
            return
        u = job.utt
        if job.epoch == self.epoch and u in self.utts:
            if job.err is not None or job.out is None:
                self.st["render_errors"] += 1
                for j, k in zip(job.js, job.kidx):  # keep going with the base frames (audio still plays)
                    u.frames[j] = job.clip.rgba[k]
            else:
                for b, j in enumerate(job.js):
                    if j >= u.next_play:
                        u.frames[j] = job.out[b]
                    else:
                        self.st["late_discarded"] += 1
            u.next_render = max(u.next_render, job.js[-1] + 1)
            if u.t_render0 is None:
                u.t_render0 = time.monotonic()
            self.st["rendered"] += len(job.js)
            self.st["render_ms"] += job.timing.get("run_ms", 0)
            if self.jobs_tl is not None:
                self.jobs_tl.append([round(job.t_submit, 4), round(time.monotonic(), 4), job.js[0], len(job.js),
                                     job.timing.get("np0"), u.next_play, job.timing])
        else:
            self.st["discarded_frames"] += len(job.js)
        self.maybe_submit()

    # -------------------------------------------------------------------------------------------- playout
    def idle_frame(self):
        if self.pending_expr is not None and not self.utts:
            new = self.clips.get(self.pending_expr, self.clips["neutral"])
            self.pending_expr = None
            if new is not self.clip:
                self.xf_from, self.xf_n, self.xf_i = self.shown, XF_EXPR, 0
                self.clip, self.expr, self.clip_pos = new, new.name, 0
                self.emit({"type": "agent.expression_updated", "expression": new.name})
                jlog(logging.INFO, "expression", sid=self.sid, expr=new.name)
        h = self.utts[0] if self.utts else None
        if h is not None and h.base_pos0 is not None and not h.started and self.clip_pos >= h.base_pos0:
            self.st["idle_hold"] += 1  # wait for the utterance's first base frame instead of jumping back later
            base = self.clip.rgba[self.clip.index(h.base_pos0 - 1)]
        else:
            base = self.clip.rgba[self.clip.index(self.clip_pos)]
            self.clip_pos += 1
        if self.xf_from is not None:
            self.xf_i += 1
            if self.xf_i > self.xf_n:
                self.xf_from = None
                return base
            a = self.xf_i / (self.xf_n + 1)
            return cv2.addWeighted(base, a, self.xf_from, 1 - a, 0)
        return base

    def next_tick(self):
        """-> (rgba frame, pcm bytes, events, speaking)."""
        ev = []
        while self.utts:
            u = self.utts[0]
            if not u.started:
                need = START_FRAMES if not u.ended else min(START_FRAMES, u.n_total)
                if all(j in u.frames for j in range(need)):
                    u.started = True
                    u.t_started = time.monotonic()
                    if u.tick_submit is not None:  # ticks from first job to start -> next base_pos0 guess
                        el = self.st["ticks"] - u.tick_submit
                        self.lead = int(min(12, max(2, round(0.5 * self.lead + 0.5 * el))))
                    if u.base_pos0 > self.clip_pos:
                        self.st["base_jump_fwd"] += u.base_pos0 - self.clip_pos
                    self.clip_pos = u.base_pos0
                    self.xf_from = None
                    ev.append(("started", u))
                else:
                    break
            j = u.next_play
            if u.ended and j >= u.n_total:
                self.utts.popleft()
                ev.append(("ended", u))
                continue
            fr = u.frames.pop(j, None)
            if fr is None and u.audio_ready(j):
                # video is late but the audio is here: never hold the audio back, repeat the last picture
                u.late += 1
                self.st["late_video_frames"] += 1
                u.next_play += 1
                self.clip_pos = u.base_pos0 + u.next_play
                self.maybe_submit()
                return self.last_frame, u.audio(j), ev, True, ("d", u.uid[:8], j)
            if fr is None:  # the audio itself has not arrived yet (sender slower than real time)
                u.stalls += 1
                self.st["stall_ticks"] += 1
                return self.last_frame, SILENCE, ev, True, ("s", u.uid[:8], j)
            u.next_play += 1
            self.clip_pos = u.base_pos0 + u.next_play
            self.last_frame = fr
            self.maybe_submit()
            return fr, u.audio(j), ev, True, ("u", u.uid[:8], j)
        fr = self.idle_frame()
        self.last_frame = fr
        return fr, SILENCE, ev, False, ("i", "", self.clip_pos)

    async def tick_loop(self):
        t_next = time.monotonic()
        silence = rtc.AudioFrame(SILENCE, SR, 1, SPF)
        try:
            while True:
                now = time.monotonic()
                if now < t_next:
                    await asyncio.sleep(t_next - now)
                elif now - t_next > 0.25:
                    self.st["tick_resync"] += 1
                    t_next = now
                t_next += TICK
                fr, pcm, ev, speaking, info = self.next_tick()
                await self.asrc.capture_frame(rtc.AudioFrame(pcm, SR, 1, SPF))
                q = self.asrc.queued_duration
                at = time.monotonic() + max(0.0, q - TICK + AV_OFFSET)
                if self.tl is not None:
                    x = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
                    info = info + (round(float(np.sqrt((x * x).mean())), 5), round(at, 4))
                self.present_q.append((at, fr, ev, info))
                self.present_evt.set()
                self.st["ticks"] += 1
                if not speaking and q < AUDIO_Q_TARGET:  # idle: keep a small jitter margin in the audio queue
                    await self.asrc.capture_frame(silence)
                    self.st["audio_topup"] += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("tick loop crashed")
            self.loop.create_task(self.stop("internal_error"))

    async def presenter(self):
        W, H = self.clip.W, self.clip.H
        try:
            while True:
                if not self.present_q:
                    self.present_evt.clear()
                    await self.present_evt.wait()
                    continue
                at, fr, ev, info = self.present_q[0]
                d = at - time.monotonic()
                if d > 0.002:
                    await asyncio.sleep(d)
                    continue
                self.present_q.popleft()
                self.vsrc.capture_frame(rtc.VideoFrame(W, H, rtc.VideoBufferType.RGBA, memoryview(fr.reshape(-1))))
                self.shown = fr
                self.st["frames_out"] += 1
                if self.tl is not None:
                    x1, y1, x2, y2 = self.mouth_rect
                    c = fr[y1:y2, x1:x2, :3].astype(np.float32)
                    yv = 0.299 * c[..., 0] + 0.587 * c[..., 1] + 0.114 * c[..., 2]
                    self.tl.append([round(time.monotonic(), 4), *info, round(float((yv < 90).mean()), 4)])
                if DEBUG_DUMP and info[0] == "u" and info[2] % DEBUG_DUMP == 0:
                    dd = os.path.join(LOG_DIR, f"dump_{self.sid}")
                    os.makedirs(dd, exist_ok=True)
                    self.loop.run_in_executor(None, cv2.imwrite, os.path.join(dd, f"{info[1]}_{info[2]:05d}.png"),
                                              cv2.cvtColor(fr, cv2.COLOR_RGBA2BGR))
                if d < -0.03:
                    self.st["late_frames"] += 1
                for kind, u in ev:
                    if kind == "started":
                        lat = (time.monotonic() - u.t_first) * 1000
                        self.lat.append(round(lat))
                        self.talking = True
                        self.emit({"type": "agent.speak_started", "task": {"id": u.uid}}, u.uid)
                        jlog(logging.INFO, "speak_started", sid=self.sid, uid=u.uid[:12], first_chunk_to_started_ms=round(lat),
                             first_chunk_to_render_ms=round(((u.t_render0 or time.monotonic()) - u.t_first) * 1000))
                    elif kind == "ended":
                        self.emit({"type": "agent.speak_ended", "task": {"id": u.uid}}, u.uid)
                        jlog(logging.INFO, "speak_ended", sid=self.sid, uid=u.uid[:12], frames=u.n_total, stalls=u.stalls,
                             late_video=u.late, chunks=u.chunks)
                        self.talking = bool(self.utts) and self.utts[0].started
                self.set_state()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("presenter crashed")
            self.loop.create_task(self.stop("internal_error"))


# ---------------------------------------------------------------------------------------------------- http
async def h_health(req):
    faces = sorted(renderer.faces) if renderer.ready else []
    return web.json_response({
        "ok": True, "gpu": renderer.gpu_name or None, "faces": faces,
        "expressions": {f: sorted(renderer.faces[f]) for f in faces},
        "sessions": len(sessions), "ready": bool(renderer.ready),
        **({"error": "引擎啟動失敗"} if renderer.error else {})})


async def h_session(req):
    if not secret_ok(req):
        return err(401, "未授權")
    try:
        b = await req.json()
        assert isinstance(b, dict)
    except Exception:
        return err(400, "格式錯誤")
    sid, face = b.get("session_id"), b.get("face", "wang")
    url, tok = b.get("livekit_url"), b.get("livekit_token")
    expr = b.get("expression") or "neutral"
    try:
        max_s = int(b.get("max_seconds") or 900)
    except (TypeError, ValueError):
        return err(400, "max_seconds 格式錯誤")
    if not isinstance(sid, str) or not SID_RE.match(sid):
        return err(400, "session_id 格式錯誤")
    if not isinstance(url, str) or not url.startswith(("wss://", "ws://")) or not isinstance(tok, str) or not tok:
        return err(400, "缺少 LiveKit 連線資料")
    if not renderer.ready:
        return err(503, "真人引擎還在啟動中，請稍後再試")
    if face not in renderer.faces:
        return err(400, "沒有這個頭像")
    max_s = max(10, min(max_s, 4 * 3600))
    async with session_lock:
        if sid in sessions:
            return web.json_response({"ok": True, "ws_path": "/face/ws"})
        if sessions:
            return err(409, "真人畫面已經有一通在使用中，請稍後再試")
        if not isinstance(expr, str) or expr not in renderer.faces[face]:
            expr = "neutral"
        s = Session(sid, face, expr, url, tok, max_s)
        sessions[sid] = s
        try:
            await s.start()
        except Exception as e:
            jlog(logging.WARNING, "session_start_failed", sid=sid, err=type(e).__name__, detail=str(e)[:200])
            await s.stop("start_failed")
            return err(502, "連不上 LiveKit 房間，請稍後再試")
    return web.json_response({"ok": True, "ws_path": "/face/ws"})


async def h_stop(req):
    if not secret_ok(req):
        return err(401, "未授權")
    try:
        b = await req.json()
        sid = b.get("session_id")
    except Exception:
        return err(400, "格式錯誤")
    s = sessions.get(sid) if isinstance(sid, str) else None
    if s is not None:
        await s.stop("api_stop")
    return web.json_response({"ok": True})


async def h_ws(req):
    q = req.query
    sid, exp, sig = q.get("session", ""), q.get("exp", ""), q.get("sig", "")
    if not ws_sig_ok(sid, exp, sig):
        return err(403, "連線驗證失敗")
    s = sessions.get(sid)
    if s is None or s.closing:
        return err(404, "找不到這通真人畫面")
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4 * 1024 * 1024, compress=False)
    await ws.prepare(req)
    await s.attach(ws)
    jlog(logging.INFO, "ws_open", sid=sid)
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    m = json.loads(msg.data)
                    if not isinstance(m, dict):
                        raise ValueError
                except Exception:
                    s.error("invalid_json", "訊息不是 JSON 物件")
                    continue
                try:
                    s.handle(m)
                except Exception:
                    log.exception("handle failed")
                    s.error("internal_error", "這個指令處理失敗", m.get("event_id") if isinstance(m.get("event_id"), str) else None)
            elif msg.type == WSMsgType.BINARY:
                s.warn("unsupported_event", "請用 JSON 文字訊息")
            elif msg.type == WSMsgType.ERROR:
                break
    finally:
        s.detach(ws)
        jlog(logging.INFO, "ws_closed", sid=sid)
    return ws


@web.middleware
async def guard(req, handler):
    try:
        return await handler(req)
    except web.HTTPException:
        raise
    except Exception:
        log.exception("http handler failed")
        return err(500, "真人引擎內部錯誤")


async def on_shutdown(app):
    for s in list(sessions.values()):
        await s.stop("shutdown")


def _gc_watch():
    import gc
    st = {}

    def cb(phase, info):
        if phase == "start":
            st["t"] = time.perf_counter()
        elif "t" in st:
            ms = (time.perf_counter() - st.pop("t")) * 1000
            if ms > 15:
                jlog(logging.WARNING, "gc_pause", ms=round(ms, 1), gen=info.get("generation"))
    gc.callbacks.append(cb)


def main():
    global renderer
    setup_logging()
    sys.setswitchinterval(0.002)  # the event loop gets the GIL back faster while the GPU worker runs Python code
    _gc_watch()
    if not FACE_SECRET:
        jlog(logging.ERROR, "no_face_secret", msg="FACE_SECRET is not set: /face/session and /face/ws will refuse all requests")
    renderer = Renderer(compile_models=os.environ.get("FACE_COMPILE", "1") == "1")
    renderer.start()
    app = web.Application(middlewares=[guard], client_max_size=1024 * 1024)
    app.add_routes([web.get("/face/health", h_health), web.post("/face/session", h_session),
                    web.post("/face/stop", h_stop), web.get("/face/ws", h_ws)])
    app.on_shutdown.append(on_shutdown)
    jlog(logging.INFO, "listening", port=int(os.environ.get("FACE_PORT", "8010")), lookahead_ms=LA_MS)
    web.run_app(app, host="0.0.0.0", port=int(os.environ.get("FACE_PORT", "8010")), access_log=None,
                handle_signals=True, print=None)


if __name__ == "__main__":
    main()
