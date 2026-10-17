#!/bin/bash
cd /workspace/face_engine
. /workspace/venv/bin/activate
export PATH=/workspace/face_engine/bin:$PATH
python tools/e2e_test.py /workspace/audio/leda.wav --viewer-token-out /workspace/face_engine/tmp/viewer_win.json --wait-viewer 120 $E2E_ARGS > logs/e2e_full.out 2>&1
