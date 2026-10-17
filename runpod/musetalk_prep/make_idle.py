# Retime a 24 fps Wan clip to 25 fps (same frames, 4% faster), optionally trim, and blend the tail back into
# frame 0 (head re-aligned by phase correlation + cross-fade) so a forward loop is seamless.
# MuseTalk itself plays the cycle ping-pong, which needs no seam fix; this makes the file usable either way.
# Usage: python make_idle.py <in_24fps.mp4> <out_idle.mp4> [last_frame=-1] [xfade_frames=0]
import sys, subprocess
import numpy as np
import cv2

src, dst = sys.argv[1], sys.argv[2]
last = int(sys.argv[3]) if len(sys.argv) > 3 else -1
K = int(sys.argv[4]) if len(sys.argv) > 4 else 0
cap = cv2.VideoCapture(src)
fr = []
while True:
    ok, f = cap.read()
    if not ok:
        break
    fr.append(f)
if last >= 0:
    fr = fr[:last + 1]
n = len(fr)
H, W = fr[0].shape[:2]
s = W / 704.0
x1, y1, x2, y2 = [int(v * s) for v in (300, 150, 560, 560)]
gray = lambda f: cv2.cvtColor(f[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY).astype(np.float32)
g0 = gray(fr[0])
if K > 0:
    f0 = fr[0].astype(np.float32)
    for j in range(K):
        i = n - K + j
        a = (j + 1) / (K + 1)
        (dx, dy), _ = cv2.phaseCorrelate(g0, gray(fr[i]))
        M = np.float32([[1, 0, -a * dx], [0, 1, -a * dy]])
        w = cv2.warpAffine(fr[i], M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).astype(np.float32)
        fr[i] = (w * (1 - a) + f0 * a).round().astype(np.uint8)
        print(f"tail frame {i}: shift ({dx:.2f},{dy:.2f}) blend {a:.2f}")
(dx, dy), _ = cv2.phaseCorrelate(g0, gray(fr[-1]))
print(f"last frame vs frame0 shift after fix: ({dx:.2f},{dy:.2f}), mae {np.abs(fr[-1].astype(np.float32) - fr[0]).mean():.2f}")
p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", "25",
                      "-i", "-", "-c:v", "libx264", "-crf", "12", "-preset", "slow", "-pix_fmt", "yuv420p", dst], stdin=subprocess.PIPE)
for f in fr:
    p.stdin.write(np.ascontiguousarray(f).tobytes())
p.stdin.close(); p.wait()
print("wrote", dst, n, "frames @25fps =", n / 25, "s")
