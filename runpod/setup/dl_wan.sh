#!/bin/bash
# Wan2.2 TI2V-5B diffusers weights on NON-persistent storage (container disk + /dev/shm) to spare /workspace
mkdir -p /workspace/markers
exec >> /workspace/dl_wan.log 2>&1
. /workspace/venv/bin/activate
echo "== start $(date)"
R=Wan-AI/Wan2.2-TI2V-5B-Diffusers
hf download $R --include 'transformer/*' --local-dir /dev/shm/wan || exit 1
hf download $R --include 'model_index.json' 'scheduler/*' 'tokenizer/*' 'text_encoder/*' 'vae/*' --local-dir /root/wan || exit 1
[ -e /root/wan/transformer ] || ln -s /dev/shm/wan/transformer /root/wan/transformer
du -shL /root/wan/*
touch /workspace/markers/wan_dl.done
echo "== done $(date)"
