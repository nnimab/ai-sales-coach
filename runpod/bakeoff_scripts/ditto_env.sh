#!/bin/bash
# Run a python script in the Ditto venv (TRT 8.6.1 + cuDNN 8 side-by-side with torch's cuDNN 9).
# L40S pod: /workspace is a network volume (MooseFS) -> every cache / temp / pyc / pyximport build goes to the local
# container disk (/root/...); only final outputs are copied to /workspace.
B=/workspace/venv/lib/python3.11/site-packages/nvidia
export LD_LIBRARY_PATH=/workspace/venv_ditto/cudnn8/nvidia/cudnn/lib:$B/cudnn/lib:$B/cufft/lib:$B/curand/lib:$B/cublas/lib:$B/cuda_runtime/lib:$B/cuda_nvrtc/lib:$LD_LIBRARY_PATH
export PATH=/workspace/face_engine/bin:$PATH FFMPEG=/workspace/face_engine/bin/ffmpeg
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 TORCH_HOME=/workspace/torch_home PYTHONUNBUFFERED=1
mkdir -p /root/pycache /root/tmp /root/.nv
export PYTHONPYCACHEPREFIX=/root/pycache TMPDIR=/root/tmp CUDA_CACHE_PATH=/root/.nv/ComputeCache HOME=/root
exec /workspace/venv_ditto/bin/python "$@"
