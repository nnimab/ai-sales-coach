#!/bin/bash
export PATH=/workspace/face_engine/bin:$PATH
for e in worried happy; do /workspace/face_engine/tools/prep_face.sh /workspace/faces/wang/${e}_s7.mp4 /workspace/face_engine/faces/wang/${e}_s7 /workspace/avatars/wang_mt/keep_hand.png; done
