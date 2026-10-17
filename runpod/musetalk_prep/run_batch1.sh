#!/bin/bash
# Phase-1 batch: benchmark, lookahead, final renders for 王小姐 (resumable via marker files)
cd /workspace
P=/workspace/venv/bin/python
A=/workspace/avatars/wang_mt
R=/workspace/renders
K=$A/keep_hand.png
M=/workspace/markers
exec >> /workspace/run_batch1.log 2>&1
echo "== start $(date)"
[ -f $M/bench.done ] || { $P mt/mt_bench.py $A && touch $M/bench.done; }
[ -f $M/gem_main.done ] || { $P mt/mt_render.py $A /workspace/audio/gemini_wang.wav $R/wang_musetalk_gemini.mp4 --keep $K --bs 8 && touch $M/gem_main.done; }
[ -f $M/gem_bypass.done ] || { $P mt/mt_render.py $A /workspace/audio/gemini_wang.wav $R/wang_musetalk_gemini_idlebypass.mp4 --keep $K --idle_bypass --bs 8 && touch $M/gem_bypass.done; }
[ -f $M/gem_side.done ] || { $P mt/mt_render.py $A /workspace/audio/gemini_wang.wav $R/wang_musetalk_gemini_side.mp4 --keep $K --side --bs 8 && touch $M/gem_side.done; }
[ -f $M/zh1.done ] || { $P mt/mt_render.py $A /workspace/audio/zh1.wav $R/wang_musetalk_1.mp4 --keep $K --bs 8 && touch $M/zh1.done; }
[ -f $M/zh2.done ] || { $P mt/mt_render.py $A /workspace/audio/zh2.wav $R/wang_musetalk_2.mp4 --keep $K --bs 8 && touch $M/zh2.done; }
[ -f $M/lookahead.done ] || { $P mt/mt_lookahead.py $A /workspace/audio/gemini_wang.wav $A/lookahead_gemini.json && touch $M/lookahead.done; }
echo "== BATCH1 DONE $(date)"
