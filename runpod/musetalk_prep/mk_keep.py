# Static "keep original pixels" mask for the hand on 王小姐's jaw (704x896 base clip coordinates, from frame 0).
# Usage: python mk_keep.py <ref_frame.png> <out_mask.png> [feather_px=5] [grow_px=0]
import sys
import numpy as np
import cv2

HAND = np.array([
    (398, 545), (420, 530), (440, 515), (460, 502), (480, 488), (497, 476), (512, 462), (525, 446), (533, 430),
    (538, 412), (545, 398), (556, 398), (566, 420), (574, 455), (577, 490), (568, 515), (552, 535), (530, 552),
    (510, 572), (495, 595), (485, 620), (480, 700), (395, 700), (392, 640), (393, 590), (395, 560)], np.int32)

ref = cv2.imread(sys.argv[1])
out = sys.argv[2]
feather = int(sys.argv[3]) if len(sys.argv) > 3 else 5
grow = int(sys.argv[4]) if len(sys.argv) > 4 else 0
H, W = ref.shape[:2]
sx, sy = W / 704.0, H / 896.0
poly = (HAND * [sx, sy]).round().astype(np.int32)
m = np.zeros((H, W), np.uint8)
cv2.fillPoly(m, [poly], 255)
if grow > 0:
    m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1)))
elif grow < 0:
    m = cv2.erode(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (-2 * grow + 1, -2 * grow + 1)))
if feather > 0:
    k = 2 * feather + 1
    m = cv2.GaussianBlur(m, (k, k), 0)
cv2.imwrite(out, m)
vis = ref.copy()
vis[..., 1] = np.maximum(vis[..., 1], (m * 0.5).astype(np.uint8))
cv2.polylines(vis, [poly], True, (0, 255, 255), 1)
cv2.imwrite(out.replace(".png", "_vis.png"), vis)
print("keep mask", out, "area px", int((m > 127).sum()))
