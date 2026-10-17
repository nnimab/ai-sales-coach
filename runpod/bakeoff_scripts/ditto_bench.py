# Ditto per-component micro-benchmark (single thread, no pipeline contention) on this GPU.
# Usage: python ditto_bench.py trt|pytorch <source.png> [out.json]
import os, sys, time, json
DITTO = "/workspace/bakeoff/ditto"
sys.path.insert(0, DITTO)
os.chdir(DITTO)
import numpy as np
import torch

backend, src = sys.argv[1], sys.argv[2]
out = sys.argv[3] if len(sys.argv) > 3 else None
CK = f"{DITTO}/checkpoints"
if backend == "trt":
    data_root, cfg = f"{CK}/ditto_trt_Ampere_Plus", f"{CK}/ditto_cfg/v0.4_hubert_cfg_trt_online.pkl"
else:
    data_root, cfg = f"{CK}/ditto_pytorch", f"{CK}/ditto_cfg/v0.4_hubert_cfg_pytorch.pkl"
from stream_pipeline_online import StreamSDK

sdk = StreamSDK(cfg, data_root)
sdk.setup(src, "/tmp/ditto_bench_tmp.mp4", online_mode=True, overlap_v2=70, sampling_timesteps=10)
si = sdk.source_info
f_s, x_s_info, frame_rgb, M = si["f_s_lst"][0], si["x_s_info_lst"][0], si["img_rgb_lst"][0], si["M_c2o_lst"][0]


def bench(fn, n, warm=3):
    for _ in range(warm):
        r = fn()
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    t = time.perf_counter()
    for _ in range(n):
        r = fn()
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    return (time.perf_counter() - t) / n * 1000, r


res = {"backend": backend}
chunk = (np.random.default_rng(0).standard_normal(6480) * 0.05).astype(np.float32)
res["hubert_ms_per_200ms_chunk"], _ = bench(lambda: sdk.wav2feat(chunk, chunksize=(3, 5, 2)), 20)
aud = sdk.condition_handler(np.zeros((80, 1024), np.float32), 0)[None]
a2m = sdk.audio2motion
for steps in (10, 5):
    a2m.lmdm.setup(steps)
    res[f"lmdm_ms_per_call_{steps}steps(=10 new frames)"], _ = bench(lambda: a2m.lmdm(a2m.kp_cond, aud, steps), 5, 1)
a2m.lmdm.setup(10)
kp = a2m(aud, None)
x_d_info = a2m.cvt_fmt(kp)[5]
sdk.motion_stitch.d0 = a2m.cvt_fmt(kp[0:1])[0]
res["motion_stitch_ms"], (x_s, x_d) = bench(lambda: sdk.motion_stitch(x_s_info, x_d_info), 50)
res["warp_ms"], f3d = bench(lambda: sdk.warp_f3d(f_s, x_s, x_d), 30)
res["decode_ms"], img = bench(lambda: sdk.decode_f3d(f3d), 30)
res["putback_ms"], _ = bench(lambda: sdk.putback(frame_rgb, img, M), 30)
res["render_img_shape"] = list(np.asarray(img).shape)
res["frame_shape"] = list(frame_rgb.shape)
per_frame = res["motion_stitch_ms"] + res["warp_ms"] + res["decode_ms"] + res["putback_ms"] \
    + res["lmdm_ms_per_call_10steps(=10 new frames)"] / 10 + res["hubert_ms_per_200ms_chunk"] / 5
res["serial_ms_per_frame"] = per_frame
res["serial_fps"] = 1000 / per_frame
res = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in res.items()}
print("BENCH", json.dumps(res), flush=True)
if out:
    json.dump(res, open(out, "w"), indent=1)
sys.stdout.flush()
os._exit(0)  # worker threads / TRT destructors can hang on exit
