# Check the torch port of the v11 compositing (gpu_blend.ClipStatics.compose) against phase-1 blend2.compose.
# Usage: python blend_check.py <avatar_dir> <faces.npz> <out.jpg> [device=cpu] [nframes=24]
import os, sys, time, glob, pickle
sys.path.insert(0, "/workspace/face_engine")
sys.path.insert(0, "/workspace/mt")
import numpy as np
import cv2
import torch
import mtlib  # noqa: F401  (cwd -> LiveTalking, thread limits)
import blend2
from gpu_blend import ClipStatics, V11

adir, fz, outp = sys.argv[1], sys.argv[2], sys.argv[3]
dev = torch.device(sys.argv[4] if len(sys.argv) > 4 else "cpu")
NF = int(sys.argv[5]) if len(sys.argv) > 5 else 24
frames = [cv2.imread(p) for p in sorted(glob.glob(f"{adir}/full_imgs/*.png"))]
coords = pickle.load(open(f"{adir}/coords.pkl", "rb"))
masks = [cv2.imread(p, cv2.IMREAD_GRAYSCALE) for p in sorted(glob.glob(f"{adir}/mask_m90_90_u57/*.png"))]
mcoords = pickle.load(open(f"{adir}/mask_coords_m90_90_u57.pkl", "rb"))
keep = cv2.imread(f"{adir}/keep_hand.png", cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0
lms = np.load(f"{adir}/landmarks.npy")
faces = np.load(fz)["faces"]
N = len(frames)
opts = {k: v for k, v in V11.items() if k not in ("tema", "grain")}  # deterministic comparison
t = time.time()
st = ClipStatics(frames, coords, masks, mcoords, keep, lms, dev, opts)
print(f"statics {time.time()-t:.1f}s crop {st.Hc}x{st.Wc} bbox {st.bbox}", flush=True)
ids = list(range(40, 40 + NF))  # speaking part of the phase-1 gemini render
maes, mx, tiles = [], [], []
tc = tg = 0.0
for b0 in range(0, NF, 4):
    js = ids[b0:b0 + 4]
    ks = [mtlib.mirror_index(N, j) for j in js]
    t1 = time.perf_counter()
    cpu = [blend2.compose(frames[k], faces[j], coords[k], masks[k], mcoords[k], keep, opts, {}, None, lms[k]) for j, k in zip(js, ks)]
    tc += time.perf_counter() - t1
    f = torch.from_numpy(np.stack([faces[j][..., ::-1] for j in js]).copy()).permute(0, 3, 1, 2).float().to(dev)
    if dev.type == "cuda":
        torch.cuda.synchronize()
    t1 = time.perf_counter()
    g = st.compose(f, ks, None)
    if dev.type == "cuda":
        torch.cuda.synchronize()
    tg += time.perf_counter() - t1
    g = g.cpu().numpy()[..., :3][..., ::-1]
    xs, ys, xe, ye = st.mcrop
    for c, gg, k in zip(cpu, g, ks):
        d = np.abs(c.astype(np.float32) - gg.astype(np.float32))[ys:ye, xs:xe]
        maes.append(d.mean())
        mx.append(np.percentile(d, 99.9))
        if len(tiles) < 4:
            crop = lambda im: cv2.resize(im[400:580, 330:560], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            dd = np.clip(np.abs(c.astype(np.int16) - gg.astype(np.int16)) * 8, 0, 255).astype(np.uint8)
            tiles.append(np.concatenate([crop(frames[k]), crop(c), crop(np.ascontiguousarray(gg)), crop(dd)], 1))
print(f"MAE crop mean {np.mean(maes):.3f} max-frame {np.max(maes):.3f}; p99.9 absdiff mean {np.mean(mx):.1f}", flush=True)
print(f"cpu blend2 {tc/NF*1000:.1f} ms/frame, torch[{dev}] {tg/NF*1000:.1f} ms/frame", flush=True)
cv2.imwrite(outp, np.concatenate(tiles, 0), [cv2.IMWRITE_JPEG_QUALITY, 90])
