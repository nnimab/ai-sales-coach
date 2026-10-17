"""Real-time Ditto talking-head stream (antgroup ditto-talkinghead, TensorRT, online mode) for the live engine.

Audio and video are kept in sync INSIDE the stream: every get_frame() returns one 40 ms output tick =
(rgb frame, the 960 int16 samples @24 kHz that belong to it, uid or None, last-flag). Speech audio is delayed
inside the stream by exactly the time the video needs (latency_s). With no speech queued the stream emits
silent ticks with idle frames (Ditto keeps generating from silence: blinks + small head micro-motion).

Clock: the CONSUMER drives the timeline (call get_frame() at 25 Hz). The producer works `ahead` ticks in front
of the consumer; if a frame is not rendered in time the tick is still returned (with its audio) and the previous
frame is repeated, so audio never stutters.

Pipeline (3 worker threads, all TRT engines as shipped = ditto_trt_Ampere_Plus, TRT 8.6.1):
  producer : per 200 ms hop: assign 5 ticks of audio -> 16 kHz chunk (120 ms history + 85 ms lookahead)
             -> HuBERT (5 features) -> LMDM (online, overlap_v2=75 -> one call per hop, 5 new frames)
  render   : motion_stitch (calm-head tuning, see _mix) -> warp -> SPADE decoder (512x512)
  putback  : paste into the source photo (+ static hand keep-mask) -> ready frames
Frames for hop n are emitted after hop n+1 is processed (Ditto's online fuse), so the algorithmic delay is
2 hops (400 ms) + 85 ms lookahead; ahead = 2*G - 5 + margin ticks.

ONE DittoStream per process (it patches Ditto's module-level _mix_s_d_info).
Run with the Ditto venv: see /workspace/ditto_live/env.sh (LD_LIBRARY_PATH for TRT 8.6 + cuDNN 8) and
/workspace/venv_ditto/bin/python. InsightFace (avatar registration) is non-commercial: OK for the demo only.
"""
import os
import sys
import time
import threading
import queue
import collections

DITTO = os.environ.get("DITTO_ROOT", "/workspace/bakeoff/ditto")
if DITTO not in sys.path:
    sys.path.insert(0, DITTO)

import numpy as np
from scipy.signal import resample_poly

HERE = os.path.dirname(os.path.abspath(__file__))
TICK = 0.04
SR_OUT = 24000
SPT = 960                  # 24 kHz samples per tick
HOP = 5                    # ticks per HuBERT hop (chunksize (3,5,2))
HIST24 = 3 * SPT           # 120 ms history in the HuBERT chunk
LOOK24 = 2040              # 85 ms lookahead (6480 @16k = 120 + 200 + 85 ms)
MARG24 = 480               # resampling margin (20 ms) on both sides
LIP = [6, 12, 14, 17, 19, 20]

DEFAULTS = dict(
    cfg_pkl=f"{DITTO}/checkpoints/ditto_cfg/v0.4_hubert_cfg_trt_online.pkl",
    data_root=f"{DITTO}/checkpoints/ditto_trt_Ampere_Plus",
    keep_mask=os.path.join(HERE, "assets", "keep_hand.png"),  # "" / None = off
    a_pose=0.35,           # head rotation damping about the photo pose (1 = Ditto default)
    a_t=0.35,              # head translation damping
    a_lip=0.75,            # lip motion gain
    hp_s=3.0,              # slow re-centering of pose/t toward the photo pose (EMA time constant, 0 = off)
    smo_k_d=7,             # Ditto motion smoothing window (frames) over scale/pose/t
    emo=4,                 # 4 = Neutral; None = cfg default emotion sequence
    steps=10,              # DDIM steps (5 = faster, slightly less precise)
    overlap_v2=75,         # 75 -> 5 new frames per LMDM call (low latency); 70 -> 10 (cfg default, +200 ms)
    margin_ticks=3,        # compute margin in front of the consumer
    max_frame_wait=0.025,  # get_frame waits at most this long for a late frame, then repeats the previous one
    prebuffer_s=0.30,      # an utterance starts only when this much audio is queued (or it has ended)
    seed=0,
)


