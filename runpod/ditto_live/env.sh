# source /workspace/ditto_live/env.sh  -> environment for importing ditto_stream (TRT 8.6.1 + cuDNN 8 + torch libs).
# Python: /workspace/venv_ditto/bin/python  (its .pth also exposes /workspace/venv site-packages).
B=/workspace/venv/lib/python3.11/site-packages/nvidia
export LD_LIBRARY_PATH=/workspace/venv_ditto/cudnn8/nvidia/cudnn/lib:$B/cudnn/lib:$B/cufft/lib:$B/curand/lib:$B/cublas/lib:$B/cuda_runtime/lib:$B/cuda_nvrtc/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
export OMP_NUM_THREADS=${OMP_NUM_THREADS:-4} MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 PYTHONUNBUFFERED=1
export TORCH_HOME=/workspace/torch_home FFMPEG=/workspace/face_engine/bin/ffmpeg
# /workspace is a network volume: keep pyc / pyximport (blend.pyx, built into ~/.pyxbld on first import) / temp local
mkdir -p /root/pycache /root/tmp
export PYTHONPYCACHEPREFIX=/root/pycache TMPDIR=${TMPDIR:-/root/tmp}
export DITTO_ROOT=/workspace/bakeoff/ditto DITTO_PY=/workspace/venv_ditto/bin/python
