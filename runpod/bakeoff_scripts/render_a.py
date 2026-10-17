# Bake-off variant A: offline replica of the live engine look (MuseTalk 1.5 + v11 compositing, neutral avatar, forward
# loop, production idle-bypass cross-fade). Uses the phase-1 CPU reference compositor (blend2.compose, validated against
# the engine's GPU port) and an uncompiled fp16 UNet, so the timing here is NOT the production speed.
# Usage: python render_a.py <avatar_dir> <wav16k_padded> <wav_for_mux> <out.mp4>
import os, sys, time, json, subprocess
sys.path.insert(0, "/workspace/mt")
import numpy as np
import cv2
import torch
import mtlib  # chdir to LiveTalking, 4 threads
import blend2

adir, wav_p, mux_p, out = sys.argv[1:5]  # absolute paths (mtlib chdirs to LiveTalking on import)
V11 = dict(tema=0.6, lf=True, lipcolor=1.0, clarity=0.8, gloss=1.0, detail=1.0, sharpen=0.8, grain=True, erode=6, feather=10)
RAMP_HALF, PRE_ROLL, HOLD = 3, 2, 10  # production defaults (server.py)

t0 = time.time()
models = mtlib.load_models()
av = mtlib.load_avatar(adir, "m90_90_u57")
LMS = np.load(f"{adir}/landmarks.npy")
keep = cv2.imread(f"{adir}/keep_hand.png", cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0
N = len(av["latents"])
print(f"loaded {time.time()-t0:.1f}s, {N} base frames", flush=True)

wav = mtlib.read_wav16k(wav_p)
nf = int(np.ceil(len(wav) / 16000 * 25))
torch.cuda.synchronize(); tw = time.time()
feat = mtlib.whisper_feats(models[4], wav)
torch.cuda.synchronize(); whisper_s = time.time() - tw
faces = []
torch.cuda.synchronize(); ti = time.time()
for b in range(0, nf, 4):
    ids = list(range(b, min(nf, b + 4)))
    faces.extend(mtlib.infer(models, [av["latents"][i % N] for i in ids], [mtlib.frame_chunk(feat, i) for i in ids]))
torch.cuda.synchronize(); infer_s = time.time() - ti

# production idle-bypass weights
r = np.array([np.sqrt(np.mean(wav[i * 640:(i + 1) * 640] ** 2) + 1e-12) for i in range(nf)], np.float32)
act = r > max(0.01, 0.08 * float(np.percentile(r, 95)))
idx = np.where(act)[0]
for a, b in zip(idx[:-1], idx[1:]):
    if 1 < b - a <= 7:
        act[a:b] = True
d = np.zeros(nf, bool)
for sh in range(-HOLD, PRE_ROLL + 1):
    if sh >= 0:
        d[:nf - sh] |= act[sh:]
    else:
        d[-sh:] |= act[:nf + sh]
k = 2 * RAMP_HALF + 1
alpha = np.convolve(d.astype(np.float32), np.ones(k) / k, mode="same")

rng = np.random.default_rng(0)
T = {}
ema = None
frames = []
tc = time.time()
for i in range(nf):
    j = i % N
    face = faces[i].astype(np.float32)
    ema = face if ema is None else V11["tema"] * face + (1 - V11["tema"]) * ema
    g = blend2.compose(av["frames"][j], ema.round().astype(np.uint8), av["coords"][j], av["masks"][j], av["mcoords"][j],
                       keep, V11, T, rng, LMS[j])
    o = av["frames"][j]
    frames.append((g.astype(np.float32) * alpha[i] + o.astype(np.float32) * (1 - alpha[i])).round().astype(np.uint8))
compose_s = time.time() - tc

h, w = frames[0].shape[:2]
p = subprocess.Popen(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
                      "-r", "25", "-i", "-", "-i", mux_p, "-c:v", "libx264", "-crf", "14", "-preset", "slow", "-pix_fmt", "yuv420p",
                      "-c:a", "aac", "-b:a", "160k", "-shortest", out], stdin=subprocess.PIPE)
for f in frames:
    p.stdin.write(np.ascontiguousarray(f).tobytes())
p.stdin.close(); p.wait()
np.save(out + ".alpha.npy", alpha)
stats = dict(frames=nf, whisper_s=whisper_s, unet_vae_s=infer_s, unet_vae_fps_bs4_uncompiled=nf / infer_s,
             compose_cpu_ms_per_frame=compose_s / nf * 1000, speaking_frames=int((alpha > 0.5).sum()))
json.dump(stats, open(out + ".json", "w"), indent=1)
print(json.dumps(stats), flush=True)
