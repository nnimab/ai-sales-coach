# 放到網路上（Vercel）

照這份做完，你會有一個自己的網址（例如 `https://ai-coach.vercel.app`），用手機或別的電腦打開就能練習。

**只限你本人使用。** 主管可以在旁邊看，但不要把網址（或通行碼）給別人自己去用：雲端版用的是你一個人的 ChatGPT 方案，OpenAI 不允許共用帳號。

## 最快的方法：雙擊 deploy.bat

1. 第一次要先裝 Vercel 的指令工具：開「命令提示字元」執行 `npm i -g vercel`（要先裝 Node.js）。
2. 雙擊 `demo` 資料夾裡的 `deploy.bat`。第一次會開瀏覽器請你登入 Vercel，也會提醒你接上資料庫（Upstash）。
3. 跑完會印出網址。**打開就能用，不用登入**：有網址的人都能用，方便展示，所以不要亂傳。
4. 改了程式，再雙擊一次就會更新（設好的亂碼不會被換掉）。

之後想加通行碼：到 Vercel → Settings → Environment Variables，把 `AUTH_MODE` 改成 `passcode`、新增 `APP_PASSCODE`（你的通行碼），再 Redeploy。注意：之後再跑 deploy.bat 會改回不用登入。
只想擋住「改設定」：新增 `ADMIN_PASSCODE`，練習照樣不用登入；要改設定時到 `網址/login` 輸入它。

下面是不用 deploy.bat、全部在網頁上點的做法（大約 30 分鐘，不用寫程式；預設要輸入通行碼）。

## 雲端版能做什麼

| | 本機版（start.bat） | 雲端版（Vercel） |
| --- | --- | --- |
| 客戶扮演、即時教練、評分 | 可以 | 可以 |
| 用你的 ChatGPT 方案當大腦 | 在這台電腦登入 | 從本機版同步一次，之後雲端自己續期 |
| Gemini 備援（ChatGPT 不能用時自動接手） | 有 | 有 |
| 即時通話（Gemini Live） | 有 | 有 |
| 真人模式（HeyGen LiveAvatar） | 有 | 有（金鑰加在環境變數） |
| 本機模型（Ollama） | 有 | 沒有（雲端沒有你的顯示卡） |
| 金鑰放哪裡 | `.env`（網頁上貼） | Vercel 的環境變數（網頁上不能貼） |
| 我的背景、自訂情境、聲音設定 | 這台電腦的檔案 | 雲端資料庫（Upstash），和本機分開 |
| 練習紀錄 | 這個瀏覽器 | 這個瀏覽器 |

## 開始前要準備

1. **GitHub 帳號**：到 https://github.com 免費註冊。另外安裝 GitHub Desktop（https://desktop.github.com）。
2. **Vercel 帳號**：到 https://vercel.com 用 GitHub 帳號登入。
   - 自己個人練習：Hobby（免費）就可以。
   - 算公司用途（公司付費、給同事用、正式訓練）：Vercel 規定要用 Pro。
3. **Gemini API 金鑰**：到 https://aistudio.google.com/apikey 按「Create API key」。免費版送出的內容 Google 會拿去改進產品，放公司資料前請先開啟付費。
4. **（要用 ChatGPT 的話）** 你自己的 ChatGPT Plus 或 Pro 帳號，而且這台電腦的本機版能正常打開。
5. **兩組亂碼**：等一下的 `APP_SECRET` 和 `CHATGPT_SYNC_KEY` 要用。
   - 按「開始」→ 輸入 `cmd` → 開啟「命令提示字元」
   - 貼上這行、按 Enter，會印出一組 43 個字的亂碼：
     ```
     python -c "import secrets; print(secrets.token_urlsafe(32))"
     ```
   - 再執行一次，拿第二組。兩組要不一樣，先存在記事本裡，不要傳給別人。
6. **通行碼**：自己想一組好記、別人猜不到的（8 個字以上），登入網站用。

