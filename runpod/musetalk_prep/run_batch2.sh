#!/bin/bash
# Phase-1 batch 2: wav2lip256 comparison + fixed-bbox MuseTalk variant (resumable via marker files)
cd /workspace
P=/workspace/venv/bin/python
A=/workspace/avatars/wang_mt
R=/workspace/renders
M=/workspace/markers
G=/workspace/audio/gemini_wang.wav
exec >> /workspace/run_batch2.log 2>&1
echo "== start $(date)"
export TORCH_HOME=/workspace/torch_home OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4
[ -f $M/w2l_prep.done ] || { $P mt/w2l_prep.py $A/full_imgs /workspace/avatars/wang_w2l && touch $M/w2l_prep.done; }
[ -f $M/w2l_render.done ] || { $P mt/w2l_render.py /workspace/avatars/wang_w2l $G $R/wang_wav2lip_gemini.mp4 && \
  $P mt/w2l_render.py /workspace/avatars/wang_w2l $G $R/wang_wav2lip_gemini_keep.mp4 --keep $A/keep_hand.png && touch $M/w2l_render.done; }
[ -f $M/fixed_prep.done ] || { $P mt/mt_prep.py /workspace/faces/wang/idle.mp4 /workspace/avatars/wang_mt_fixed --bbox fixed && touch $M/fixed_prep.done; }
[ -f $M/fixed_render.done ] || { $P mt/mt_render.py /workspace/avatars/wang_mt_fixed $G $R/wang_gemini_fixedbbox.mp4 --keep $A/keep_hand.png --bs 8 && touch $M/fixed_render.done; }
echo "== BATCH2 DONE $(date)"
