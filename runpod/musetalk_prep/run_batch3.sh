#!/bin/bash
# Re-run the benchmark with small CPU thread pools (pod CPU quota ~7.65 vCPU), then batch 2.
cd /workspace
P=/workspace/venv/bin/python
A=/workspace/avatars/wang_mt
M=/workspace/markers
exec >> /workspace/run_batch3.log 2>&1
echo "== start $(date)"
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4
if [ ! -f $M/bench_t4.done ]; then
  [ -f $A/bench.json ] && [ ! -f $A/bench_throttled_96threads.json ] && mv $A/bench.json $A/bench_throttled_96threads.json
  $P mt/mt_bench.py $A && cp $A/bench.json $A/bench_t4.json && touch $M/bench_t4.done
fi
cat /sys/fs/cgroup/cpu/cpu.stat
/workspace/mt/run_batch2.sh
echo "== BATCH3 DONE $(date)"
