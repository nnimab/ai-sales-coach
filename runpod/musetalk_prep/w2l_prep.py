# wav2lip256 avatar prep (LiveTalking avatars/wav2lip/genavatar.py logic, Apache-2.0) without the watermark.
# Usage: python w2l_prep.py <png_dir> <out_avatar_dir>
import os, sys, glob, pickle
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import cv2
import mtlib  # cwd -> LiveTalking
from avatars.wav2lip import face_detection

src, adir = sys.argv[1], sys.argv[2]
os.makedirs(f"{adir}/face_imgs", exist_ok=True)
if not os.path.exists(f"{adir}/full_imgs"):
    os.symlink(src, f"{adir}/full_imgs")
frames = [cv2.imread(p) for p in sorted(glob.glob(f"{src}/*.png"))]
det = face_detection.FaceAlignment(face_detection.LandmarksType._2D, flip_input=False, device="cuda")
preds = []
for i in range(0, len(frames), 16):
    preds.extend(det.get_detections_for_batch(np.array(frames[i:i + 16])))
boxes = []
for r, im in zip(preds, frames):
    if r is None:
        r = [0, 0, im.shape[1], im.shape[0]]
    boxes.append([max(0, r[0]), max(0, r[1]), min(im.shape[1], r[2]), min(im.shape[0], r[3] + 10)])
boxes = np.array(boxes, np.float32)
for i in range(len(boxes)):  # T=5 smoothing as in LiveTalking
    w = boxes[len(boxes) - 5:] if i + 5 > len(boxes) else boxes[i:i + 5]
    boxes[i] = w.mean(0)
coords = []
for i, (b, f) in enumerate(zip(boxes, frames)):
    x1, y1, x2, y2 = [int(v) for v in b]
    cv2.imwrite(f"{adir}/face_imgs/{i:08d}.png", cv2.resize(f[y1:y2, x1:x2], (256, 256)))
    coords.append((y1, y2, x1, x2))
pickle.dump(coords, open(f"{adir}/coords.pkl", "wb"))
print("w2l avatar", adir, len(coords), "box0", coords[0])
