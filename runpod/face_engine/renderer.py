# Face engine GPU side: MuseTalk 1.5 (UNet + SD-VAE, whisper-tiny features) + v11 compositing, one worker thread.
# Model loading follows LiveTalking's MuseTalk port (Apache-2.0, https://github.com/lipku/LiveTalking, see NOTICE).
import os, sys, time, json, glob, pickle, threading, queue, logging
import numpy as np
import cv2
import torch

LT = "/workspace/LiveTalking"
if LT not in sys.path:
    sys.path.insert(0, LT)

from gpu_blend import ClipStatics, V11  # noqa: E402
from scipy.signal import resample_poly  # noqa: E402

log = logging.getLogger("face.render")
DEV = torch.device("cuda")
FACES_DIR = os.environ.get("FACE_FACES_DIR", "/workspace/face_engine/faces")
CACHE_DIR = os.environ.get("FACE_CACHE_DIR", "/workspace/face_engine/cache")
MEGA = os.path.join(CACHE_DIR, "compile_artifacts.bin")
BATCHES = (2, 4)


class Clip:
    """One expression base clip prepared as a MuseTalk avatar (fixed bbox) + playback schedule.
    Playback position p (frames since the clip was entered) -> frame index:
      loop=forward : p mod N (the clip's tail is cross-faded into frame 0)
      loop=pingpong: frames [0, intro) once, then ping-pong over [intro, N)."""

    def __init__(self, face, name, d, cfg):
        self.face, self.name, self.dir = face, name, d
        self.loop = cfg.get("loop", "forward")
        self.intro = int(cfg.get("intro", 0))
        paths = sorted(glob.glob(f"{d}/full_imgs/*.png"))
        self.frames = [cv2.imread(p) for p in paths]
        self.n = len(self.frames)
        self.coords = pickle.load(open(f"{d}/coords.pkl", "rb"))
        mname = cfg.get("mask", "m90_90_u57")
        self.masks = [cv2.imread(p, cv2.IMREAD_GRAYSCALE) for p in sorted(glob.glob(f"{d}/mask_{mname}/*.png"))]
        self.mcoords = pickle.load(open(f"{d}/mask_coords_{mname}.pkl", "rb"))
        self.lms = np.load(f"{d}/landmarks.npy")
        kp = f"{d}/keep_hand.png"
        self.keep = cv2.imread(kp, cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0 if os.path.exists(kp) else None
        self.latents_cpu = torch.cat(torch.load(f"{d}/latents.pt"), 0)  # (N,8,32,32)
        assert len(self.masks) == self.n == len(self.latents_cpu), f"{d}: inconsistent avatar files"
        self.H, self.W = self.frames[0].shape[:2]
        # idle frames as RGBA bytes for the publisher (CPU)
        self.rgba = [np.ascontiguousarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGBA)) for f in self.frames]
        self.st = None
        self.latents = None

    def to_gpu(self):
        self.st = ClipStatics(self.frames, self.coords, self.masks, self.mcoords, self.keep, self.lms, DEV, V11)
        self.latents = self.latents_cpu.to(DEV, torch.float16).contiguous(memory_format=torch.channels_last)

    def index(self, p):
        p = max(0, int(p))
        if self.loop == "pingpong" and self.intro < self.n - 1:
            if p < self.intro:
                return p
            m = self.n - self.intro
            turn, res = divmod(p - self.intro, m)
            return self.intro + (res if turn % 2 == 0 else m - res - 1)
        return p % self.n


def load_faces():
    faces = {}
    for fj in sorted(glob.glob(f"{FACES_DIR}/*/face.json")):
        fd = os.path.dirname(fj)
        face = os.path.basename(fd)
        cfg = json.load(open(fj))
        clips = {}
        for name, c in cfg["expressions"].items():
            d = os.path.join(fd, c.get("dir", name))
            if not os.path.exists(f"{d}/PREP_DONE"):
                log.warning(json.dumps({"ev": "clip_missing", "face": face, "expr": name}))
                continue
            t = time.time()
            clips[name] = Clip(face, name, d, c)
            log.info(json.dumps({"ev": "clip_loaded", "face": face, "expr": name, "frames": clips[name].n,
                                 "s": round(time.time() - t, 1)}))
        if "neutral" in clips:
            faces[face] = clips
    return faces


