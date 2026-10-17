# Offline MuseTalk 1.5 render of a prepared avatar driven by a wav, with timing.
# Usage: python mt_render.py <avatar_dir> <wav> <out.mp4> [--mask m90_90] [--keep keep.png] [--bs 8] [--start 0]
import argparse, os, sys, time, json, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import cv2
import torch
import mtlib

ap = argparse.ArgumentParser()
ap.add_argument("adir")
ap.add_argument("wav")
ap.add_argument("out")
ap.add_argument("--mask", default="m90_90")
ap.add_argument("--keep", default=None, help="full-frame grayscale png; white = always keep original pixels")
ap.add_argument("--bs", type=int, default=8)
ap.add_argument("--start", type=int, default=0, help="avatar frame index offset")
ap.add_argument("--pad_s", type=float, default=0.5, help="silence before/after speech (s)")
ap.add_argument("--side", action="store_true", help="also write side-by-side original|generated")
ap.add_argument("--hand_eval", default=None, help="hand mask png used only for metrics")
ap.add_argument("--idle_bypass", action="store_true",
                help="show original base frames when the audio is silent (energy VAD), cross-fade 3 frames at edges")
args = ap.parse_args()

t0 = time.time()
models = mtlib.load_models()
av = mtlib.load_avatar(args.adir, args.mask)
keep = None
if args.keep:
    keep = cv2.imread(args.keep, cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0
print(f"loaded in {time.time()-t0:.1f}s, avatar frames {len(av['frames'])}", flush=True)

wav = mtlib.read_wav16k(args.wav)
pad = np.zeros(int(args.pad_s * 16000), np.float32)
wav = np.concatenate([pad, wav, pad])
assert len(wav) < 30 * 16000, "keep test audio < 30 s"
tw = time.time()
feat = mtlib.whisper_feats(models[4], wav)
torch.cuda.synchronize()
whisper_s = time.time() - tw
nf = int(np.ceil(len(wav) / 16000 * 25))
N = len(av["latents"])
out_frames = []
t_inf = t_paste = 0.0
for b in range(0, nf, args.bs):
    ids = list(range(b, min(nf, b + args.bs)))
    aidx = [mtlib.mirror_index(N, i + args.start) for i in ids]
    lat = [av["latents"][k] for k in aidx]
    ch = [mtlib.frame_chunk(feat, i) for i in ids]
    torch.cuda.synchronize(); ti = time.time()
    faces = mtlib.infer(models, lat, ch)
    torch.cuda.synchronize(); t_inf += time.time() - ti
    tp = time.time()
    for k, face in zip(aidx, faces):
        fr = mtlib.paste_back(av["frames"][k], face, av["coords"][k], av["masks"][k], av["mcoords"][k], keep)
        if args.side:
            fr = np.concatenate([av["frames"][k], fr], 1)
        out_frames.append(fr)
    t_paste += time.time() - tp

if args.idle_bypass:
    # per-frame speech activity from 40 ms RMS; bridge pauses < 280 ms; 3-frame ramps in/out
    rms = np.array([np.sqrt(np.mean(wav[i * 640:(i + 1) * 640] ** 2) + 1e-12) for i in range(nf)])
    act = rms > max(0.01, 0.08 * np.percentile(rms, 95))
    idx = np.where(act)[0]
    for a, b in zip(idx[:-1], idx[1:]):
        if 1 < b - a <= 7:
            act[a:b] = True
    act_d = (np.convolve(act.astype(np.float32), np.ones(5), mode="same") > 0).astype(np.float32)  # dilate 2 frames
    alpha = np.convolve(np.pad(act_d, 2, mode="edge"), np.ones(5) / 5, mode="valid").clip(0, 1)  # 5-frame ramps
    for i in range(nf):
        k = mtlib.mirror_index(N, i + args.start)
        o = av["frames"][k]
        g = out_frames[i][:, o.shape[1]:] if args.side else out_frames[i]
        mixed = (g.astype(np.float32) * alpha[i] + o.astype(np.float32) * (1 - alpha[i])).round().astype(np.uint8)
        out_frames[i] = np.concatenate([o, mixed], 1) if args.side else mixed
    print("idle_bypass: speaking frames", int((alpha > 0.5).sum()), "/", nf, flush=True)

metrics = {}
if args.hand_eval or args.keep:
    hm = cv2.imread(args.hand_eval or args.keep, cv2.IMREAD_GRAYSCALE) > 127
    W0 = av["frames"][0].shape[1]
    G = [f[:, W0:] if args.side else f for f in out_frames]
    ids = [mtlib.mirror_index(N, i + args.start) for i in range(nf)]
    # region where MuseTalk pixels are blended in (any frame)
    R = np.zeros(hm.shape, bool)
    for k in set(ids):
        x = np.zeros(hm.shape, np.uint8)
        xs, ys, xe, ye = av["mcoords"][k]
        H0 = hm.shape[0]
        sx1, sy1, sx2, sy2 = max(0, xs), max(0, ys), min(W0, xe), min(H0, ye)
        x[sy1:sy2, sx1:sx2] = av["masks"][k][sy1 - ys:sy2 - ys, sx1 - xs:sx2 - xs]
        R |= x > 25
    face_r = R & ~hm
    hand_r = R & hm
    g = lambda t: G[t].astype(np.float32)
    o = lambda t: av["frames"][ids[t]].astype(np.float32)
    npad = int(args.pad_s * 25)
    sp = range(npad + 1, nf - npad - 1)
    metrics = {
        "hand_px_in_blend_region": int(hand_r.sum()),
        "hand_mae_vs_orig": float(np.mean([np.abs(g(t) - o(t))[hand_r].mean() for t in range(nf)])) if hand_r.any() else 0.0,
        "speech_d2_gen": float(np.mean([np.abs(g(t) - 0.5 * (g(t - 1) + g(t + 1)))[face_r].mean() for t in sp])),
        "speech_d2_orig": float(np.mean([np.abs(o(t) - 0.5 * (o(t - 1) + o(t + 1)))[face_r].mean() for t in sp])),
        "idle_d1_gen": float(np.mean([np.abs(g(t) - g(t - 1))[face_r].mean() for t in range(1, npad)])),
        "idle_d1_orig": float(np.mean([np.abs(o(t) - o(t - 1))[face_r].mean() for t in range(1, npad)])),
        "hand_d2_gen": float(np.mean([np.abs(g(t) - 0.5 * (g(t - 1) + g(t + 1)))[hand_r].mean() for t in sp])) if hand_r.any() else 0.0,
    }
    print("metrics", json.dumps(metrics), flush=True)

h, w = out_frames[0].shape[:2]
tmp = args.out + ".tmp.wav"
import soundfile as sf
sf.write(tmp, wav, 16000)
cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", "25", "-i", "-",
       "-i", tmp, "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
       "-shortest", args.out]
p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
for f in out_frames:
    p.stdin.write(np.ascontiguousarray(f).tobytes())
p.stdin.close(); p.wait()
os.remove(tmp)
stats = {"frames": nf, "bs": args.bs, "whisper_s": whisper_s, "unet_vae_s": t_inf, "paste_s": t_paste,
         "unet_vae_fps": nf / t_inf, "paste_fps": nf / t_paste, "end_to_end_fps": nf / (t_inf + t_paste + whisper_s),
         "vram_peak_gb": torch.cuda.max_memory_allocated() / 1e9, "mask": args.mask, "keep": args.keep, **metrics}
json.dump(stats, open(args.out + ".json", "w"), indent=1)
print(json.dumps(stats), flush=True)
