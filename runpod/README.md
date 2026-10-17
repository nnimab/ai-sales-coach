# RunPod 真人版：備份與重建

RunPod 上「真人版」用到、但不在主程式裡的東西，都備份在這個資料夾。
主程式（對練教練）照常用 `tools/runpod_sync.sh` 送上去，不在這裡。

## 現在放在哪裡（2026-10-17）

- **網路硬碟**：`ai-coach-data`（id `0ave4bnnca`），100 GB，RunPod 美國密蘇里機房 `US-MO-1`，每月約 $7。
  這是完整的一份，模型和兩套 Python 環境都在上面，開機約 2 分鐘就能用。
- **影片和圖片**：GitHub 這個 repo 的 Release `runpod-media-2026-10-17`，檔案 `runpod-media.tar.zst`（627 MB）。
  sha256：`6516419485812c5dda5e2c18a79bbe877901800db8d5be89038d116fb2562fcb`
- **程式碼和套件清單**：就是這個資料夾。
- **模型**：沒有備份，都可以重新下載（見下面「從零重建」）。

## 資料夾對照（這裡 → 機器上的 /workspace）

| 這裡 | 機器上 | 是什麼 |
|---|---|---|
| `face_engine/` | `/workspace/face_engine/` | 臉孔引擎：`server.py`（MuseTalk）、`server_ditto.py`（Ditto）、`run.sh`（依 `ENGINE` 檔選引擎） |
| `ditto_live/` | `/workspace/ditto_live/` | Ditto 即時串流（`ditto_stream.py`）、環境設定 `env.sh`、`run_py.sh` |
| `bakeoff_scripts/` | `/workspace/bakeoff/scripts/` | Ditto 安裝（`setup_venv_ditto.sh`）、測速、調整用的腳本 |
| `musetalk_prep/` | `/workspace/mt/` | 做 MuseTalk 底片、遮罩、待機影片的腳本 |
| `setup/` | `/workspace/` | 下載 MuseTalk 模型、LiveTalking、Wan 的腳本 |
| `requirements/` | — | 兩套 Python 環境當時裝的套件和版本（`venv`、`venv_ditto`）、Ditto 模型檔清單 |
| `../tools/runpod/start_all.sh` | `/workspace/start_all.sh` | 開機自動開引擎、對練教練、固定入口回報 |

影片壓縮檔解開後會放回：`face_engine/faces`（王小姐三種表情底片）、`avatars`、`wan_out`、`renders`、`faces`、`audio`、
`ditto_live/assets`（Ditto 用的照片和手部遮罩）、`ditto_live/out`、`bakeoff/src`、`bakeoff/audio`、`bakeoff/out*`。

## 平常開機

1. 用 RunPod API 開機（`POST /pods/<id>/start`）。
2. 如果出現 **not enough free GPUs**（原本那台的顯卡被別人租走了）：
   照下面設定開一台新的，掛同一個網路硬碟。網址會變，但手機開
   **https://ai-sales-coach-phi.vercel.app/live** 會自動跳到新網址（機器每 5 分鐘回報一次）。
3. 新機器要重新做一次 ChatGPT 同步：本機版「AI 大腦 → 雲端版」，網址填新的 `https://<pod id>-8765.proxy.runpod.net`，再按「登入並同步」。

**機器設定**
- 映像：`runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`
- 顯卡：L40S（Ditto 用的 TensorRT 8.6 不支援 RTX 5090 這類新卡；4090、L40S、A100、H100 都可以）
- 網路硬碟掛在 `/workspace`，container disk 30 GB
- 埠：`8010/http`（臉孔引擎）、`8765/http`（對練教練）、`22/tcp`
- 啟動指令：`bash -c "bash /workspace/start_all.sh; exec /start.sh"`
- 環境變數（只列名稱，值在本機 `.env` 和 RunPod 上）：
  `GEMINI_API_KEY`、`LIVEKIT_URL`、`LIVEKIT_API_KEY`、`LIVEKIT_API_SECRET`、`APP_SECRET`、`FACE_SECRET`、
  `CHATGPT_SYNC_KEY`、`LIVE_LINK_SECRET`、`LIVE_LINK_TARGET`、`AUTH_MODE=open`、`LIVE_ENABLED=1`、
  `LLM_PROVIDER=gemini`、`PROFILE_SCOPE=shared`、`OMP_NUM_THREADS=4`、`PUBLIC_KEY`（SSH）

**切換臉孔引擎**
- `bash /workspace/start_all.sh use-ditto`：Ditto（整張臉一起動，載入約 1～1.5 分鐘，嘴型延遲約 0.8 秒）
- `bash /workspace/start_all.sh use-musetalk`：MuseTalk（只動嘴，第一次編譯 2～5 分鐘，延遲約 0.3 秒）
- 選擇記在 `/workspace/face_engine/ENGINE`，重開機也照這個。

## 從零重建（網路硬碟沒了的時候）

大約 1～2 小時，大部分時間在下載模型。

1. 開一台上面設定的機器（掛新的網路硬碟）。
2. 把這個資料夾的檔案照「資料夾對照」放回 `/workspace`，再把影片壓縮檔解回去：
   ```bash
   gh release download runpod-media-2026-10-17 -R nnimab/ai-sales-coach
   zstd -d -c runpod-media.tar.zst | tar -xf - -C /workspace
   ```
3. MuseTalk 環境（`/workspace/venv`，Python 3.11）：照 `requirements/venv-packages.txt` 的版本裝
   （torch 是 `2.9.1+cu128`），再跑 `setup/setup_lt.sh`（LiveTalking：https://github.com/lipku/LiveTalking）
   和 `setup/dl_models.sh`（MuseTalk 1.5 模型）。
4. Ditto 環境（`/workspace/venv_ditto`）：
   - 程式：`git clone https://github.com/antgroup/ditto-talkinghead /workspace/bakeoff/ditto`
   - 模型：Hugging Face `digital-avatar/ditto-talkinghead` 下載到 `/workspace/bakeoff/ditto/checkpoints`
     （要的檔案見 `requirements/ditto-checkpoints.txt`，用 `ditto_trt_Ampere_Plus`）
   - 跑 `bakeoff_scripts/setup_venv_ditto.sh`：TensorRT 8.6.1、cuDNN 8.9.7、onnxruntime-gpu 1.22；
     `venv_ditto` 用 `zz_base_venv.pth` 借用 `/workspace/venv` 的套件（見 `requirements/venv_ditto-pth.txt`）。
5. 主程式：在本機跑 `sh tools/runpod_sync.sh <放 id_pod 和 pod.json 的資料夾>`。
6. `bash /workspace/start_all.sh use-ditto`（或 `use-musetalk`），`curl localhost:8010/face/health` 看 `ready`。

## 要注意的地方

- 網路硬碟上不能放編譯快取（Triton 會出現「Stale file handle」）：`face_engine/run.sh` 已經改成放在 `/root`。
- Ditto 裡面用的 InsightFace 人臉偵測只能非商業使用：展示可以，正式上線要換掉。
- 機器開著才會計費（L40S 每小時約 $1.09）；關機後只剩網路硬碟的錢。餘額扣到 0 時，RunPod 可能會刪掉硬碟。