## 第 1 步：放上 GitHub（一定要設成私人）

情境檔裡有隱藏設定（客戶人設、隱藏資訊），**repository 絕對不能公開。**

1. 打開 GitHub Desktop → File → Add local repository → 選 `demo` 資料夾 → Add repository。
2. 按 Publish repository。
3. 名字隨意（例如 `ai-coach`），**一定要勾「Keep this code private」**，再按 Publish repository。

`.env`、`profile.json`、`voices.json`、`scenarios/custom/`、`.chatgpt/` 不會被上傳（`.gitignore` 已經擋掉）。上傳前可以在 GitHub Desktop 左邊的清單確認沒有 `.env`。

## 第 2 步：匯入 Vercel

1. 到 https://vercel.com → Add New… → Project。
2. 在清單裡找到剛剛的 repository → Import。
3. Framework Preset 應該顯示 Python（`vercel.json` 已經指定）。其他欄位不用改。
4. **先不要按 Deploy**，展開下面的 Environment Variables，做第 3 步。

## 第 3 步：設定環境變數

在 Environment Variables 一行一行加：左邊填名字（Key），右邊填值（Value）。

| 名字 | 要填什麼 |
| --- | --- |
| `APP_MODE` | `cloud` |
| `APP_PASSCODE` | 你的通行碼 |
| `APP_SECRET` | 第一組亂碼 |
| `GEMINI_API_KEY` | Gemini 金鑰 |
| `LLM_PROVIDER` | `gemini` |
| `LIVE_ENABLED` | `1` |
| `CHATGPT_SYNC_KEY` | 第二組亂碼（要用 ChatGPT 才需要） |

選填：

| 名字 | 什麼時候要填 |
| --- | --- |
| `ADMIN_PASSCODE` | 想讓別人只能看、不能改設定時，另設一組管理者通行碼。只有你自己用就不用填 |
| `AUTH_MODE` | 填 `open`：不用登入，打開網址就能用（和 deploy.bat 一樣），這時 `APP_PASSCODE` 不用填 |
| `ELEVENLABS_API_KEY`、`YATING_API_KEY` | 要用 ElevenLabs 或雅婷的聲音 |
| `LIVEAVATAR_API_KEY` | 要用真人模式（HeyGen LiveAvatar），金鑰在 https://app.liveavatar.com/developers。加了之後到「聲音設定」打開、幫客戶挑臉 |
| `LIVEAVATAR_SANDBOX` | 填 `1`：真人模式的測試模式，不扣點數，但只能用 Wayne、大約 1～2 分鐘就結束 |
| `ALLOWED_HOSTS` | 用自己的網域時填網域，例如 `coach.example.com` |

填好按 **Deploy**，等它跑完（大約 1～2 分鐘）。

## 第 4 步：接上資料庫（Upstash Redis）

沒有資料庫也能跑，但「我的背景」、自訂情境、聲音設定和 ChatGPT 的同步，重新部署後都會不見。

1. 打開專案 → Storage → Create Database → 選 **Upstash for Redis**。
2. 區域選 **Tokyo（ap-northeast-1）**，方案選免費（Free）。
3. Connect Project：勾所有環境（Production、Preview、Development）→ Connect。
4. 到 Deployments → 最新一筆右邊的「⋯」→ **Redeploy**。

## 第 5 步：測試

1. 打開 `https://你的專案名.vercel.app`，輸入通行碼登入。
2. 左下角的燈號是綠的。
3. 左邊「AI 大腦」：上面沒有黃色的警告，文字大腦是 Gemini。
   - 有黃色警告就照上面寫的補設定，再 Redeploy。
4. 選一位客戶，試打一通電話。
5. 確認情境檔不會被直接下載：打開 `https://你的專案名.vercel.app/scenarios/01-dca-volatility.json`，應該跳回登入頁或顯示 not found。
   - 如果看到一整段客戶設定：Framework Preset 不是 Python。到專案 Settings 把 Framework Preset 改成 Python，再 Redeploy。

