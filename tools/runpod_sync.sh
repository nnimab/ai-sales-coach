#!/bin/sh
# 把這台電腦的程式碼送到 RunPod（/workspace/app），再重開那邊的對練教練。一行搞定：
#   sh tools/runpod_sync.sh <連線資料夾>          （Git Bash，在 demo 資料夾裡跑）
# 連線資料夾裡要有：id_pod（SSH 私鑰）、pod.json（{"id","ip","ports":{"22":埠}}），可以有 known_hosts。
# 資料夾裡有 rp.py（RunPod API 小工具）的話，會先問 RunPod 最新的 IP 和 SSH 埠（Pod 重開後埠會變）。
# 只送程式：根目錄的 .py、web/、scenarios/*.json、tools/runpod/，加上 face/ 裡客戶的照片和待機底片（通話畫面要用）。
# 不送 .env、voices.json、profile.json、.chatgpt/、scenarios/custom/、tts_cache/（那邊的資料在 /workspace/app_data）。
set -eu
DIR="${1:-${RUNPOD_DIR:-}}"
[ -n "$DIR" ] && [ -f "$DIR/id_pod" ] && [ -f "$DIR/pod.json" ] || {
  echo "用法：sh tools/runpod_sync.sh <放 id_pod 和 pod.json 的資料夾>" >&2
  exit 1
}
WDIR="$(cd "$DIR" && { pwd -W 2>/dev/null || pwd; })" # Windows 的 python 看得懂的路徑
DIR="$(cd "$DIR" && pwd)"
cd "$(dirname "$0")/.."

if [ -f "$DIR/rp.py" ]; then
  # 問 RunPod 現在的 IP 和 SSH 埠，寫回 pod.json（金鑰只在 rp.py 裡用，不會印出來）
  python - "$WDIR" <<'PY' || echo "（問不到 RunPod，先用 pod.json 裡的）" >&2
import json, os, sys
d = sys.argv[1]
sys.path.insert(0, d)
import rp
path = os.path.join(d, "pod.json")
pod = json.load(open(path))
p = rp.rest("GET", "/pods/" + pod["id"])
port, ip = (p.get("portMappings") or {}).get("22"), p.get("publicIp")
if not port or not ip:
    sys.exit(1)
if (pod.get("ip"), pod.get("ports", {}).get("22")) != (ip, port):
    pod.update(ip=ip, ports={"22": port})
    json.dump(pod, open(path, "w"))
    print(f"Pod 的 SSH 換成 {ip}:{port}")
PY
fi

IP=$(python -c "import json,sys;print(json.load(open(sys.argv[1]))['ip'])" "$WDIR/pod.json")
PORT=$(python -c "import json,sys;print(json.load(open(sys.argv[1]))['ports']['22'])" "$WDIR/pod.json")
ssh_pod() {
  ssh -i "$DIR/id_pod" -p "$PORT" -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$DIR/known_hosts" \
    -o ConnectTimeout=20 -o LogLevel=ERROR "root@$IP" "$@"
}

FILES="$(ls *.py) web $(ls scenarios/*.json) tools/runpod $(ls face/*_idle.mp4 face/*.webp face/*.jpg face/*.png 2>/dev/null)"
for f in .env voices.json profile.json; do
  case " $FILES " in *" $f "*) echo "不應該送 $f" >&2; exit 1 ;; esac
done
echo "送到 root@$IP:$PORT /workspace/app …"
# 先解到 app.new，整個好了才換上去：送到一半斷線，那邊的舊版還能用
tar --owner=0 --group=0 --exclude='__pycache__' --exclude='*.pyc' -cf - $FILES | ssh_pod '
  set -e
  rm -rf /workspace/app.new && mkdir -p /workspace/app.new
  tar --no-same-owner --no-same-permissions -xf - -C /workspace/app.new
  sed -i "s/\r$//" /workspace/app.new/tools/runpod/*.sh
  cp /workspace/app.new/tools/runpod/run.sh /workspace/app.new/run.sh
  cp /workspace/app.new/tools/runpod/start_all.sh /workspace/start_all.sh
  chmod +x /workspace/app.new/run.sh /workspace/start_all.sh
  rm -rf /workspace/app.old
  if [ -d /workspace/app ]; then mv /workspace/app /workspace/app.old; fi
  mv /workspace/app.new /workspace/app
  rm -rf /workspace/app.old
  bash /workspace/start_all.sh app
  sleep 3
  tail -n 6 /workspace/logs/app.log
'
