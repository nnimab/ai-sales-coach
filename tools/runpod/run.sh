#!/bin/bash
# RunPod 上的對練教練（伺服器模式）：補齊環境變數、設定伺服器模式，在 0.0.0.0:8765 開起來。
# 平常由 /workspace/start_all.sh 叫（當掉會自動重開）；要手動試：bash /workspace/app/run.sh
# 金鑰（GEMINI_API_KEY、LIVEKIT_*、FACE_SECRET、APP_SECRET…）都在 Pod 的環境變數，這裡只讀、不印、不寫進檔案。
set -u
APP_DIR="$(cd "$(dirname "$0")" && pwd)"

# SSH 進來的 shell 沒有 Pod 設的環境變數：從 1 號程序補上（已經有的不蓋掉）
if [ -r /proc/1/environ ]; then
  while IFS= read -r -d '' kv; do
    k="${kv%%=*}"
    case "$k" in '' | [0-9]* | *[!A-Za-z0-9_]*) continue ;; esac
    if [ -z "${!k+x}" ]; then export "$kv"; fi
  done < /proc/1/environ
fi

POD="${RUNPOD_POD_ID:-}"
export APP_MODE=server
export HOST="${APP_HOST:-0.0.0.0}"
export PORT="${APP_PORT:-8765}"
# 設定、聲音、背景、自訂情境、開場白語音都存在這裡（Pod 重開也還在）
export DATA_DIR="${DATA_DIR:-/workspace/app_data}"
if [ -n "$POD" ]; then
  # 手機開的網址（RunPod 的代理）；代理會帶 X-Forwarded-Host，伺服器只認這個
  export ALLOWED_HOSTS="${ALLOWED_HOSTS:-${POD}-${PORT}.proxy.runpod.net}"
  # 自架臉孔引擎：伺服器從 127.0.0.1 叫它，瀏覽器從代理連它的控制通道
  export FACE_PUBLIC_URL="${FACE_PUBLIC_URL:-wss://${POD}-8010.proxy.runpod.net}"
fi
export FACE_URL="${FACE_URL:-http://127.0.0.1:8010}"
export LIVEAVATAR_ENABLED="${LIVEAVATAR_ENABLED:-1}" # 真人模式預設開（網頁上關掉會記住）
export PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1

mkdir -p "$DATA_DIR"
cd "$APP_DIR" || exit 1
exec python3 server.py --no-browser
