# MuseTalk 1.5 avatar preparation (adapted from LiveTalking avatars/musetalk/genavatar.py, Apache-2.0).
# Differences: face_alignment landmarks (no dlib), bbox smoothing/freezing, no watermark, per-variant masks.
# Usage: python mt_prep.py <video.mp4|png_dir> <avatar_dir> [--bbox fixed|smooth|raw] [--bbox_shift 0] [--extra_margin 10]
#        [--max_frames N]
import argparse, os, sys, glob, pickle, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import cv2
import torch
import mtlib  # sets cwd to LiveTalking
from avatars.musetalk.utils.face_parsing import FaceParsing
from avatars.musetalk.utils.blending import get_image_prepare_material

ap = argparse.ArgumentParser()
ap.add_argument("src")
ap.add_argument("adir")
ap.add_argument("--bbox", default="smooth")
ap.add_argument("--bbox_shift", type=int, default=0)
ap.add_argument("--extra_margin", type=int, default=10)
ap.add_argument("--max_frames", type=int, default=100000)
ap.add_argument("--cheek", default="90,90")
args = ap.parse_args()

t0 = time.time()
os.makedirs(f"{args.adir}/full_imgs", exist_ok=True)
frames = []
if os.path.isdir(args.src):
    for p in sorted(glob.glob(f"{args.src}/*.png")) + sorted(glob.glob(f"{args.src}/*.jpg")):
        frames.append(cv2.imread(p))
else:
    cap = cv2.VideoCapture(args.src)
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
frames = frames[:args.max_frames]
print("frames", len(frames), frames[0].shape, flush=True)

import face_alignment
fa = face_alignment.FaceAlignment(face_alignment.LandmarksType.TWO_D, flip_input=False, device="cuda")
lms = []
for i, f in enumerate(frames):
    p = fa.get_landmarks_from_image(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
    if not p:
        lms.append(lms[-1] if lms else None)
        print("no face in frame", i)
        continue
    # choose the largest face
    p = max(p, key=lambda q: (q[:, 0].max() - q[:, 0].min()) * (q[:, 1].max() - q[:, 1].min()))
    lms.append(p.astype(np.float32))
lms = np.stack(lms)
np.save(f"{args.adir}/landmarks.npy", lms)

# LiveTalking/MuseTalk bbox rule from 68 landmarks
boxes = []
for lm in lms:
    half = lm[29].copy()
    half[1] += args.bbox_shift
    dist = lm[:, 1].max() - half[1]
    up = max(0, half[1] - dist)
    boxes.append([lm[:, 0].min(), up, lm[:, 0].max(), lm[:, 1].max()])
boxes = np.array(boxes, np.float32)
raw_boxes = boxes.copy()
if args.bbox == "fixed":
    boxes[:] = np.median(boxes, 0)
elif args.bbox == "smooth":
    k = 9
    pad = np.pad(boxes, ((k // 2, k // 2), (0, 0)), mode="edge")
    boxes = np.stack([pad[i:i + k].mean(0) for i in range(len(boxes))])
H, W = frames[0].shape[:2]
coords = []
for b in boxes:
    x1, y1, x2, y2 = [int(round(v)) for v in b]
    y2 = min(y2 + args.extra_margin, H)
    coords.append([max(0, x1), max(0, y1), min(W, x2), y2])
print("bbox jitter (raw, px std):", raw_boxes.std(0).round(2).tolist(), "first coords", coords[0], flush=True)

del fa
torch.cuda.empty_cache()
vae = mtlib.load_vae()
latents = []
for (x1, y1, x2, y2), f in zip(coords, frames):
    crop = cv2.resize(f[y1:y2, x1:x2], (256, 256), interpolation=cv2.INTER_LANCZOS4)
    latents.append(vae.get_latents_for_unet(crop).cpu())
torch.save(latents, f"{args.adir}/latents.pt")

for i, f in enumerate(frames):
    cv2.imwrite(f"{args.adir}/full_imgs/{i:08d}.png", f)
pickle.dump(coords, open(f"{args.adir}/coords.pkl", "wb"))

lw, rw = [int(v) for v in args.cheek.split(",")]
name = f"m{lw}_{rw}"
fp = FaceParsing(left_cheek_width=lw, right_cheek_width=rw)
os.makedirs(f"{args.adir}/mask_{name}", exist_ok=True)
mcoords = []
for i, f in enumerate(frames):
    m, cb = get_image_prepare_material(f, coords[i], fp=fp, mode="jaw")
    cv2.imwrite(f"{args.adir}/mask_{name}/{i:08d}.png", m)
    mcoords.append(cb)
pickle.dump(mcoords, open(f"{args.adir}/mask_coords_{name}.pkl", "wb"))

# debug overlay of frame 0
d = frames[0].copy()
for x, y in lms[0]:
    cv2.circle(d, (int(x), int(y)), 2, (0, 255, 0), -1)
x1, y1, x2, y2 = coords[0]
cv2.rectangle(d, (x1, y1), (x2, y2), (0, 0, 255), 2)
m = np.zeros(d.shape[:2], np.uint8)
xs, ys, xe, ye = mcoords[0]
mm = mtlib.crop_pad(np.zeros((H, W), np.uint8), mcoords[0])
full = np.zeros((H, W), np.uint8)
sx1, sy1, sx2, sy2 = max(0, xs), max(0, ys), min(W, xe), min(H, ye)
mk = cv2.imread(f"{args.adir}/mask_{name}/00000000.png", cv2.IMREAD_GRAYSCALE)
full[sy1:sy2, sx1:sx2] = mk[sy1 - ys:sy2 - ys, sx1 - xs:sx2 - xs]
d[..., 2] = np.maximum(d[..., 2], (full * 0.6).astype(np.uint8))
cv2.imwrite(f"{args.adir}/debug_f0.png", d)
json.dump({"src": args.src, "n": len(frames), "bbox_mode": args.bbox, "bbox_shift": args.bbox_shift,
           "extra_margin": args.extra_margin, "prep_s": time.time() - t0}, open(f"{args.adir}/info.json", "w"))
open(f"{args.adir}/PREP_DONE", "w").write("ok")
print(f"done in {time.time()-t0:.0f}s", flush=True)
