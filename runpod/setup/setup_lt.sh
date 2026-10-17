#!/bin/bash
# 在 RunPod 上安裝 LiveTalking（裝在 /workspace，關機後還在）
set -e
cd /workspace
exec > >(tee -a /workspace/setup_lt.log) 2>&1
echo "== start $(date)"
apt-get update -qq && apt-get install -y -qq ffmpeg git-lfs > /dev/null
[ -d LiveTalking ] || git clone -q https://github.com/lipku/LiveTalking.git
python -m venv /workspace/venv
. /workspace/venv/bin/activate
pip install -q --upgrade pip
pip install -q torch==2.9.1 torchvision==0.24.1 torchaudio==2.9.1 --index-url https://download.pytorch.org/whl/cu128
cd LiveTalking
pip install -q -r requirements.txt
pip install -q gdown livekit livekit-api
echo "== pip done $(date)"
python -c "import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available(),torch.cuda.get_device_name(0))"
# 模型（官方 Google Drive 資料夾）
mkdir -p /workspace/lt_models
cd /workspace/lt_models
gdown -q --folder https://drive.google.com/drive/folders/1FOC_MD6wdogyyX_7V1d4NDIO7P9NlSAJ -O . || echo "gdown folder failed"
ls -la /workspace/lt_models
echo "== done $(date)"
