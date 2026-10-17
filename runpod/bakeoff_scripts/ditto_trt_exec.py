# How much of Ditto's TRT per-frame time is GPU compute vs. the wrapper's per-call cudaMalloc/free + pageable host copies?
# Times (a) TRTWrapper.setup+infer as Ditto calls it, (b) execute_async_v3 only on device-resident buffers.
# Usage: python ditto_trt_exec.py [out.json]
import os, sys, time, json
DITTO = "/workspace/bakeoff/ditto"
sys.path.insert(0, DITTO)
os.chdir(DITTO)
import numpy as np
import torch
from core.utils.tensorrt_utils import TRTWrapper
from cuda import cudart

E = f"{DITTO}/checkpoints/ditto_trt_Ampere_Plus"
PLUG = f"{DITTO}/checkpoints/ditto_onnx/libgrid_sample_3d_plugin.so"
rng = np.random.default_rng(0)
res = {}


def run(name, fname, inputs, plugins=(), n=30):
    m = TRTWrapper(f"{E}/{fname}", plugin_file_list=list(plugins))
    for _ in range(3):
        m.setup(inputs); m.infer()
    t = time.perf_counter()
    for _ in range(n):
        m.setup(inputs); m.infer()
    full = (time.perf_counter() - t) / n * 1000
    m.setup(inputs); m.infer()  # buffers now resident; time execute only
    cudart.cudaDeviceSynchronize()
    t = time.perf_counter()
    for _ in range(n):
        m.context.execute_async_v3(0)
    cudart.cudaDeviceSynchronize()
    ex = (time.perf_counter() - t) / n * 1000
    res[name] = {"ditto_wrapper_ms": round(full, 2), "execute_only_ms": round(ex, 2)}
    print(name, res[name], flush=True)
    return m


warp_in = {"feature_3d": rng.standard_normal((1, 32, 16, 64, 64)).astype(np.float32) * 0.1,
           "kp_source": rng.standard_normal((1, 21, 3)).astype(np.float32) * 0.3,
           "kp_driving": rng.standard_normal((1, 21, 3)).astype(np.float32) * 0.3}
w = run("warp_fp16", "warp_network_fp16.engine", warp_in)
names = [w.engine.get_tensor_name(i) for i in range(w.engine.num_io_tensors)]
print("warp io", [(nm, tuple(w.engine.get_tensor_shape(nm))) for nm in names], flush=True)
dec_in = {"feature": w.buffer["out"][0].copy()}
d = run("decoder_fp16", "decoder_fp16.engine", dec_in)
names = [d.engine.get_tensor_name(i) for i in range(d.engine.num_io_tensors)]
print("dec io", [(nm, tuple(d.engine.get_tensor_shape(nm))) for nm in names], flush=True)
json.dump(res, open(sys.argv[1], "w"), indent=1) if len(sys.argv) > 1 else None
sys.stdout.flush()
os._exit(0)