class Job:
    __slots__ = ("epoch", "utt", "clip", "js", "kidx", "alpha", "wav16", "cidx", "t_submit", "done_cb", "out", "err",
                 "timing")

    def __init__(self, **kw):
        self.out = None
        self.err = None
        self.timing = {}
        for k, v in kw.items():
            setattr(self, k, v)


class Renderer:
    """Owns the GPU. load() runs in the worker thread (model load, torch.compile warm-up), then serves Jobs."""

    def __init__(self, compile_models=True):
        self.compile_models = compile_models
        self.ready = False
        self.error = None
        self.gpu_name = ""
        self.faces = {}
        self.q = queue.Queue()
        self.th = threading.Thread(target=self._main, name="gpu-worker", daemon=True)
        self.stats = {"jobs": 0, "frames": 0, "gpu_ms": 0.0}

    def start(self):
        self.th.start()

    def submit(self, job):
        self.q.put(job)

    # ------------------------------------------------------------------------------------------ loading
    def _load(self):
        t0 = time.time()
        torch.set_num_threads(int(os.environ.get("FACE_TORCH_THREADS", "2")))
        torch.backends.cudnn.benchmark = True
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        self.gpu_name = torch.cuda.get_device_name(0)
        from avatars.musetalk.models.vae import VAE
        from avatars.musetalk.models.unet import PositionalEncoding
        from transformers import AutoFeatureExtractor, WhisperModel
        from diffusers import UNet2DConditionModel
        vae = VAE(model_path=f"{LT}/models/sd-vae")
        self.vae = vae.vae.half().to(DEV).eval()
        self.vae_sf = vae.scaling_factor
        cfg = json.load(open(f"{LT}/models/musetalkV15/musetalk.json"))
        unet = UNet2DConditionModel(**cfg)
        unet.load_state_dict(torch.load(f"{LT}/models/musetalkV15/unet.pth", map_location="cpu"))
        self.unet = unet.half().to(DEV).eval()
        self.pe = PositionalEncoding(d_model=384).half().to(DEV)
        self.ts = torch.tensor([0], device=DEV)
        self.fe = AutoFeatureExtractor.from_pretrained(f"{LT}/models/whisper")
        self.whisper = WhisperModel.from_pretrained(f"{LT}/models/whisper").to(DEV, torch.float16).eval()
        self.whisper.requires_grad_(False)
        self.unet = self.unet.to(memory_format=torch.channels_last)
        self.vae = self.vae.to(memory_format=torch.channels_last)
        log.info(json.dumps({"ev": "models_loaded", "s": round(time.time() - t0, 1), "gpu": self.gpu_name}))
        self.unet_f = self.unet
        self.dec_f = self.vae.decoder
        if self.compile_models:
            os.makedirs(CACHE_DIR, exist_ok=True)
            if os.path.exists(MEGA):
                try:
                    torch.compiler.load_cache_artifacts(open(MEGA, "rb").read())
                    log.info(json.dumps({"ev": "compile_cache_loaded", "bytes": os.path.getsize(MEGA)}))
                except Exception as e:  # stale cache: just recompile
                    log.warning(json.dumps({"ev": "compile_cache_load_failed", "err": str(e)[:200]}))
            self.unet_f = torch.compile(self.unet, dynamic=False)
            self.dec_f = torch.compile(self.vae.decoder, dynamic=False)
        t1 = time.time()
        self.faces = load_faces()
        for clips in self.faces.values():
            for c in clips.values():
                c.to_gpu()
        log.info(json.dumps({"ev": "faces_loaded", "faces": {f: sorted(c) for f, c in self.faces.items()},
                             "s": round(time.time() - t1, 1)}))
        # warm-up: every batch shape through the whole pipeline (compiles on first call)
        t2 = time.time()
        clip = next(iter(next(iter(self.faces.values())).values()))
        for B in BATCHES:
            for _ in range(3):
                wav = (np.random.randn(16000 * 3) * 0.05).astype(np.float32)
                feats = self.features(wav)
                idx = np.clip(np.arange(B)[:, None] * 2 + 40 + np.arange(-4, 6)[None], 0, feats.shape[0] - 1)
                faces = self.lipsync(clip, list(range(B)), feats[torch.from_numpy(idx).to(DEV)].reshape(B, 50, 384))
                clip.st.compose(faces, list(range(B)), torch.ones(B, device=DEV)).cpu()
            torch.cuda.synchronize()
            log.info(json.dumps({"ev": "warm", "bs": B, "s": round(time.time() - t2, 1)}))
        for clips in self.faces.values():  # composite kernels for every clip geometry
            for c in clips.values():
                c.st.compose(torch.zeros(4, 3, 256, 256, device=DEV), [0, 1, 2, 3], torch.ones(4, device=DEV)).cpu()
        if self.compile_models:
            try:
                art = torch.compiler.save_cache_artifacts()
                if art is not None:
                    tmp = MEGA + ".tmp"
                    open(tmp, "wb").write(art[0])
                    os.replace(tmp, MEGA)
                    log.info(json.dumps({"ev": "compile_cache_saved", "bytes": len(art[0])}))
            except Exception as e:
                log.warning(json.dumps({"ev": "compile_cache_save_failed", "err": str(e)[:200]}))
        self.bench()
        log.info(json.dumps({"ev": "ready", "total_s": round(time.time() - t0, 1),
                             "vram_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2)}))

    def bench(self):
        clip = next(iter(next(iter(self.faces.values())).values()))
        res = {}
        for B in BATCHES:
            wav = (np.random.randn(16000 * 5) * 0.05).astype(np.float32)
            torch.cuda.synchronize()
            t = time.perf_counter()
            n = 10
            for i in range(n):
                feats = self.features(wav)
                idx = np.clip(np.arange(B)[:, None] * 2 + 40 + np.arange(-4, 6)[None], 0, feats.shape[0] - 1)
                faces = self.lipsync(clip, list(range(B)), feats[torch.from_numpy(idx).to(DEV)].reshape(B, 50, 384))
                clip.st.compose(faces, list(range(B)), torch.ones(B, device=DEV)).cpu()
            dt = (time.perf_counter() - t) / n
            res[f"bs{B}_ms"] = round(dt * 1000, 1)
            res[f"bs{B}_fps"] = round(B / dt, 1)
        wav = (np.random.randn(16000 * 5) * 0.05).astype(np.float32)
        torch.cuda.synchronize()
        t = time.perf_counter()
        for i in range(10):
            self.features(wav)
        torch.cuda.synchronize()
        res["whisper_ms"] = round((time.perf_counter() - t) / 10 * 1000, 1)
        self.bench_res = res
        log.info(json.dumps({"ev": "bench", **res}))

    # ------------------------------------------------------------------------------------------ inference
    @torch.no_grad()
    def features(self, wav16):
        """whisper-tiny encoder hidden states (T,5,384) fp16 on GPU at 50 Hz for a 16 kHz window (<= 30 s)."""
        try:  # log-mel on the GPU (torch STFT) keeps the CPU / GIL free for the publisher
            x = self.fe(wav16, return_tensors="pt", sampling_rate=16000, device="cuda").input_features
        except TypeError:
            x = self.fe(wav16, return_tensors="pt", sampling_rate=16000).input_features
        x = x.to(DEV, torch.float16)
        hs = self.whisper.encoder(x, output_hidden_states=True).hidden_states
        f = torch.stack(hs, dim=2)[0]  # (1500,5,384)
        n = int(np.ceil(len(wav16) / 320))
        return f[:n]

    @torch.no_grad()
    def lipsync(self, clip, kidx, chunks):
        """chunks: (B,50,384) fp16 GPU. Returns generated faces (B,3,256,256) float RGB 0..255."""
        B = len(kidx)
        Bp = 2 if B <= 2 else 4
        ki = list(kidx) + [kidx[-1]] * (Bp - B)
        if Bp > B:
            chunks = torch.cat([chunks, chunks[-1:].expand(Bp - B, -1, -1)], 0)
        lat = clip.latents[torch.as_tensor(ki, device=DEV)]
        a = self.pe(chunks.to(torch.float16))
        pred = self.unet_f(lat, self.ts, encoder_hidden_states=a).sample
        z = self.vae.post_quant_conv(pred / self.vae_sf)
        img = self.dec_f(z)
        img = (img[:B].float() / 2 + 0.5).clamp(0, 1) * 255.0
        return img

    @torch.no_grad()
    def run_job(self, job):
        t0 = time.perf_counter()
        ev = [torch.cuda.Event(enable_timing=True) for _ in range(5)]
        wav16 = resample_poly(job.wav16, 2, 3).astype(np.float32)  # job carries the 24 kHz window
        t1 = time.perf_counter()
        ev[0].record()
        feats = self.features(wav16)
        ci = torch.from_numpy(np.clip(job.cidx, 0, feats.shape[0] - 1)).to(DEV)
        chunks = feats[ci].reshape(len(job.kidx), 50, 384)
        ev[1].record()
        faces = self.lipsync(job.clip, job.kidx, chunks)
        ev[2].record()
        # causal temporal EMA on the generated 256 face (v11: 0.6 new / 0.4 previous), per utterance
        tema = V11.get("tema", 0)
        if tema:
            u = job.utt
            outs = []
            for b in range(faces.shape[0]):
                e = faces[b] if u.ema is None else tema * faces[b] + (1 - tema) * u.ema
                u.ema = e
                outs.append(e)
            faces = torch.stack(outs)
        alpha = torch.from_numpy(job.alpha.astype(np.float32)).to(DEV)
        tc0, cc0 = time.perf_counter(), time.thread_time()
        rgba = job.clip.st.compose(faces, job.kidx, alpha)
        tc1, cc1 = time.perf_counter(), time.thread_time()
        ev[3].record()
        out = rgba.cpu().numpy()
        ev[4].record()
        dt = time.perf_counter() - t0
        self.stats["jobs"] += 1
        self.stats["frames"] += len(job.kidx)
        self.stats["gpu_ms"] += dt * 1000
        job.timing.update(run_ms=round(dt * 1000, 1), resample_ms=round((t1 - t0) * 1000, 1),
                          feat_ms=round(ev[0].elapsed_time(ev[1]), 1), unet_vae_ms=round(ev[1].elapsed_time(ev[2]), 1),
                          comp_ms=round(ev[2].elapsed_time(ev[3]), 1), dl_ms=round(ev[3].elapsed_time(ev[4]), 1),
                          t_start=round(time.monotonic() - dt, 4), comp_wall_ms=round((tc1 - tc0) * 1000, 1),
                          comp_cpu_ms=round((cc1 - cc0) * 1000, 1))
        return out

    def _main(self):
        try:
            self._load()
            import gc
            gc.collect()
            gc.freeze()  # millions of long-lived torch/protobuf objects: keep them out of full GC passes (pauses)
            self.ready = True
        except Exception as e:
            log.exception("load failed")
            self.error = str(e)[:300]
            return
        while True:
            job = self.q.get()
            if job is None:
                return
            try:
                job.out = self.run_job(job)
            except Exception as e:
                log.exception("job failed")
                job.err = str(e)[:300]
            try:
                job.done_cb(job)
            except Exception:
                log.exception("done_cb failed")
