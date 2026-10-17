#!/bin/bash
# Download MuseTalk 1.5 weights (resumable; marker files in /workspace/markers)
mkdir -p /workspace/markers
exec >> /workspace/dl_models.log 2>&1
. /workspace/venv/bin/activate
M=/workspace/LiveTalking/models
cd $M
echo "== start $(date)"
if [ ! -f /workspace/markers/mt_weights.done ]; then
  hf download TMElyralab/MuseTalk musetalkV15/unet.pth musetalkV15/musetalk.json --local-dir $M || exit 1
  hf download stabilityai/sd-vae-ft-mse config.json diffusion_pytorch_model.bin --local-dir $M/sd-vae || exit 1
  hf download openai/whisper-tiny config.json pytorch_model.bin preprocessor_config.json --local-dir $M/whisper || exit 1
  mkdir -p $M/face-parse-bisent
  [ -s $M/face-parse-bisent/resnet18-5c106cde.pth ] || curl -sL https://download.pytorch.org/models/resnet18-5c106cde.pth -o $M/face-parse-bisent/resnet18-5c106cde.pth
  if [ ! -s $M/face-parse-bisent/79999_iter.pth ]; then
    gdown --id 154JgKpzCPW82qINcVieuPH3fZ2e0P812 -O $M/face-parse-bisent/79999_iter.pth || hf download ManyOtherFunctions/face-parse-bisent 79999_iter.pth --local-dir $M/face-parse-bisent
  fi
  ls -laR $M | head -60
  touch /workspace/markers/mt_weights.done
fi
if [ ! -f /workspace/markers/wav2lip_avatar.done ]; then
  cp /workspace/lt_models/wav2lip256.pth $M/wav2lip.pth
  mkdir -p /workspace/LiveTalking/data/avatars && tar -xzf /workspace/lt_models/wav2lip256_avatar1.tar.gz -C /workspace/LiveTalking/data/avatars && touch /workspace/markers/wav2lip_avatar.done
fi
pip install -q face_alignment ftfy imageio-ffmpeg && touch /workspace/markers/pip_extra.done
echo "== mt done $(date)"
