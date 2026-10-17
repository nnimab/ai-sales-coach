# Bake-off variant D: Ditto (antgroup/ditto-talkinghead) driven by one source image (or video) + a wav, with timing.
# Online mode feeds 200 ms HuBERT chunks (3,5,2) like stream_pipeline_online / inference.py; --pace realtime submits each
# chunk only when its audio would have arrived in real time (latency test), --pace fast as fast as possible (throughput).
# Per-stage wall times are recorded with thin wrappers; "frame ready" = end of putback (before the mp4 writer).
# Usage (from any cwd): python ditto_run.py --backend trt|pytorch --source img.png --audio a16k.wav --out out.mp4
#        [--online 1] [--pace realtime|fast] [--steps N] [--emo K] [--warmup 1] [--mux wav]
import os, sys, time, json, math, argparse, subprocess
DITTO = "/workspace/bakeoff/ditto"
sys.path.insert(0, DITTO)
os.chdir(DITTO)
import numpy as np
import librosa

ap = argparse.ArgumentParser()
ap.add_argument("--backend", default="trt")
ap.add_argument("--source", required=True)
ap.add_argument("--audio", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--online", type=int, default=1)
ap.add_argument("--pace", default="fast")
ap.add_argument("--steps", type=int, default=0, help="0 = cfg default (online 10, offline 50)")
ap.add_argument("--emo", default="", help="int 0-7 ('Angry','Disgust','Fear','Happy','Neutral','Sad','Surprise','Contempt'); empty = cfg default")
ap.add_argument("--warmup", type=int, default=1)
ap.add_argument("--mux", default="", help="audio file to mux into the final mp4 (default --audio)")
ap.add_argument("--crf", type=int, default=14)
args = ap.parse_args()

CK = f"{DITTO}/checkpoints"
if args.backend == "trt":
    data_root = f"{CK}/ditto_trt_Ampere_Plus"
    cfg = f"{CK}/ditto_cfg/v0.4_hubert_cfg_trt_online.pkl" if args.online else f"{CK}/ditto_cfg/v0.4_hubert_cfg_trt.pkl"
else:
    data_root = f"{CK}/ditto_pytorch"
    cfg = f"{CK}/ditto_cfg/v0.4_hubert_cfg_pytorch.pkl"

if args.online:
    from stream_pipeline_online import StreamSDK
else:
    from stream_pipeline_offline import StreamSDK
from core.atomic_components.writer import VideoWriterByImageIO

REC = {}


class Timed:
    """Delegating wrapper that records (start, duration) of each call."""
    def __init__(self, obj, name):
        object.__setattr__(self, "_o", obj)
        object.__setattr__(self, "_n", name)

    def __call__(self, *a, **k):
        t = time.perf_counter()
        r = self._o(*a, **k)
        REC.setdefault(self._n, []).append((t, time.perf_counter() - t))
        return r

    def __getattr__(self, k):
        return getattr(self._o, k)

    def __setattr__(self, k, v):
        setattr(self._o, k, v)


t_init = time.perf_counter()
sdk = StreamSDK(cfg, data_root)
init_s = time.perf_counter() - t_init
print(f"init {init_s:.1f}s backend={args.backend} online={args.online}", flush=True)
for n in ("audio2motion", "motion_stitch", "warp_f3d", "decode_f3d", "putback"):
    setattr(sdk, n, Timed(getattr(sdk, n), n))
sdk.wav2feat = Timed(sdk.wav2feat, "hubert_chunk")

audio_full, _ = librosa.load(args.audio, sr=16000)


def run_once(audio, out_path, pace):
    REC.clear()
    kw = {}
    if args.online:
        kw["online_mode"] = True
    if args.steps:
        kw["sampling_timesteps"] = args.steps
    if args.emo != "":
        kw["emo"] = int(args.emo)
    t = time.perf_counter()
    sdk.setup(args.source, out_path, **kw)
    setup_s = time.perf_counter() - t
    try:
        sdk.writer.close()
    except Exception:
        pass
    sdk.writer = VideoWriterByImageIO(sdk.tmp_output_path, crf=args.crf)
    num_f = math.ceil(len(audio) / 16000 * 25)
    sdk.setup_Nd(N_d=num_f, fade_in=-1, fade_out=-1, ctrl_info={})
    submits = []
    t0 = time.perf_counter()  # audio sample 0 "arrives" now
    if args.online:
        cs = (3, 5, 2)
        a = np.concatenate([np.zeros((cs[0] * 640,), np.float32), audio], 0)
        split_len = int(sum(cs) * 0.04 * 16000) + 80  # 6480
        dur = len(audio) / 16000
        for i in range(0, len(a), cs[1] * 640):
            ch = a[i:i + split_len]
            if len(ch) < split_len:
                ch = np.pad(ch, (0, split_len - len(ch)))
            if pace == "realtime":
                t_av = t0 + min(dur, max(0.0, (i + split_len - cs[0] * 640) / 16000))
                while time.perf_counter() < t_av:
                    time.sleep(min(0.002, max(0.0, t_av - time.perf_counter())))
            submits.append(time.perf_counter() - t0)
            sdk.run_chunk(ch, cs)
    else:
        th = time.perf_counter()
        feat = sdk.wav2feat.wav2feat(audio)
        REC["hubert_offline"] = [(th, time.perf_counter() - th)]
        sdk.audio2motion_queue.put(feat)
    t_close = time.perf_counter()
    sdk.close()
    t_end = time.perf_counter()
    ready = np.array([s + d for s, d in REC.get("putback", [])]) - t0
    n = len(ready)
    lat = ready - (np.arange(n) + 1) * 0.04  # how long after its audio "arrived" each frame is ready
    stats = {
        "setup_s(avatar_registration)": round(setup_s, 3),
        "frames": n, "audio_s": round(len(audio) / 16000, 3),
        "wall_s_first_audio_to_last_frame": round(float(ready[-1]), 3) if n else None,
        "throughput_fps(frames/wall from first chunk)": round(n / float(ready[-1]), 2) if n else None,
        "steady_fps(frames 25%..100%)": round((n - n // 4) / float(ready[-1] - ready[n // 4 - 1]), 2) if n > 8 else None,
        "first_frame_ready_s": round(float(ready[0]), 3) if n else None,
        "latency_s_p50": round(float(np.percentile(lat, 50)), 3) if n else None,
        "latency_s_p95": round(float(np.percentile(lat, 95)), 3) if n else None,
        "latency_s_max": round(float(lat.max()), 3) if n else None,
        "n_chunks": len(submits),
        "stage_ms_mean": {k: round(1000 * float(np.mean([d for _, d in v])), 2) for k, v in REC.items()},
        "stage_calls": {k: len(v) for k, v in REC.items()},
    }
    # GPU-busy estimate per frame (stages run in separate threads but share one GPU)
    per_frame = 0.0
    for k in ("motion_stitch", "warp_f3d", "decode_f3d"):
        if k in REC:
            per_frame += np.mean([d for _, d in REC[k]])
    a2m = REC.get("audio2motion", [])
    hub = REC.get("hubert_chunk", [])
    if n:
        per_frame += (sum(d for _, d in a2m) + sum(d for _, d in hub)) / n
    stats["sum_stage_ms_per_frame(serial upper bound)"] = round(per_frame * 1000, 2)
    return stats, lat


if args.warmup:
    w = audio_full[: 16000 * 3]
    s, _ = run_once(w, args.out + ".warmup.mp4", "fast")
    print("warmup", json.dumps(s), flush=True)
    for p in (args.out + ".warmup.mp4", args.out + ".warmup.mp4.tmp.mp4"):
        if os.path.exists(p):
            os.remove(p)

stats, lat = run_once(audio_full, args.out, args.pace)
stats.update({"backend": args.backend, "online": args.online, "pace": args.pace, "steps": args.steps or ("cfg10" if args.online else "cfg50"),
              "emo": args.emo or "cfg_default", "init_s": round(init_s, 1), "source": args.source})
import torch
stats["torch_vram_peak_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 2) if torch.cuda.is_available() else None
mux = args.mux or args.audio
FF = os.environ.get("FFMPEG", "ffmpeg")
subprocess.run([FF, "-nostdin", "-loglevel", "error", "-y", "-i", sdk.tmp_output_path, "-i", mux, "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", args.out], check=True)
os.remove(sdk.tmp_output_path)
np.save(args.out + ".latency.npy", lat)
json.dump(stats, open(args.out + ".json", "w"), indent=1)
print("RESULT", json.dumps(stats), flush=True)