## 第 6 步：雲端也用你的 ChatGPT 方案

先確認第 3 步有加 `CHATGPT_SYNC_KEY`、第 4 步有接上資料庫（後來才加的話要 Redeploy）。

1. 在這台電腦執行 `start.bat`，到左邊「AI 大腦」，展開「雲端版（Vercel）」。
2. **雲端網址**：貼 `https://你的專案名.vercel.app`。
   - 要用專案的正式網址（Vercel 專案頁 Domains 列出的那個）。不要用每次部署都不一樣、很長的那種網址，那種 Vercel 預設會擋。
3. **同步密碼**：貼 `CHATGPT_SYNC_KEY` 的值（和 Vercel 上的一模一樣）。
4. 按「儲存並測試」，看到「連上雲端了」。
5. 按「登入並同步到雲端」→ 用你自己的 Plus 或 Pro 帳號登入、按同意 → 回到本機畫面，顯示「已同步到雲端」。
6. 打開雲端版的「AI 大腦」：ChatGPT 卡片會顯示你的 email 和「使用 ChatGPT 方案」，文字大腦自動換成 ChatGPT。

這是怎麼運作的：本機用「雲端那台的身分」幫雲端登入一次，拿到的憑證馬上用 HTTPS 加密傳給雲端，這台電腦不留那一份；之後雲端自己續期。這是 OpenAI 文件「Self-hosted VMs」寫的做法。本機自己的 ChatGPT 登入是另一份，兩邊互不影響。

之後：
- 雲端顯示「登入過期」：回本機版再按一次「登入並同步到雲端」（大約 10 秒）。OpenAI 目前可能有續期的問題，有時大約每小時就要重做一次。
- ChatGPT 用量到上限、登入過期或暫時不能用時，**Gemini 會自動接手**，練習不會中斷。
- 不想讓雲端再用你的 ChatGPT：雲端版「AI 大腦」→「清除雲端的 ChatGPT 憑證」。

## 真人版（RunPod）的固定網址

RunPod 的 Pod 重建後網址會換。手機一律開 `https://你的專案名.vercel.app/live`：

- 真人版開著：直接轉到現在那台 Pod。
- 真人版沒開（15 分鐘沒收到 Pod 的回報）：顯示「真人版目前沒有開」，可以按一下改用一般版。
- 這個網址不用登入、不用通行碼。

要打開這個功能：

1. 想一組夠長的密碼（例如 40 個以上的英數字），當作 `LIVE_LINK_SECRET`。
2. Vercel：加環境變數 `LIVE_LINK_SECRET`（就是那組密碼），然後 Redeploy。
3. RunPod 的 Pod 設定（Environment Variables）：
   - `LIVE_LINK_SECRET`：同一組密碼
   - `LIVE_LINK_TARGET`：`https://你的專案名.vercel.app`
4. Pod 開機時 `start_all.sh` 會一起開「固定入口回報」（`tools/runpod/live_link.py`），每 5 分鐘把 Pod 的網址報給 Vercel；用 `sh tools/runpod_sync.sh` 同步程式時也會重開它。
   - 看狀態：`bash /workspace/start_all.sh status`；記錄在 `/workspace/logs/link.log`。
   - 手動送一次：`python3 /workspace/app/tools/runpod/live_link.py --once`

回報要用這組密碼簽名，而且只接受 `https://<Pod 代號>-8765.proxy.runpod.net` 這種網址，別人沒辦法把 `/live` 轉到別的網站。

## 之後要改東西

- **改程式**：在 GitHub Desktop 按 Commit，再按 Push origin。Vercel 會自動重新部署。
- **改環境變數**：改完一定要到 Deployments →「⋯」→ Redeploy 才會生效。
- **用自己的網域**：專案 → Settings → Domains 加網域，再加環境變數 `ALLOWED_HOSTS=你的網域`，然後 Redeploy。
- **本機的自訂情境和「我的背景」不會跟著上去**：雲端版要自己再建一次（存在 Upstash）。

