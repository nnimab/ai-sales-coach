#!/bin/bash
# Start the face engine (port 8010).  Usage: run.sh            -> foreground
#                                            run.sh -d         -> background (logs/stdout.log, logs/engine.pid)
#                                            run.sh stop       -> stop the background instance
# SSH shells don't inherit the pod's env vars: take only what the engine needs from PID 1 (never printed).
cd "$(dirname "$0")"
D=$(pwd)
PIDF=$D/logs/engine.pid
mkdir -p "$D/logs" "$D/cache"
if [ "$1" = "stop" ]; then
  [ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null && echo "stopped" || echo "not running"
  rm -f "$PIDF"; exit 0
fi
if [ -r /proc/1/environ ]; then
  while IFS= read -r -d '' kv; do
    case "$kv" in
      FACE_SECRET=*) [ -z "$FACE_SECRET" ] && export "$kv" ;;
      OMP_NUM_THREADS=*) [ -z "$OMP_NUM_THREADS" ] && export "$kv" ;;
    esac
  done < /proc/1/environ
fi
export OMP_NUM_THREADS=${OMP_NUM_THREADS:-4} MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4
# Which engine: $D/ENGINE = "ditto" -> Ditto (server_ditto.py in the Ditto venv); anything else -> MuseTalk (default).
# Switch with: bash /workspace/start_all.sh use-ditto | use-musetalk
ENGINE=$(tr -d '[:space:]' 2>/dev/null < "$D/ENGINE")
if [ "${ENGINE:-musetalk}" = "ditto" ] && [ "$1" != "-d" ]; then
  exec bash "${DITTO_ENV:-/workspace/ditto_live/run_py.sh}" "$D/server_ditto.py"
fi
export TORCH_HOME=/workspace/torch_home
# Compile caches live on the local container disk: on a network volume Triton's parallel temp-dir writes fail
# ("Stale file handle"). Seed from $D/cache once per boot; save back after the engine reports ready.
LC=/root/fe_cache
if [ ! -d "$LC" ]; then mkdir -p "$LC"; cp -a "$D/cache/." "$LC/" 2>/dev/null; fi
export TORCHINDUCTOR_CACHE_DIR=$LC/inductor TRITON_CACHE_DIR=$LC/triton TORCHINDUCTOR_COMPILE_THREADS=4
save_cache() {
  for _ in $(seq 1 120); do
    sleep 10
    if curl -s localhost:8010/face/health | grep -q '"ready": true'; then
      rm -rf "$D/cache.new" && cp -a "$LC" "$D/cache.new" && rm -rf "$D/cache" && mv "$D/cache.new" "$D/cache"
      return
    fi
  done
}
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True PYTHONUNBUFFERED=1
. /workspace/venv/bin/activate
if [ "$1" = "-d" ]; then
  if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then echo "already running (pid $(cat "$PIDF"))"; exit 0; fi
  save_cache > /dev/null 2>&1 &
  setsid nohup python "$D/server.py" >> "$D/logs/stdout.log" 2>&1 < /dev/null &
  echo $! > "$PIDF"
  echo "started pid $(cat "$PIDF")"
else
  save_cache > /dev/null 2>&1 &
  exec python "$D/server.py"
fi
