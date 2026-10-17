# Published (pre-encode dump) vs received (subscriber recording) mouth crops at the same instants.
# Usage: python codec_compare.py <engine_timeline.json> <sub.timeline.json> <sub.video.mp4> <dump_dir> <out.png>
import sys, os, json, glob, subprocess, shutil
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
tlp, rcp, vid, dd, outp = sys.argv[1:6]
ff = shutil.which("ffmpeg") or "ffmpeg"
tl = json.load(open(tlp))
rc = json.load(open(rcp))
rows = tl["rows"]
tp = np.array([r[0] for r in rows])
mouth = np.array([r[6] for r in rows])
vt = np.array(rc["video_t"])
vm = np.array(rc["video_mouth"], np.float64)
ok = ~np.isnan(vm)
DT = 0.01


def zoh(ts, vals, grid):
    idx = np.searchsorted(ts, grid, side="right") - 1
    out = np.asarray(vals, np.float64)[np.clip(idx, 0, len(vals) - 1)]
    out[idx < 0] = 0
    return out


def lag(a_t, a_v, b_t, b_v):
    ga = np.arange(a_t[0], a_t[-1], DT)
    gb = np.arange(b_t[0], b_t[-1], DT)
    A = zoh(a_t, a_v, ga); B = zoh(b_t, b_v, gb)
    A = A - A.mean(); B = B - B.mean()
    n = 1 << int(np.ceil(np.log2(len(A) + len(B))))
    c = np.fft.irfft(np.fft.rfft(B, n) * np.conj(np.fft.rfft(A, n)), n)
    k = int(np.argmax(c))
    if k > n // 2:
        k -= n
    return gb[0] - ga[0] + k * DT


lv = lag(tp, mouth, vt[ok], vm[ok])
W, H = 704, 896
raw = subprocess.run([ff, "-v", "error", "-i", vid, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
fr = np.frombuffer(raw, np.uint8)
nf = len(fr) // (W * H * 3)
fr = fr[:nf * W * H * 3].reshape(nf, H, W, 3)


def png(p):
    r = subprocess.run([ff, "-v", "error", "-i", p, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(r, np.uint8).reshape(H, W, 3)


def sharp(c):
    g = c.astype(np.float32).mean(2)
    lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var())


x1, y1, x2, y2 = 330, 420, 500, 540
tiles, stats = [], []
for p in sorted(glob.glob(os.path.join(dd, "*.png")))[2:30:3]:
    uid, j = os.path.basename(p)[:-4].rsplit("_", 1)
    j = int(j)
    t = next((r[0] for r in rows if r[1] == "u" and r[2] == uid and r[3] == j), None)
    if t is None:
        continue
    k = int(round((t + lv - rc["grid_t0"]) / 0.04)) + 1
    best, bk = 1e9, None
    pub = png(p)
    for kk in range(max(0, k - 3), min(nf, k + 4)):  # pick the closest received frame (grid jitter)
        d = np.abs(fr[kk, y1:y2, x1:x2].astype(np.float32) - pub[y1:y2, x1:x2]).mean()
        if d < best:
            best, bk = d, kk
    if bk is None:
        continue
    a, b = pub[y1:y2, x1:x2], fr[bk, y1:y2, x1:x2]
    mse = ((a.astype(np.float32) - b) ** 2).mean()
    stats.append((sharp(a), sharp(b), 10 * np.log10(255 ** 2 / max(mse, 1e-6))))
    tiles.append(np.concatenate([a, b], 1))
print(json.dumps({"video_clock_lag_s": round(lv, 3), "n": len(stats),
                  "sharpness_published": round(float(np.mean([s[0] for s in stats])), 1),
                  "sharpness_received": round(float(np.mean([s[1] for s in stats])), 1),
                  "psnr_mouth_db": round(float(np.mean([s[2] for s in stats])), 2)}))
img = np.concatenate(tiles[:8], 0)
p = subprocess.Popen([ff, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{img.shape[1]}x{img.shape[0]}",
                      "-i", "-", outp], stdin=subprocess.PIPE)
p.stdin.write(img.tobytes()); p.stdin.close(); p.wait()