## 常見問題

| 看到什麼 | 怎麼辦 |
| --- | --- |
| 登入頁寫「網站還沒設定好」 | 照清單補環境變數，再 Redeploy |
| 「AI 大腦」有黃色警告「資料庫還沒接上」 | 做第 4 步 |
| 一直跳回登入頁 | 改過 `APP_PASSCODE` 或 `APP_SECRET` 後要重新登入，這是正常的 |
| 改了 `APP_SECRET` 之後 ChatGPT 變成沒同步 | 雲端的憑證是用它加密的，換了就讀不到：回本機再同步一次 |
| 同步時說「同步密碼不對」 | 本機和 Vercel 的 `CHATGPT_SYNC_KEY` 要一模一樣；Vercel 改過要 Redeploy |
| 同步時說「雲端還沒設定 CHATGPT_SYNC_KEY」 | 到 Vercel 加上它，再 Redeploy |
| 同步時說「雲端擋住了這個請求」 | 確認貼的是正式網址（第 6 步第 2 點）；或到 Settings → Deployment Protection 確認正式網址沒有被擋 |
| 「聲音設定」不能貼金鑰 | 雲端版的金鑰要加在 Vercel 的環境變數，畫面上會寫要加哪一個名字 |
| 客戶回話很慢、常說太忙 | Gemini 免費版尖峰時常塞車，到 AI Studio 開啟付費 |

給技術人員：專案附的 `tools/smoke.py` 可以自動檢查部署，用法是 `python tools/smoke.py https://你的專案名.vercel.app 你的通行碼`；不用登入的網站（`AUTH_MODE=open`）改成 `python tools/smoke.py https://你的專案名.vercel.app --open`。

## 費用參考

- **Vercel**：Hobby 免費（只限個人、非商業）；Pro 每位成員每月 US$20。
- **Upstash**：免費額度每月 50 萬次指令、256 MB。免費資料庫 30 天沒用會被封存，久沒練習要先到 Upstash 恢復。
- **Gemini**：用多少付多少。
- **ChatGPT**：用你 Plus／Pro 方案裡的額度，不另外收 API 費用。Plus 有 5 小時的用量上限，用完由 Gemini 接手。

## 以後：OpenAI 核准的雲端登入（選用）

現在的做法是從本機同步。如果 OpenAI 之後核准你的網站直接用 ChatGPT 登入：

1. 先到 https://openai.com/form/sign-in-with-chatgpt-interest/ 申請，選「Sign in and ChatGPT plan use」。
2. 核准後在 Vercel 加：
   - `CHATGPT_MODE=hosted`
   - `CHATGPT_CLIENT_ID=`（OpenAI 給的）
   - `CHATGPT_CLIENT_SECRET=`（有給才填）
   - `CHATGPT_REDIRECT_URI=https://你的網域/auth/callback`
3. Redeploy。之後在雲端版「AI 大腦」直接按「Continue with ChatGPT」登入，不用再從本機同步。

## 其他設定（通常不用改）

| 名字 | 預設 | 作用 |
| --- | --- | --- |
| `SESSION_DAYS` | `180` | 登入後多久要重新輸入通行碼 |
| `PROFILE_SCOPE` | `user` | `user` = 每個瀏覽器各一份「我的背景」；`shared` = 你的手機和電腦共用同一份 |
| `PROFILE_LOCKED` | 空白 | `1` = 考核期間不能改「我的背景」 |
| `BRAIN_BACKUPS` | `gemini,ollama` | ChatGPT 不能用時依序改用誰（雲端不會用本機模型） |
| `TTS_CACHE_TTL_DAYS` | `30` | 開場白語音在資料庫留幾天 |
| `STORE_PREFIX` | `aicoach:` | 同一個 Upstash 放好幾個網站時，用來分開 |
