# GFPGAN v1.4 on top of the best compositing variant: timing + identity check.
# Usage: python gfp_exp.py <avatar_dir> <blend_out_dir> <variant> <strength,...>
import os, sys, json, time, glob, pickle, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import cv2
import torch
import mtlib, blend2, gfp

adir, bdir, variant = sys.argv[1], sys.argv[2], sys.argv[3]
strengths = [float(s) for s in sys.argv[4].split(",")]
import blend_exp_cfg as cfg  # VARIANTS copy
mname, opts = cfg.VARIANTS[variant]
av = mtlib.load_avatar(adir, "m90_90")
N = len(av["latents"])
faces = np.load(f"{bdir}/faces.npz")["faces"]
nf = len(faces)
ms = [cv2.imread(p, cv2.IMREAD_GRAYSCALE) for p in sorted(glob.glob(f"{adir}/mask_{mname}/*.png"))]
mc = pickle.load(open(f"{adir}/mask_coords_{mname}.pkl", "rb"))
keep = cv2.imread(f"{adir}/keep_hand.png", cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0
lms = np.load(f"{adir}/landmarks.npy")
H, W = av["frames"][0].shape[:2]
rng = np.random.default_rng(0)
comp, fullm = [], []
for i in range(nf):
    k = mtlib.mirror_index(N, i)
    comp.append(blend2.compose(av["frames"][k], faces[i], av["coords"][k], ms[k], mc[k], keep, opts, None, rng, lms[k]))
    m = ms[k].astype(np.float32) / 255.0
    m = cv2.GaussianBlur(cv2.erode((m > 0.5).astype(np.uint8), np.ones((13, 13), np.uint8)).astype(np.float32), (0, 0), 10)
    full = np.zeros((H, W), np.float32)
    xs, ys, xe, ye = mc[k]
    sx1, sy1, sx2, sy2 = max(0, xs), max(0, ys), min(W, xe), min(H, ye)
    full[sy1:sy2, sx1:sx2] = m[sy1 - ys:sy2 - ys, sx1 - xs:sx2 - xs]
    fullm.append(full * (1 - keep))
res = {}
for half in [True, False]:
    net = gfp.load_gfpgan(half=half)
    for bs in [1, 4, 8]:
        T = {}
        x = comp[:bs]
        for _ in range(2):
            gfp.restore(net, x, [lms[mtlib.mirror_index(N, i)] for i in range(bs)], fullm[:bs], 0.5, {})
        T = {}
        reps = 5
        for _ in range(reps):
            gfp.restore(net, x, [lms[mtlib.mirror_index(N, i)] for i in range(bs)], fullm[:bs], 0.5, T)
        res[f"{'fp16' if half else 'fp32'}_bs{bs}"] = {k: round(v / reps / bs * 1000, 2) for k, v in T.items()}
        print("gfpgan", "fp16" if half else "fp32", "bs", bs, res[f"{'fp16' if half else 'fp32'}_bs{bs}"], "ms/frame", flush=True)
    if half:
        net16 = net
    else:
        del net
net = net16
tmpwav = f"{bdir}/audio16k.wav"
for s in strengths:
    out = []
    for b in range(0, nf, 8):
        ids = list(range(b, min(nf, b + 8)))
        out.extend(gfp.restore(net, [comp[i] for i in ids], [lms[mtlib.mirror_index(N, i)] for i in ids], [fullm[i] for i in ids], s))
    path = f"{bdir}/{variant}_gfpgan{int(s*100)}.mp4"
    p = subprocess.Popen(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", "25",
                          "-i", "-", "-i", tmpwav, "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path],
                         stdin=subprocess.PIPE)
    for f in out:
        p.stdin.write(np.ascontiguousarray(f).tobytes())
    p.stdin.close(); p.wait()
    tiles = []
    for c in [20, 48, 60, 90, 130, 170, 210, 250]:
        t = cv2.resize(out[c][400:580, 330:560], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        cv2.putText(t, f"gfp{int(s*100)} f{c}", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        tiles.append(t)
    rows = [np.concatenate(tiles[r:r + 4], 1) for r in range(0, len(tiles), 4)]
    cv2.imwrite(f"{bdir}/{variant}_gfpgan{int(s*100)}_crops.jpg", np.concatenate(rows, 0), [cv2.IMWRITE_JPEG_QUALITY, 92])
    # whole-face crop to check identity (eyes etc. are untouched by design; lower face only)
    t = np.concatenate([comp[48][230:600, 260:600], out[48][230:600, 260:600]], 1)
    cv2.imwrite(f"{bdir}/{variant}_gfpgan{int(s*100)}_face48.jpg", t, [cv2.IMWRITE_JPEG_QUALITY, 92])
json.dump(res, open(f"{bdir}/gfpgan_timing.json", "w"), indent=1)
print("DONE")
