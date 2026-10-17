# Compositing experiments for 王小姐: same MuseTalk faces, different blending. Writes videos, crops, timings.
# Usage: python blend_exp.py <avatar_dir> <wav> <out_dir> <variant,...|all> [--side baseline,best]
import os, sys, time, json, subprocess, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import cv2
import torch
import mtlib
import blend2

ap = argparse.ArgumentParser()
ap.add_argument("adir"); ap.add_argument("wav"); ap.add_argument("outd"); ap.add_argument("variants")
ap.add_argument("--side", default=None, help="a,b : write side-by-side a|b")
ap.add_argument("--crops", default="20,48,60,90,130,170,210,250")
args = ap.parse_args()
os.makedirs(args.outd, exist_ok=True)

from blend_exp_cfg import VARIANTS
names = list(VARIANTS) if args.variants == "all" else args.variants.split(",")

cache = os.path.join(args.outd, "faces.npz")
av_base = mtlib.load_avatar(args.adir, "m90_90")
N = len(av_base["latents"])
wav = mtlib.read_wav16k(args.wav)
pad = np.zeros(8000, np.float32)
wav = np.concatenate([pad, wav, pad])
nf = int(np.ceil(len(wav) / 16000 * 25))
if os.path.exists(cache):
    faces = np.load(cache)["faces"]
else:
    models = mtlib.load_models()
    feat = mtlib.whisper_feats(models[4], wav)
    faces = []
    for b in range(0, nf, 8):
        ids = list(range(b, min(nf, b + 8)))
        faces.extend(mtlib.infer(models, [av_base["latents"][mtlib.mirror_index(N, i)] for i in ids],
                                 [mtlib.frame_chunk(feat, i) for i in ids]))
    faces = np.stack(faces)
    np.savez(cache, faces=faces)
    del models
    torch.cuda.empty_cache()
keep = cv2.imread(os.path.join(args.adir, "keep_hand.png"), cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0
LMS = np.load(os.path.join(args.adir, "landmarks.npy"))
import soundfile as sf
tmpwav = os.path.join(args.outd, "audio16k.wav")
sf.write(tmpwav, wav, 16000)
import pickle, glob
masks_cache = {}


def masks(name):
    if name not in masks_cache:
        ms = [cv2.imread(p, cv2.IMREAD_GRAYSCALE) for p in sorted(glob.glob(f"{args.adir}/mask_{name}/*.png"))]
        mc = pickle.load(open(f"{args.adir}/mask_coords_{name}.pkl", "rb"))
        masks_cache[name] = (ms, mc)
    return masks_cache[name]


def write(path, frames):
    h, w = frames[0].shape[:2]
    p = subprocess.Popen(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
                          "-r", "25", "-i", "-", "-i", tmpwav, "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
                          "-c:a", "aac", "-shortest", path], stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(np.ascontiguousarray(f).tobytes())
    p.stdin.close(); p.wait()


crop_ids = [int(c) for c in args.crops.split(",")]
results = {}
outs = {}
rng = np.random.default_rng(0)
for name in names:
    mname, opts = VARIANTS[name]
    ms, mc = masks(mname)
    T = {}
    fr = []
    t0 = time.perf_counter()
    ema = None
    for i in range(nf):
        k = mtlib.mirror_index(N, i)
        face = faces[i]
        if opts.get("tema"):  # causal temporal EMA on the generated 256 face (anti-jitter), ~0.2 ms
            tt = time.perf_counter()
            ema = face.astype(np.float32) if ema is None else opts["tema"] * face + (1 - opts["tema"]) * ema
            face = ema.round().astype(np.uint8)
            T["tema"] = T.get("tema", 0) + time.perf_counter() - tt
        fr.append(blend2.compose(av_base["frames"][k], face, av_base["coords"][k], ms[k], mc[k], keep, opts, T, rng, LMS[k]))
    tot = time.perf_counter() - t0
    results[name] = {"ms_per_frame_total": round(tot / nf * 1000, 2), **{k: round(v / nf * 1000, 2) for k, v in T.items()},
                     "mask": mname, "opts": opts}
    print(name, json.dumps(results[name]), flush=True)
    write(os.path.join(args.outd, f"{name}.mp4"), fr)
    # zoomed crops (lower face, 2x) for chosen frames + base frame for reference
    tiles = []
    for c in crop_ids:
        t = fr[c][400:580, 330:560]
        t = cv2.resize(t, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        cv2.putText(t, f"{name} f{c}", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        tiles.append(t)
    rows = [np.concatenate(tiles[r:r + 4], 1) for r in range(0, len(tiles), 4)]
    cv2.imwrite(os.path.join(args.outd, f"{name}_crops.jpg"), np.concatenate(rows, 0), [cv2.IMWRITE_JPEG_QUALITY, 92])
    outs[name] = fr
# base reference crops
tiles = []
for c in crop_ids:
    t = av_base["frames"][mtlib.mirror_index(N, c)][400:580, 330:560]
    t = cv2.resize(t, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    cv2.putText(t, f"base f{c}", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    tiles.append(t)
rows = [np.concatenate(tiles[r:r + 4], 1) for r in range(0, len(tiles), 4)]
cv2.imwrite(os.path.join(args.outd, "base_crops.jpg"), np.concatenate(rows, 0), [cv2.IMWRITE_JPEG_QUALITY, 92])
if args.side:
    a, b = args.side.split(",")
    if a in outs and b in outs:
        sb = []
        for fa, fb in zip(outs[a], outs[b]):
            fa = fa.copy(); fb = fb.copy()
            cv2.putText(fa, "before", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (200, 200, 200), 2)
            cv2.putText(fb, "after", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (200, 200, 200), 2)
            sb.append(np.concatenate([fa, fb], 1))
        write(os.path.join(args.outd, f"side_{a}_vs_{b}.mp4"), sb)
json.dump(results, open(os.path.join(args.outd, "timing.json"), "w"), indent=1)
print("DONE")
