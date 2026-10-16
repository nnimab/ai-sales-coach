#!/bin/bash
# RunPod 開機時把「自架臉孔引擎」、「對練教練」和「固定入口回報」在背景開好，當掉自動重開；
# 記錄在 /workspace/logs/{engine,app,link}.log。
# Pod 的 Container Start Command（先開我們的，再跑 RunPod 原本的 /start.sh，SSH 照常）：
#   bash -c "bash /workspace/start_all.sh; exec /start.sh"
# 也可以單獨用：
#   bash /workspace/start_all.sh app       開對練教練；已經在跑就重開（換新版程式碼）
#   bash /workspace/start_all.sh engine    開臉孔引擎（/workspace/face_engine/run.sh；還沒有就等它出現，8010 已經有人開著就不動）
#   bash /workspace/start_all.sh link      開固定入口回報（/workspace/app/tools/runpod/live_link.py，每 5 分鐘把這台的網址報給
#                                          Vercel，手機開 Vercel 的 /live 就會轉過來）；已經在跑就重開
#   bash /workspace/start_all.sh status    看三個現在怎樣
#   bash /workspace/start_all.sh use-ditto     臉孔引擎改用 Ditto（整張臉一起動）；記在 /workspace/face_engine/ENGINE，重開機也照這個
#   bash /workspace/start_all.sh use-musetalk  退回 MuseTalk（預設；重開要 2～5 分鐘編譯）
#   bash /workspace/start_all.sh stop-app  停掉對練教練（不再自動重開）
#   bash /workspace/start_all.sh stop-link 停掉固定入口回報（不再自動重開；Vercel 15 分鐘後就當作真人版沒開）
# 這支自己很快就結束，不會擋住開機（一定回 0）。
set -u
W=/workspace
LOG=$W/logs
SELF="$W/start_all.sh"
[ -f "$SELF" ] || SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
mkdir -p "$LOG"

now() { date -u '+%F %T'; }
alive() { [ -f "$1" ] && kill -0 "$(cat "$1" 2>/dev/null)" 2>/dev/null; }
port_busy() { ss -ltnH "sport = :$1" 2>/dev/null | grep -q .; }

# 記錄太大（20 MB）就換一個新的，舊的留一份 .1
rotate() {
  local f="$LOG/$1.log"
  if [ -f "$f" ] && [ "$(stat -c %s "$f" 2>/dev/null || echo 0)" -gt 20000000 ]; then mv -f "$f" "$f.1"; fi
}

# 跑一次：程式的 PID 記在 $LOG/名稱.pid（重開時用），結束後等 2 秒（一開就當掉的等 30 秒）再開
run_once() {
  local name=$1; shift
  rotate "$name"
  local t0; t0=$(date +%s)
  echo "[$(now)] 啟動 $name" >> "$LOG/$name.log"
  "$@" >> "$LOG/$name.log" 2>&1 < /dev/null &
  echo $! > "$LOG/$name.pid"
  wait $!
  local code=$?
  local wait_s=2
  [ $(( $(date +%s) - t0 )) -lt 30 ] && wait_s=30 # 一開就當掉：慢一點再試，記錄才不會灌爆
  if [ -f "$LOG/$name.restart" ]; then wait_s=1; rm -f "$LOG/$name.restart"; fi # 是我們要它重開（換新版）
  echo "[$(now)] $name 結束（代碼 $code），$wait_s 秒後重開" >> "$LOG/$name.log"
  sleep "$wait_s"
}

loop_app() {
  while true; do
    if [ -f "$W/app/run.sh" ]; then run_once app bash "$W/app/run.sh"; else sleep 30; fi
  done
}

loop_engine() {
  while true; do
    if [ ! -f "$W/face_engine/run.sh" ]; then sleep 30; continue; fi # 引擎還沒裝好：等
    if port_busy 8010; then sleep 30; continue; fi # 8010 已經有人開著（例如手動開的）：不動它
    run_once engine bash "$W/face_engine/run.sh"
  done
}

