#!/bin/bash
# Separate venv for Ditto: own packages first, then the base venv's site-packages (torch 2.9.1 cu128, numpy, cv2...) via .pth.
set -x
V=/workspace/venv_ditto
[ -x $V/bin/python ] || python3 -m venv $V
SP=$V/lib/python3.11/site-packages
echo /workspace/venv/lib/python3.11/site-packages > $SP/zz_base_venv.pth
P=$V/bin/pip
$P install -q --upgrade pip
$P install --no-deps "cuda-python==12.6.2.post1" colored filetype cython polygraphy || exit 1
$P install --no-deps onnxruntime-gpu coloredlogs humanfriendly || exit 1
$P install --no-deps mediapipe absl-py flatbuffers sounddevice "protobuf>=4.25,<5" || exit 1
# TensorRT 8.6.1 (Ditto's prebuilt Ampere_Plus engines need exactly 8.6)
$P install --no-deps "tensorrt==8.6.1.post1" || $P install --no-deps --extra-index-url https://pypi.nvidia.com "tensorrt-libs==8.6.1" "tensorrt-bindings==8.6.1" "tensorrt==8.6.1" || echo TRT_INSTALL_FAILED
$V/bin/python -c "import tensorrt as t; print('trt', t.__version__)" || echo TRT_IMPORT_FAILED
$V/bin/python -c "import onnxruntime as o; print('ort', o.__version__, o.get_available_providers())"
$V/bin/python -c "import mediapipe as m; print('mp', m.__version__)"
$V/bin/python -c "import torch, numpy, cv2; print(torch.__version__, numpy.__version__, cv2.__version__)"
echo SETUP_DONE
