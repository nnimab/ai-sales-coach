# How much future audio does MuseTalk 1.5 need per frame?
# Streaming emulation: for target frame t (start time T=40t ms) whisper only sees audio [T-L, T+X].
# Compare the generated mouth with (a) the same left context L but a long lookahead (2 s) -> isolates lookahead,
# and (b) the whole-utterance features -> shows the left-context effect.
# Usage: python mt_lookahead.py <avatar_dir> <wav> [out.json]
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import torch
import mtlib

adir, wavp = sys.argv[1], sys.argv[2]
outp = sys.argv[3] if len(sys.argv) > 3 else adir + "/lookahead.json"
models = mtlib.load_models()
ap = models[4]
av = mtlib.load_avatar(adir)
N = len(av["latents"])
wav = mtlib.read_wav16k(wavp)
pad = np.zeros(8000, np.float32)
wav = np.concatenate([pad, wav, pad])
nf = int(len(wav) / 16000 * 25)
rms = np.array([np.sqrt(np.mean(wav[i * 640:(i + 1) * 640] ** 2)) for i in range(nf)])
targets = [t for t in range(int(0.6 * 25), nf - int(2.2 * 25), 3) if rms[t] > 0.1 * rms.max()]
print("targets", len(targets), flush=True)


def face_for(feat, c, frame):
    idx = np.clip(np.arange(c - 4, c + 6), 0, len(feat) - 1)
    ch = feat[idx].reshape(-1, 384)
    lat = [av["latents"][mtlib.mirror_index(N, frame)]]
    return mtlib.infer(models, lat, [ch])[0].astype(np.float32), ch.astype(np.float32)


def stream_face(t, L, X):
    t_s = t * 640
    s0 = max(0, t_s - int(L * 16))
    s1 = min(len(wav), t_s + int(X * 16))
    f = ap.audio2feat(wav[s0:s1])
    return face_for(f, (t_s - s0) // 320, t)


def mae(a, b):
    return float(np.abs(a[128:] - b[128:]).mean())  # lower half of the 256 face = mouth/jaw


full = ap.audio2feat(wav)
ref_full = {t: face_for(full, 2 * t, t) for t in targets}
nat = float(np.mean([mae(face_for(full, 2 * (t + 1), t)[0], ref_full[t][0]) for t in targets]))
sil = float(np.mean([mae(face_for(np.zeros_like(full), 2 * t, t)[0], ref_full[t][0]) for t in targets]))
res = {"targets": len(targets), "natural_next_frame_mae": nat, "silence_vs_speech_mae": sil, "runs": []}
print("scale: next-frame MAE", round(nat, 2), "| silence-vs-speech MAE", round(sil, 2), flush=True)
for L in [400, 1000, 2000]:
    ref = {t: stream_face(t, L, 2000) for t in targets}
    for X in [0, 40, 80, 120, 160, 200, 240, 280, 320, 400, 600]:
        m, fe, mf = [], [], []
        for t in targets:
            g, ch = stream_face(t, L, X)
            m.append(mae(g, ref[t][0]))
            fe.append(np.linalg.norm(ch - ref[t][1]) / np.linalg.norm(ref[t][1]))
            mf.append(mae(g, ref_full[t][0]))
        r = {"left_ms": L, "lookahead_ms": X, "mae_vs_long_lookahead": round(float(np.mean(m)), 3),
             "p90": round(float(np.percentile(m, 90)), 3), "feat_rel_err": round(float(np.mean(fe)), 4),
             "mae_vs_full_utterance": round(float(np.mean(mf)), 3)}
        print(r, flush=True)
        res["runs"].append(r)
json.dump(res, open(outp, "w"), indent=1)
print("saved", outp)