class _Utt:
    __slots__ = ("uid", "buf", "pos", "ended")

    def __init__(self, uid):
        self.uid, self.buf, self.pos, self.ended = uid, bytearray(), 0, False

    def avail(self):  # samples
        return (len(self.buf) // 2) - self.pos


class DittoStream:
    def __init__(self, source_path=None, **tuning):
        unknown = set(tuning) - set(DEFAULTS)
        if unknown:
            raise TypeError(f"unknown tuning keys {sorted(unknown)}")
        self.cfg = dict(DEFAULTS, **tuning)
        c = self.cfg
        if source_path is None:
            source_path = os.path.join(HERE, "assets", "wang_idle_f0.png")
        self.source_path = source_path
        np.random.seed(c["seed"])
        t0 = time.perf_counter()
        from stream_pipeline_online import StreamSDK
        import core.atomic_components.motion_stitch as MS
        self._MS = MS
        self._orig_mix = MS._mix_s_d_info
        MS._mix_s_d_info = self._mix
        self.sdk = sdk = StreamSDK(c["cfg_pkl"], c["data_root"])
        kw = dict(sdk.default_kwargs)
        self._kw = kw
        self.source_info = sdk.avatar_registrar(
            source_path, max_dim=kw.get("max_size", 1920), n_frames=-1,
            crop_scale=kw.get("crop_scale", 2.3), crop_vx_ratio=kw.get("crop_vx_ratio", 0),
            crop_vy_ratio=kw.get("crop_vy_ratio", -0.125), crop_flag_do_rot=kw.get("crop_flag_do_rot", True))
        si = self.source_info
        self._x_s_info = si["x_s_info_lst"][0]
        self._f_s = si["f_s_lst"][0]
        self._frame_rgb = np.ascontiguousarray(si["img_rgb_lst"][0])
        self._M = si["M_c2o_lst"][0]
        self.H, self.W = self._frame_rgb.shape[:2]
        self._putback = sdk.putback
        if c["keep_mask"]:
            self._putback = self._make_keep(sdk.putback, c["keep_mask"])
        self.G = 80 - c["overlap_v2"]
        self.ahead = 2 * self.G - HOP + c["margin_ticks"]
        self.init_s = time.perf_counter() - t0
        self._ema, self._ema_d0, self._lip_gain = None, None, 1.0
        self._warmup()
        self._reset_state()
        self.ready_s = time.perf_counter() - t0

    # ------------------------------------------------------------------ setup
    def _make_keep(self, putback, path, feather=2.5):
        import cv2
        m = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if m is None or m.shape != (self.H, self.W):
            print(f"[ditto_stream] keep mask {path} missing or size mismatch -> off", flush=True)
            return putback
        m = cv2.GaussianBlur(m.astype(np.float32) / 255.0, (0, 0), feather)
        ys, xs = np.where(m > 1e-3)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        mk = m[y0:y1, x0:x1, None]
        s = self._frame_rgb[y0:y1, x0:x1].astype(np.float32)

        def pb(frame_rgb, render_img, M_c2o):
            out = putback(frame_rgb, render_img, M_c2o)
            r = out[y0:y1, x0:x1].astype(np.float32)
            out[y0:y1, x0:x1] = np.clip(r + mk * (s - r) + 0.5, 0, 255).astype(np.uint8)
            return out
        return pb

    def _setup_motion(self):
        c, kw, sdk = self.cfg, self._kw, self.sdk
        emo = kw.get("emo", 4) if c["emo"] is None else int(c["emo"])
        sdk.condition_handler.setup(self.source_info, emo, eye_f0_mode=kw.get("eye_f0_mode", False), ch_info=None)
        sdk.audio2motion.setup(
            sdk.condition_handler.x_s_info_0, overlap_v2=c["overlap_v2"], fix_kp_cond=kw.get("fix_kp_cond", 0),
            fix_kp_cond_dim=kw.get("fix_kp_cond_dim", None), sampling_timesteps=c["steps"], online_mode=True,
            v_min_max_for_clip=kw.get("v_min_max_for_clip", None), smo_k_d=c["smo_k_d"])
        sdk.motion_stitch.setup(
            N_d=-1, use_d_keys=None, relative_d=True, drive_eye=kw.get("drive_eye", None),
            delta_eye_arr=kw.get("delta_eye_arr", None), delta_eye_open_n=kw.get("delta_eye_open_n", 0),
            fade_out_keys=kw.get("fade_out_keys", ("exp",)), fade_type=kw.get("fade_type", ""), flag_stitching=True,
            is_image_flag=self.source_info["is_image_flag"], x_s_info=self._x_s_info, d0=None, ch_info=None,
            overall_ctrl_info=kw.get("overall_ctrl_info", {}))

    def _warmup(self):
        sdk = self.sdk
        self._setup_motion()
        z = np.zeros(6480, np.float32)
        self._sil_feat = sdk.wav2feat(z, chunksize=(3, 5, 2))
        a2m = sdk.audio2motion
        af = sdk.wav2feat.wav2feat(np.zeros(80 * 640, np.float32), sr=16000)
        kp = a2m(sdk.condition_handler(af[:80], 0)[None], None)
        info = a2m.cvt_fmt(kp[:, :1])[0]
        sdk.motion_stitch.d0 = info
        for _ in range(3):
            x_s, x_d = sdk.motion_stitch(self._x_s_info, dict(info))
            img = sdk.decode_f3d(sdk.warp_f3d(self._f_s, x_s, x_d))
            self._putback(self._frame_rgb, img, self._M)

    def _reset_state(self):
        self._setup_motion()
        sdk = self.sdk
        self._audio_feat = sdk.wav2feat.wav2feat(np.zeros(self.cfg["overlap_v2"] * 640, np.float32), sr=16000)
        self._cond_idx_start = -len(self._audio_feat)
        self._item_buf = np.zeros((0, self._audio_feat.shape[1]), np.float32)
        self._res = None
        self._valid_start = None
        self._local_idx = 0
        self._global_idx = 0
        self._gen_idx = 0
        self._ema = None
        self._ema_d0 = None
        self._lip_gain = 1.0
        self._mute_until = -1
        # timeline / IO state
        self._cv = threading.Condition()
        self._fifo = collections.deque()
        self._dropped_uids = set()
        self._epoch = 0
        self._hop = 0                       # next hop to assign
        self._hist24 = np.zeros(HIST24 + MARG24, np.int16)
        self._meta = {}                     # tick -> (pcm int16[960], uid, last, epoch, speech)
        self._ready = {}                    # tick -> rgb
        self._next_tick = 0                 # next tick get_frame() returns
        self._last_rgb = self._frame_rgb.copy()
        self._render_q = queue.Queue()
        self._put_q = queue.Queue()
        self._closed = False
        self._threads = []
        self.n_rendered = self.n_repeated = self.n_skipped = 0
        self.t_render = collections.deque(maxlen=500)
        self.t_hop = collections.deque(maxlen=200)

    # ---------------------------------------------------------- motion tuning
    def _mix(self, x_s_info, x_d_info, use_d_keys=("exp", "pitch", "yaw", "roll", "t"), d0=None):
        """Replacement for motion_stitch._mix_s_d_info: pose/t damped about the PHOTO pose (+ slow re-centering),
        lips scaled; non-lip expression and eyes are handled by MotionStitch afterwards (blinks untouched)."""
        if d0 is None:
            return self._orig_mix(x_s_info, x_d_info, use_d_keys, d0)
        c = self.cfg
        deg = self._deg
        fac = use_d_keys if isinstance(use_d_keys, dict) else {k: 1.0 for k in use_d_keys}
        cur = np.concatenate([deg(x_d_info["pitch"]) - deg(d0["pitch"]), deg(x_d_info["yaw"]) - deg(d0["yaw"]),
                              deg(x_d_info["roll"]) - deg(d0["roll"]),
                              (x_d_info["t"] - d0["t"]).reshape(-1).astype(np.float32)])
        if c["hp_s"] and c["hp_s"] > 0:
            if self._ema is None or self._ema_d0 is not d0:
                self._ema, self._ema_d0 = cur.copy(), d0
            else:
                self._ema += (cur - self._ema) * np.float32(TICK / c["hp_s"])
            cur = cur - self._ema
        lipm = np.ones((21, 3), np.float32)
        lipm[LIP] = c["a_lip"] * self._lip_gain
        lipm = lipm.reshape(1, -1)
        out = {}
        for i, k in enumerate(("pitch", "yaw", "roll")):
            if k in fac:
                out[k] = (deg(x_s_info[k]) + np.float32(c["a_pose"] * fac[k]) * cur[i:i + 1]).astype(np.float32)
        if "t" in fac:
            out["t"] = (x_s_info["t"] + np.float32(c["a_t"] * fac["t"]) * cur[3:].reshape(x_s_info["t"].shape)).astype(np.float32)
        if "exp" in fac:
            out["exp"] = (x_s_info["exp"] + (x_d_info["exp"] - d0["exp"]) * np.float32(fac["exp"]) * lipm).astype(np.float32)
        for k, v in x_s_info.items():
            if k not in out:
                out[k] = v
        return out

    def _deg(self, x):
        return np.asarray(self._MS.bin66_to_degree(x), dtype=np.float32).reshape(-1)[:1]

    # ------------------------------------------------------------- public API
    @property
    def latency_s(self):
        """Delay from pushing speech (when nothing else is queued) to get_frame() returning its first tick, upper
        bound for audio that arrives faster than real time: (ahead + one hop) ticks. Audio is delayed inside the
        stream by this amount, so A/V are in sync by construction."""
        return (self.ahead + HOP) * TICK

    def start(self, wait_s=30.0):
        if self._threads:
            return
        self._threads = [threading.Thread(target=self._run, args=(f,), name=f"ditto-{f.__name__}", daemon=True)
                         for f in (self._producer, self._render, self._putback_worker)]
        for t in self._threads:
            t.start()
        t_end = time.perf_counter() + wait_s
        with self._cv:
            while 0 not in self._ready and not self._closed and time.perf_counter() < t_end:
                self._cv.wait(0.05)

    def push_audio(self, pcm16_24k: bytes, uid: str):
        if not pcm16_24k:
            return
        with self._cv:
            if uid in self._dropped_uids:
                return
            u = self._fifo[-1] if self._fifo and self._fifo[-1].uid == uid and not self._fifo[-1].ended else None
            if u is None:
                u = _Utt(uid)
                self._fifo.append(u)
            u.buf += pcm16_24k

    def end_utterance(self, uid: str):
        with self._cv:
            for u in self._fifo:
                if u.uid == uid:
                    u.ended = True
                    return
            if uid not in self._dropped_uids:  # ended before any audio arrived -> one silent closing tick
                u = _Utt(uid)
                u.ended = True
                self._fifo.append(u)

    def interrupt(self):
        """Drop all queued and not-yet-played speech; already assigned ticks are emitted silent (uid None) and
        their lips are faded shut over ~3 frames (and held shut until the dropped speech has left Ditto's motion window);
        frames already rendered (a few ticks) keep their mouth, so the mouth closes ~0.15-0.2 s after interrupt()."""
        with self._cv:
            for u in self._fifo:
                self._dropped_uids.add(u.uid)
            for m in self._meta.values():
                if m[1] is not None:
                    self._dropped_uids.add(m[1])
            self._fifo.clear()
            self._epoch += 1
            # frames whose LMDM window / online fuse still contains the dropped speech: keep the lips closed
            self._mute_until = self._hop * HOP + 2 * self.G + HOP
            self._cv.notify_all()

    def get_frame(self, timeout=1.0):
        """Next output tick: (rgb HxWx3 uint8, pcm bytes (960 x int16 @24 kHz), uid or None, last)."""
        t_end = time.perf_counter() + (timeout if timeout is not None else 1e9)
        with self._cv:
            k = self._next_tick
            while k not in self._meta and not self._closed:
                rem = t_end - time.perf_counter()
                if rem <= 0:  # producer not running yet: filler tick outside the timeline
                    return self._last_rgb, bytes(SPT * 2), None, False
                self._cv.wait(rem)
            if self._closed and k not in self._meta:
                return self._last_rgb, bytes(SPT * 2), None, False
            pcm, uid, last, epoch, speech = self._meta.pop(k)
            fw_end = min(t_end, time.perf_counter() + self.cfg["max_frame_wait"])
            while k not in self._ready and not self._closed:
                rem = fw_end - time.perf_counter()
                if rem <= 0:
                    break
                self._cv.wait(rem)
            rgb = self._ready.pop(k, None)
            if rgb is None:
                rgb = self._last_rgb
                self.n_repeated += 1
            else:
                self._last_rgb = rgb
            for kk in [kk for kk in self._ready if kk < k]:
                del self._ready[kk]
            self._next_tick = k + 1
            if epoch != self._epoch or (uid is not None and uid in self._dropped_uids):
                pcm, uid, last = None, None, False
            self._cv.notify_all()
        return rgb, (pcm.tobytes() if pcm is not None else bytes(SPT * 2)), uid, last

    def stats(self):
        tr = np.array(self.t_render) * 1000 if self.t_render else np.zeros(1)
        th = np.array(self.t_hop) * 1000 if self.t_hop else np.zeros(1)
        return {"ticks": self._next_tick, "rendered": self.n_rendered, "repeated": self.n_repeated,
                "skipped_late": self.n_skipped, "render_ms_p50": round(float(np.median(tr)), 2),
                "render_ms_p95": round(float(np.percentile(tr, 95)), 2), "hop_ms_p50": round(float(np.median(th)), 2),
                "hop_ms_p95": round(float(np.percentile(th, 95)), 2), "ahead_ticks": self.ahead,
                "latency_s": round(self.latency_s, 3), "init_s": round(self.init_s, 1), "ready_s": round(self.ready_s, 1)}

    def close(self):
        with self._cv:
            self._closed = True
            self._cv.notify_all()
        for t in self._threads:
            t.join(timeout=5)
        self._MS._mix_s_d_info = self._orig_mix

    # ---------------------------------------------------------------- workers
    def _run(self, fn):
        try:
            fn()
        except Exception:
            import traceback
            traceback.print_exc()
            with self._cv:
                self._closed = True
                self._cv.notify_all()

    def _take_tick(self, hop_start):
        """Next 960 samples of the timeline from the speech FIFO (called with the lock held)."""
        while self._fifo:
            u = self._fifo[0]
            a = u.avail()
            if u.pos == 0 and not u.ended and a < int(self.cfg["prebuffer_s"] * SR_OUT):
                break                                    # utterance not started yet: wait for a prebuffer
            if a >= SPT or u.ended:
                n = min(a, SPT)
                pcm = np.zeros(SPT, np.int16)
                if n:
                    pcm[:n] = np.frombuffer(bytes(u.buf[u.pos * 2:(u.pos + n) * 2]), np.int16)
                u.pos += n
                last = u.ended and u.avail() == 0
                if last:
                    self._fifo.popleft()
                return pcm, u.uid, last, n > 0
            return np.zeros(SPT, np.int16), u.uid, False, False   # underrun inside an utterance (gap)
        return np.zeros(SPT, np.int16), None, False, False

    def _peek(self, n):
        out = np.zeros(n, np.int16)
        got = 0
        for u in self._fifo:
            a = u.avail()
            if u.pos == 0 and not u.ended and a < int(self.cfg["prebuffer_s"] * SR_OUT):
                break
            take = min(a, n - got)
            if take > 0:
                out[got:got + take] = np.frombuffer(bytes(u.buf[u.pos * 2:(u.pos + take) * 2]), np.int16)
                got += take
            if got >= n or not u.ended:
                break
        return out

    def _producer(self):
        sdk = self.sdk
        while True:
            with self._cv:
                while not self._closed and HOP * self._hop > self._next_tick + self.ahead:
                    self._cv.wait(0.5)
                if self._closed:
                    return
                t0 = time.perf_counter()
                k0 = self._hop * HOP
                pcms = []
                for i in range(HOP):
                    pcm, uid, last, speech = self._take_tick(k0)
                    self._meta[k0 + i] = (pcm, uid, last, self._epoch, speech)
                    pcms.append(pcm)
                look = self._peek(LOOK24 + MARG24)
                self._hop += 1
                self._cv.notify_all()
            hop24 = np.concatenate(pcms)
            x24 = np.concatenate([self._hist24, hop24, look])
            self._hist24 = x24[len(hop24): len(hop24) + len(self._hist24)].copy()   # last 140 ms before the lookahead
            if not x24.any():
                feat = self._sil_feat
            else:
                x16 = resample_poly(x24.astype(np.float32) / 32768.0, 2, 3).astype(np.float32)
                chunk = x16[MARG24 * 2 // 3: MARG24 * 2 // 3 + 6480]
                feat = sdk.wav2feat(chunk, chunksize=(3, 5, 2))
            self._feed(feat)
            self.t_hop.append(time.perf_counter() - t0)

    def _feed(self, feat):
        """Port of StreamSDK._audio2motion_worker for one hop of features (continuous, never ends)."""
        sdk = self.sdk
        a2m, ch = sdk.audio2motion, sdk.condition_handler
        seq, valid = a2m.seq_frames, a2m.valid_clip_len
        self._item_buf = np.concatenate([self._item_buf, feat], 0)
        if self._item_buf.shape[0] < valid:
            return
        self._audio_feat = np.concatenate([self._audio_feat, self._item_buf], 0)
        self._item_buf = self._item_buf[:0]
        while True:
            aud = self._audio_feat[self._local_idx: self._local_idx + seq]
            if len(aud) < seq:
                break
            cond = ch(aud, self._global_idx + self._cond_idx_start)[None]
            self._res = a2m(cond, self._res)
            if self._valid_start is None:
                self._valid_start = self._res.shape[1] - a2m.fuse_length
                sdk.motion_stitch.d0 = a2m.cvt_fmt(self._res[0:1])[0]
            else:
                vals = self._res[:, self._valid_start: self._valid_start + valid]
                for info in a2m.cvt_fmt(vals):
                    self._render_q.put((self._gen_idx, info))
                    self._gen_idx += 1
                self._valid_start += valid
                L = self._res.shape[1]
                if L > seq * 2:
                    cut = L - seq * 2
                    self._res = self._res[:, cut:]
                    self._valid_start -= cut
            self._local_idx += valid
            self._global_idx += valid
            if self._local_idx >= len(self._audio_feat):
                break
        L = len(self._audio_feat)
        if L > seq * 2:
            cut = L - seq * 2
            self._audio_feat = self._audio_feat[cut:]
            self._local_idx -= cut

    def _render(self):
        sdk = self.sdk
        while not self._closed:
            try:
                k, info = self._render_q.get(timeout=0.5)
            except queue.Empty:
                continue
            with self._cv:
                if k < self._next_tick:          # consumer already passed this tick
                    self.n_skipped += 1
                    continue
                m = self._meta.get(k)
                fresh = m is not None and m[4] and m[3] == self._epoch and m[1] not in self._dropped_uids
                stale = (m is not None and m[4] and not fresh) or (k < self._mute_until and not fresh)
            t0 = time.perf_counter()
            tgt = 0.0 if stale else 1.0
            self._lip_gain += float(np.clip(tgt - self._lip_gain, -0.34, 0.34))
            x_s, x_d = sdk.motion_stitch(self._x_s_info, info)
            img = sdk.decode_f3d(sdk.warp_f3d(self._f_s, x_s, x_d))
            self._put_q.put((k, img, t0))

    def _putback_worker(self):
        while not self._closed:
            try:
                k, img, t0 = self._put_q.get(timeout=0.5)
            except queue.Empty:
                continue
            rgb = self._putback(self._frame_rgb, img, self._M)
            with self._cv:
                if k >= self._next_tick:
                    self._ready[k] = rgb
                    self.n_rendered += 1
                else:
                    self.n_skipped += 1
                self.t_render.append(time.perf_counter() - t0)
                self._cv.notify_all()