# 固定入口回報：沒設 LIVE_LINK_SECRET、LIVE_LINK_TARGET 時它只說一次「沒有設定」，之後安靜地等
loop_link() {
  local py="$W/app/tools/runpod/live_link.py"
  while true; do
    if [ -f "$py" ]; then run_once link env PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 python3 "$py"; else sleep 30; fi
  done
}

spawn() { # 名稱：在背景開一個不會跟著 SSH 結束的迴圈
  local name=$1
  if alive "$LOG/$name.loop.pid"; then return 1; fi
  setsid nohup bash "$SELF" "_loop_$name" > /dev/null 2>&1 < /dev/null &
  echo $! > "$LOG/$name.loop.pid"
  return 0
}

start_app() {
  if spawn app; then
    echo "[$(now)] 對練教練：開始（記錄 $LOG/app.log）"
  elif alive "$LOG/app.pid"; then
    touch "$LOG/app.restart"
    kill "$(cat "$LOG/app.pid")" 2>/dev/null # 迴圈還在：只重開程式，新版程式碼才會生效
    echo "[$(now)] 對練教練：重開中（記錄 $LOG/app.log）"
  else
    echo "[$(now)] 對練教練：已經在跑（記錄 $LOG/app.log）"
  fi
}

start_engine() {
  if spawn engine; then
    echo "[$(now)] 臉孔引擎：開始看顧（記錄 $LOG/engine.log）"
  else
    echo "[$(now)] 臉孔引擎：已經在看顧"
  fi
}

start_link() {
  if spawn link; then
    echo "[$(now)] 固定入口回報：開始（記錄 $LOG/link.log）"
  elif alive "$LOG/link.pid"; then
    touch "$LOG/link.restart"
    kill "$(cat "$LOG/link.pid")" 2>/dev/null # 迴圈還在：只重開程式，新版程式碼才會生效
    echo "[$(now)] 固定入口回報：重開中（記錄 $LOG/link.log）"
  else
    echo "[$(now)] 固定入口回報：已經在跑（記錄 $LOG/link.log）"
  fi
}

status() {
  for n in engine app link; do
    loop=no; prog=no
    alive "$LOG/$n.loop.pid" && loop=yes
    alive "$LOG/$n.pid" && prog=yes
    echo "$n: 看顧=$loop 程式=$prog"
  done
  echo "臉孔引擎用：$(tr -d '[:space:]' 2>/dev/null < "$W/face_engine/ENGINE" || true)（空白 = musetalk）"
  port_busy 8010 && echo "8010: 有人在用" || echo "8010: 沒有"
  port_busy 8765 && echo "8765: 有人在用" || echo "8765: 沒有"
}

case "${1:-all}" in
  _loop_app) loop_app ;;
  _loop_engine) loop_engine ;;
  _loop_link) loop_link ;;
  app) start_app ;;
  engine) start_engine ;;
  link) start_link ;;
  status) status ;;
  stop-app)
    alive "$LOG/app.loop.pid" && kill "$(cat "$LOG/app.loop.pid")"
    alive "$LOG/app.pid" && kill "$(cat "$LOG/app.pid")"
    echo "[$(now)] 對練教練：已停止" ;;
  use-ditto | use-musetalk)
    echo "${1#use-}" > "$W/face_engine/ENGINE"
    if alive "$LOG/engine.pid"; then
      touch "$LOG/engine.restart"
      kill "$(cat "$LOG/engine.pid")" 2>/dev/null # 看顧的迴圈會用新的引擎重開
    fi
    alive "$LOG/engine.loop.pid" || start_engine
    echo "[$(now)] 臉孔引擎：改用 ${1#use-}，重開中（記錄 $LOG/engine.log）" ;;
  stop-link)
    alive "$LOG/link.loop.pid" && kill "$(cat "$LOG/link.loop.pid")"
    alive "$LOG/link.pid" && kill "$(cat "$LOG/link.pid")"
    echo "[$(now)] 固定入口回報：已停止" ;;
  all | *)
    start_engine
    start_app
    start_link ;;
esac
exit 0
