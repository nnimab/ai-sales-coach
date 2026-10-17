#!/bin/bash
# Padded test audio (0.5 s silence before/after, same for A and D) + Ditto source image (= first frame of the neutral idle clip).
set -e
mkdir -p /workspace/bakeoff/audio /workspace/bakeoff/src
/workspace/venv/bin/python - <<'PY'
import soundfile as sf, numpy as np, resampy
x, sr = sf.read("/workspace/audio/leda.wav", dtype="float32", always_2d=True); x = x.mean(1)
p = np.zeros(int(0.5 * sr), np.float32)
y = np.concatenate([p, x, p]); sf.write("/workspace/bakeoff/audio/leda_pad24k.wav", y, sr)
y16 = resampy.resample(y, sr, 16000).astype(np.float32); sf.write("/workspace/bakeoff/audio/leda_pad16k.wav", y16, 16000)
print("audio", sr, len(x) / sr, "s ->", len(y) / sr, "s padded")
PY
cp /workspace/face_engine/faces/wang/neutral/full_imgs/00000000.png /workspace/bakeoff/src/wang_idle_f0.png
echo inputs ok
