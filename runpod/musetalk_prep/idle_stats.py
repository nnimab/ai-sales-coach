# Inspect a Wan idle clip: motion vs frame 0 in mouth / hand / eye regions, loop seam, contact sheet.
# Usage: python idle_stats.py <clip.mp4> <out_prefix>
import sys, json
import numpy as np
import cv2

clip, outp = sys.argv[1], sys.argv[2]
cap = cv2.VideoCapture(clip)
fr = []
while True:
    ok, f = cap.read()
    if not ok:
        break
    fr.append(f.astype(np.float32))
n = len(fr)
H, W = fr[0].shape[:2]
s = W / 704.0
R = {  # regions in 704x896 coords (x1,y1,x2,y2)
    "mouth": (360, 450, 475, 500),
    "hand": (440, 470, 575, 600),
    "eyes": (310, 290, 540, 340),
    "head": (300, 150, 560, 560),
}
def reg(f, r):
    x1, y1, x2, y2 = [int(v * s) for v in r]
    return f[y1:y2, x1:x2]
stats = {k: [float(np.abs(reg(f, r) - reg(fr[0], r)).mean()) for f in fr] for k, r in R.items()}
# global translation of the head vs frame 0 via phase correlation
g0 = cv2.cvtColor(reg(fr[0], R["head"]).astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
shift = []
for f in fr:
    g = cv2.cvtColor(reg(f, R["head"]).astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
    (dx, dy), _ = cv2.phaseCorrelate(g0, g)
    shift.append((round(dx, 2), round(dy, 2)))
eye_d1 = [float(np.abs(reg(fr[i], R["eyes"]) - reg(fr[i - 1], R["eyes"])).mean()) for i in range(1, n)]
out = {"frames": n, "size": [W, H],
       "max_vs_f0": {k: round(max(v), 2) for k, v in stats.items()},
       "last_vs_f0": {k: round(v[-1], 2) for k, v in stats.items()},
       "max_shift_px": [max(abs(a) for a, b in shift), max(abs(b) for a, b in shift)],
       "last_shift_px": shift[-1],
       "blink_candidates": [i + 1 for i, d in enumerate(eye_d1) if d > np.median(eye_d1) * 4][:20],
       "mouth_curve": [round(v, 1) for v in stats["mouth"][::5]],
       "hand_curve": [round(v, 1) for v in stats["hand"][::5]],
       "eyes_curve": [round(v, 1) for v in stats["eyes"][::5]],
       "shift_curve": shift[::10]}
json.dump(out, open(outp + "_stats.json", "w"), indent=1)
print(json.dumps(out))
idx = np.linspace(0, n - 1, 12).round().astype(int)
tiles = [cv2.resize(fr[i].astype(np.uint8), (W // 2, H // 2)) for i in idx]
for t, i in zip(tiles, idx):
    cv2.putText(t, str(i), (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
rows = [np.concatenate(tiles[r * 6:(r + 1) * 6], 1) for r in range(2)]
cv2.imwrite(outp + "_sheet.jpg", np.concatenate(rows, 0), [cv2.IMWRITE_JPEG_QUALITY, 88])
# face close-up sheet (mouth/hand area) every ~10 frames
x1, y1, x2, y2 = [int(v * s) for v in (330, 360, 600, 620)]
idx2 = np.linspace(0, n - 1, 12).round().astype(int)
t2 = [fr[i][y1:y2, x1:x2].astype(np.uint8).copy() for i in idx2]
for t, i in zip(t2, idx2):
    cv2.putText(t, str(i), (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
rows = [np.concatenate(t2[r * 6:(r + 1) * 6], 1) for r in range(2)]
cv2.imwrite(outp + "_mouth_sheet.jpg", np.concatenate(rows, 0), [cv2.IMWRITE_JPEG_QUALITY, 90])
