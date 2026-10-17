# Mouth crops around speech onsets/offsets of a recording (audio RMS on the mp4's own timeline).
# Usage: python onset_sheet.py <rec.mp4> <out_prefix> [x1,y1,x2,y2] [n_onsets]
import sys, subprocess, shutil, json
import numpy as np

src, outp = sys.argv[1], sys.argv[2]
x1, y1, x2, y2 = [int(v) for v in (sys.argv[3] if len(sys.argv) > 3 else "320,400,520,560").split(",")]
NON = int(sys.argv[4]) if len(sys.argv) > 4 else 2
ff = shutil.which("ffmpeg") or "ffmpeg"
a = np.frombuffer(subprocess.run([ff, "-v", "error", "-i", src, "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                                 capture_output=True).stdout, np.int16).astype(np.float32) / 32768
r = np.sqrt((a[: len(a) // 640 * 640].reshape(-1, 640) ** 2).mean(1))  # 40 ms bins = video frames
act = r > 0.02
on = [i for i in range(1, len(act)) if act[i] and not act[max(0, i - 12):i].any()]
off = [i for i in range(1, len(act) - 1) if act[i] and not act[i + 1:i + 13].any()]
W, H = 704, 896
raw = subprocess.run([ff, "-v", "error", "-i", src, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
fr = np.frombuffer(raw, np.uint8)
n = len(fr) // (W * H * 3)
fr = fr[: n * W * H * 3].reshape(n, H, W, 3)
rows = []
for kind, lst in (("on", on[:NON]), ("off", off[:NON])):
    for i in lst:
        ids = list(range(max(0, i - 8), min(n, i + 6)))
        tiles = [fr[k, y1:y2, x1:x2] for k in ids]
        rows.append(np.concatenate(tiles, 1))
        print(kind, "frame", i, "t", round(i * 0.04, 2), "tiles", ids[0], "..", ids[-1])
w = max(r.shape[1] for r in rows)
rows = [np.pad(r, ((0, 4), (0, w - r.shape[1]), (0, 0))) for r in rows]
img = np.concatenate(rows, 0)
p = subprocess.Popen([ff, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{img.shape[1]}x{img.shape[0]}",
                      "-i", "-", outp + ".jpg"], stdin=subprocess.PIPE)
p.stdin.write(img.tobytes())
p.stdin.close()
p.wait()
