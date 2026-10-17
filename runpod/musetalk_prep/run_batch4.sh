#!/bin/bash
# bench (4 CPU threads) -> u57 masks -> blending experiments -> batch2 (wav2lip, fixed bbox). Resumable.
cd /workspace
P=/workspace/venv/bin/python
A=/workspace/avatars/wang_mt
M=/workspace/markers
exec >> /workspace/run_batch4.log 2>&1
echo "== start $(date)"
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 TORCH_HOME=/workspace/torch_home
if [ ! -f $M/bench_t4.done ]; then
  [ -f $A/bench.json ] && [ ! -f $A/bench_throttled_96threads.json ] && mv $A/bench.json $A/bench_throttled_96threads.json
  $P mt/mt_bench.py $A && cp $A/bench.json $A/bench_t4.json && touch $M/bench_t4.done
fi
cat /sys/fs/cgroup/cpu/cpu.stat
[ -f $M/masks_u57.done ] || { $P mt/mt_masks.py $A 90 90 0.57 && $P mt/mt_masks.py $A 90 40 0.57 && touch $M/masks_u57.done; }
[ -f $M/blend_exp.done ] || { $P mt/blend_exp.py $A /workspace/audio/gemini_wang.wav /workspace/renders/blend all && touch $M/blend_exp.done; }
/workspace/mt/run_batch2.sh
echo "== BATCH4 DONE $(date)"
