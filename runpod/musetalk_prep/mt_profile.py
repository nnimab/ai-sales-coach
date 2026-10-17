# Split MuseTalk per-batch cost into UNet vs VAE-decode, and try cheap speedups (channels_last, torch.compile).
# Usage: python mt_profile.py [compile]
import os, sys, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import torch
import mtlib

torch.backends.cudnn.benchmark = True
vae, unet, pe, ts, ap = mtlib.load_models()
res = {}


def timeit(fn, reps=20):
    for _ in range(3):
        fn()
    torch.cuda.synchronize(); t = time.perf_counter()
    for _ in range(reps):
        fn()
    torch.cuda.synchronize()
    return (time.perf_counter() - t) / reps * 1000


def run(tag):
    for bs in [4, 8]:
        lat = torch.randn(bs, 8, 32, 32, device="cuda", dtype=torch.float16)
        a = pe(torch.randn(bs, 50, 384, device="cuda", dtype=torch.float16))
        out = {}
        with torch.no_grad():
            out["unet_ms"] = timeit(lambda: unet.model(lat, ts, encoder_hidden_states=a).sample)
            z = unet.model(lat, ts, encoder_hidden_states=a).sample
            out["vae_ms"] = timeit(lambda: vae.vae.decode(z / vae.scaling_factor).sample)
            out["vae_plus_download_ms"] = timeit(lambda: vae.decode_latents(z))
        out["fps_gpu_only"] = round(bs / (out["unet_ms"] + out["vae_ms"]) * 1000, 1)
        res[f"{tag}_bs{bs}"] = {k: round(v, 2) for k, v in out.items()}
        print(tag, bs, res[f"{tag}_bs{bs}"], flush=True)


run("fp16")
unet.model = unet.model.to(memory_format=torch.channels_last)
vae.vae = vae.vae.to(memory_format=torch.channels_last)
run("fp16_channels_last")
if "compile" in sys.argv:
    t = time.time()
    unet.model = torch.compile(unet.model)
    vae.vae.decoder = torch.compile(vae.vae.decoder)
    run("fp16_cl_compiled")
    res["compile_s_incl_runs"] = round(time.time() - t, 1)
json.dump(res, open("/workspace/avatars/wang_mt/profile.json", "w"), indent=1)
print(json.dumps(res))
