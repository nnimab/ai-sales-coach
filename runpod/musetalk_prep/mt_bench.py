# Throughput / VRAM benchmark for MuseTalk 1.5 (UNet + VAE decode) and whisper feature extraction.
# Usage: python mt_bench.py <avatar_dir> [mask_name]
import os, sys, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import torch
import mtlib

adir = sys.argv[1]
mask = sys.argv[2] if len(sys.argv) > 2 else "m90_90"
torch.backends.cudnn.benchmark = True
models = mtlib.load_models()
base_vram = torch.cuda.memory_allocated() / 1e9
av = mtlib.load_avatar(adir, mask)
N = len(av["latents"])
res = {"weights_vram_gb": base_vram, "gpu": torch.cuda.get_device_name(0), "batch": {}}
rng = np.random.default_rng(0)
for bs in [1, 2, 4, 6, 8, 12, 16, 24]:
    lat = [av["latents"][i % N] for i in range(bs)]
    ch = [rng.standard_normal((50, 384)).astype(np.float32) for _ in range(bs)]
    for _ in range(3):
        mtlib.infer(models, lat, ch)
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    it = 20
    t = time.time()
    for _ in range(it):
        mtlib.infer(models, lat, ch)
    torch.cuda.synchronize()
    dt = (time.time() - t) / it
    res["batch"][bs] = {"ms_per_batch": round(dt * 1000, 1), "fps": round(bs / dt, 1),
                        "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2)}
    print(bs, res["batch"][bs], flush=True)

# whisper: LiveTalking-style call on a short window (extractor pads to 30 s internally)
ap = models[4]
for secs in [0.56, 1.04, 2.0]:
    x = rng.standard_normal(int(secs * 16000)).astype(np.float32) * 0.05
    for _ in range(3):
        ap.audio2feat(x)
    torch.cuda.synchronize()
    t = time.time()
    for _ in range(10):
        ap.audio2feat(x)
    torch.cuda.synchronize()
    res.setdefault("whisper_ms", {})[secs] = round((time.time() - t) / 10 * 1000, 1)
print("whisper ms per call:", res["whisper_ms"], flush=True)

# CPU paste-back cost (one frame, full resolution)
k = 0
face = (rng.random((256, 256, 3)) * 255).astype(np.uint8)
t = time.time()
for i in range(50):
    mtlib.paste_back(av["frames"][i % N], face, av["coords"][i % N], av["masks"][i % N], av["mcoords"][i % N])
res["paste_ms_per_frame"] = round((time.time() - t) / 50 * 1000, 2)
res["frame_shape"] = list(av["frames"][0].shape)
print("paste ms/frame:", res["paste_ms_per_frame"], flush=True)
json.dump(res, open(f"{adir}/bench.json", "w"), indent=1)
print(json.dumps(res))
