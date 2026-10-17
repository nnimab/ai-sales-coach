# Offline A/B of compositing settings on the engine's own pipeline (same faces, different tema / sharpening).
# Metrics on the mouth region: sharpness (Laplacian variance) and temporal jitter (2nd difference) during speech.
# Usage: python ab_blend.py <wav24k> <out_dir> [seconds=8]
import os, sys, json, time, subprocess, wave
sys.path.insert(0, "/workspace/face_engine")
os.environ.setdefault("FACE_COMPILE", "0")
import numpy as np
import torch
from scipy.signal import resample_poly
import renderer as R
from gpu_blend import ClipStatics, V11

wavp, outd = sys.argv[1], sys.argv[2]
SECS = float(sys.argv[3]) if len(sys.argv) > 3 else 8
os.makedirs(outd, exist_ok=True)
r = R.Renderer(compile_models=False)
r._load()
clip = r.faces["wang"]["neutral"]
with wave.open(wavp) as w:
    pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16)[: int(SECS * 24000)]
x = np.concatenate([np.zeros(9600, np.float32), pcm.astype(np.float32) / 32768, np.zeros(9600, np.float32)])
wav16 = resample_poly(x, 2, 3).astype(np.float32)
feats = r.features(wav16)
nf = int(len(pcm) / 960)
faces = []
for b in range(0, nf, 4):
    js = list(range(b, min(nf, b + 4)))
    c = np.array(js) * 2 + 20
    ci = torch.from_numpy(np.clip(c[:, None] + np.arange(-4, 6)[None], 0, feats.shape[0] - 1)).to(R.DEV)
    faces.append(r.lipsync(clip, [clip.index(j) for j in js], feats[ci].reshape(len(js), 50, 384)))
faces = torch.cat(faces)
rms = np.sqrt((pcm[: nf * 960].astype(np.float32).reshape(nf, 960) / 32768) ** 2).mean(1)
speech = np.where(rms > 0.02)[0]
speech = speech[(speech > 1) & (speech < nf - 2)]
VARS = {
    "A_v11": {},
    "B_tema80": {"tema": 0.8},
    "C_tema80_sharp": {"tema": 0.8, "sharpen": 1.2, "clarity": 1.1},
    "D_tema100_sharp": {"tema": 1.0, "sharpen": 1.2, "clarity": 1.1},
}
x1, y1, x2, y2 = 340, 430, 490, 540
res = {}
crops = {}
for name, ov in VARS.items():
    o = dict(V11); o.update(ov)
    st = clip.st if not ov else ClipStatics(clip.frames, clip.coords, clip.masks, clip.mcoords, clip.keep, clip.lms, R.DEV, o)
    ema, outs = None, []
    for b in range(0, nf, 4):
        fb = []
        for i in range(b, min(nf, b + 4)):
            e = faces[i] if ema is None else o["tema"] * faces[i] + (1 - o["tema"]) * ema
            ema = e
            fb.append(e)
        js = list(range(b, b + len(fb)))
        outs.append(st.compose(torch.stack(fb), [clip.index(j) for j in js], torch.ones(len(fb), device=R.DEV)).cpu().numpy())
    fr = np.concatenate(outs)[..., :3]
    m = fr[:, y1:y2, x1:x2].astype(np.float32)
    g = m.mean(3)
    lap = (g[:, 1:-1, 1:-1] * 4 - g[:, :-2, 1:-1] - g[:, 2:, 1:-1] - g[:, 1:-1, :-2] - g[:, 1:-1, 2:])
    sharp = lap.var(axis=(1, 2))
    d2 = np.abs(m[1:-1] - 0.5 * (m[:-2] + m[2:])).mean(axis=(1, 2, 3))
    res[name] = {"sharpness_speech": round(float(sharp[speech].mean()), 1),
                 "jitter_d2_speech": round(float(d2[speech - 1].mean()), 3)}
    crops[name] = fr[:, y1:y2, x1:x2]
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "704x896", "-r", "25",
                          "-i", "-", "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p", f"{outd}/{name}.mp4"],
                         stdin=subprocess.PIPE)
    p.stdin.write(np.ascontiguousarray(fr).tobytes()); p.stdin.close(); p.wait()
base = np.stack([clip.frames[clip.index(j)][y1:y2, x1:x2, ::-1] for j in range(nf)]).astype(np.float32)
gb = base.mean(3)
lapb = (gb[:, 1:-1, 1:-1] * 4 - gb[:, :-2, 1:-1] - gb[:, 2:, 1:-1] - gb[:, 1:-1, :-2] - gb[:, 1:-1, 2:])
res["base_clip"] = {"sharpness_speech": round(float(lapb.var(axis=(1, 2))[speech].mean()), 1)}
print(json.dumps(res, indent=1))
sel = speech[:: max(1, len(speech) // 8)][:8]
rows = [np.concatenate([crops[n][i] for n in VARS], 1) for i in sel]
img = np.concatenate(rows, 0)
p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{img.shape[1]}x{img.shape[0]}",
                      "-i", "-", f"{outd}/ab_crops.png"], stdin=subprocess.PIPE)
p.stdin.write(np.ascontiguousarray(img).tobytes()); p.stdin.close(); p.wait()
json.dump(res, open(f"{outd}/ab.json", "w"), indent=1)
