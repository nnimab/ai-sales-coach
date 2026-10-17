#!/bin/bash
# Prepare one expression clip as a MuseTalk avatar for the face engine (fixed bbox, u57 jaw mask, hand keep-mask).
# Usage: prep_face.sh <clip_25fps.mp4> <out_dir> <keep_mask.png>
set -e
. /workspace/venv/bin/activate
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 TORCH_HOME=/workspace/torch_home
SRC=$1; OUT=$2; KEEP=$3
mkdir -p "$OUT"
[ -f "$OUT/PREP_DONE" ] || python /workspace/mt/mt_prep.py "$SRC" "$OUT" --bbox fixed
[ -f "$OUT/mask_coords_m90_90_u57.pkl" ] || python /workspace/mt/mt_masks.py "$OUT" 90 90 0.57
cp "$KEEP" "$OUT/keep_hand.png"
cp "$SRC" "$OUT/clip.mp4"
echo "prep ok $OUT"
