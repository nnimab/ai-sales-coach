# Drift check for an expression clip vs the neutral first frame: head (face box) and hand shifts per frame,
# plus an overlay of the static hand keep-mask on a few frames.  Usage: python clip_check.py <clip.mp4> <out.jpg>
import sys
import numpy as np
import cv2
sys.path.insert(0, "/workspace/mt")
src, outp = sys.argv[1], sys.argv[2]
cap = cv2.VideoCapture(src)
fr = []
while True:
    ok, f = cap.read()
    if not ok:
        break
    fr.append(f)
keep = cv2.imread("/workspace/avatars/wang_mt/keep_hand.png", cv2.IMREAD_GRAYSCALE)
g = lambda f, b: cv2.cvtColor(f[b[1]:b[3], b[0]:b[2]], cv2.COLOR_BGR2GRAY).astype(np.float32)
FACE = (290, 230, 540, 560)
HAND = (390, 400, 580, 700)
res = []
for i, f in enumerate(fr):
    (fx, fy), _ = cv2.phaseCorrelate(g(fr[0], FACE), g(f, FACE))
    (hx, hy), _ = cv2.phaseCorrelate(g(fr[0], HAND), g(f, HAND))
    res.append((fx, fy, hx, hy))
r = np.array(res)
print("frames", len(fr), "face shift max |dx|,|dy|", np.abs(r[:, :2]).max(0).round(1), "hand shift max", np.abs(r[:, 2:]).max(0).round(1))
for i in range(0, len(fr), 10):
    print(i, r[i].round(1).tolist())
tiles = []
for i in [0, len(fr) // 3, 2 * len(fr) // 3, len(fr) - 1]:
    v = fr[i].copy()
    cnt, _ = cv2.findContours((keep > 127).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(v, cnt, -1, (0, 255, 255), 1)
    tiles.append(v[350:720, 300:620])
cv2.imwrite(outp, np.concatenate(tiles, 1))
