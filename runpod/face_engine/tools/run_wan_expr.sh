#!/bin/bash
# Phase 2A: re-fetch Wan2.2 TI2V-5B small parts (container disk is wiped on restart), then generate expression clips.
# Transformer stays on /workspace/wan_tmp/transformer (persistent). Resumable via marker files.
mkdir -p /workspace/markers /workspace/wan_out/expr
exec >> /workspace/wan_out/expr/run.log 2>&1
echo "== start $(date)"
. /workspace/venv/bin/activate
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 HF_HOME=/root/.cache/huggingface
R=Wan-AI/Wan2.2-TI2V-5B-Diffusers
if [ ! -f /root/wan/vae/config.json ] || [ ! -d /root/wan/text_encoder ] || [ ! -f /root/wan/model_index.json ]; then
  hf download $R model_index.json --local-dir /root/wan || exit 1
  hf download $R --include "scheduler/*" "tokenizer/*" "text_encoder/*" "vae/*" --local-dir /root/wan || exit 1
fi
[ -e /root/wan/transformer ] || ln -s /workspace/wan_tmp/transformer /root/wan/transformer
du -shL /root/wan/*
echo "== download ok $(date)"
for J in "${@:-worried:7,happy:7 worried:11,happy:11}"; do
  for JJ in $J; do python /workspace/face_engine/tools/wan_expr.py /workspace/wan_out/expr "$JJ" 50; done
done
echo "== WAN EXPR DONE $(date)"
touch /workspace/markers/wan_expr.done
