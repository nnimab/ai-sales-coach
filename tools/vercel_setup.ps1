# 一鍵部署到 Vercel：設定環境變數 → 部署正式版 → 把雲端網址和同步密碼寫回本機 .env → 自動檢查
# 由 deploy.bat 執行；可以重複跑，已經設過的亂碼不會被換掉（換掉會讓雲端的 ChatGPT 憑證讀不到）
# 網站不用登入（AUTH_MODE=open）：打開網址就能練習，有網址的人都能用（展示用）
# -Auto：完全不問問題（給排程或別的程式用）；沒接 Upstash 只警告，本機和雲端都沒有 Gemini 金鑰就停下來
# 結束代碼：0 = 完成；1 = 沒有部署成功；2 = 部署好了，但自動檢查有沒過的項目
param([switch]$Auto)

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$Project = 'ai-sales-coach'
$Team = 'h23881956s-projects'

function Say($text) { Write-Host $text }
function Warn($text) { Write-Host $text -ForegroundColor Yellow }
function Fail($text) { Write-Host ''; Write-Host $text -ForegroundColor Red; exit 1 }

# 跑 vercel 指令，stdout 和 stderr 一起收（交給 cmd 合併，PowerShell 5.1 才不會把 stderr 當錯誤）
function Vercel([string]$argline) {
    $out = cmd /c "vercel $argline 2>&1"
    return @{ code = $LASTEXITCODE; text = ($out -join "`n") }
}

function LocalEnv([string]$name) {
    if (-not (Test-Path .env)) { return '' }
    foreach ($line in Get-Content .env -Encoding UTF8) {
        if ($line -match "^\s*$name\s*=(.*)$") {
            return ($Matches[1] -split ' #', 2)[0].Trim().Trim('"').Trim("'")
        }
    }
    return ''
}

function SaveLocalEnv([string]$name, [string]$value) {
    $env:AIC_SET_NAME = $name
    $env:AIC_SET_VALUE = $value
    python -c "import os; from storage import set_env_value; set_env_value('.env', os.environ['AIC_SET_NAME'], os.environ['AIC_SET_VALUE'])"
    $code = $LASTEXITCODE
    Remove-Item Env:AIC_SET_NAME, Env:AIC_SET_VALUE
    if ($code -ne 0) { Fail "寫入本機 .env 失敗：$name" }
}

# 值寫進暫存檔再餵給 vercel（不經過命令列、不多一個換行）
function SetCloudEnv([string]$name, [string]$value, [switch]$Sensitive) {
    $tmp = [IO.Path]::GetTempFileName()
    [IO.File]::WriteAllText($tmp, $value)
    $flag = if ($Sensitive) { ' --sensitive' } else { '' }
    $out = cmd /c "vercel env add $name production --force$flag < `"$tmp`" 2>&1"
    $code = $LASTEXITCODE
    Remove-Item $tmp -Force
    if ($code -ne 0) { Fail ("設定 $name 失敗：`n" + ($out -join "`n")) }
    Say "  已設定 $name"
}

function NewSecret {
    $s = python -c "import secrets; print(secrets.token_urlsafe(32))"
    if ($LASTEXITCODE -ne 0 -or -not $s) { Fail '產生亂碼失敗：請確認這台電腦有安裝 Python' }
    return ([string]$s).Trim()
}

# 讀不到雲端的變數清單就停：不然會以為亂碼還沒設，把已經設好的換掉
function CloudNames {
    $r = Vercel 'env ls production'
    if ($r.code -ne 0) { Fail ("讀不到 Vercel 的環境變數，先停下來（免得把設好的亂碼換掉）：`n" + $r.text) }
    return $r.text
}

Say '== 1. 檢查 Vercel 登入'
$who = Vercel 'whoami'
if ($who.code -ne 0) {
    if ($Auto) { Fail '還沒登入 Vercel：請先雙擊 deploy.bat 登入一次（或執行 vercel login）' }
    Say '還沒登入 Vercel，接下來會開瀏覽器讓你登入'
    cmd /c 'vercel login'
    $who = Vercel 'whoami'
    if ($who.code -ne 0) { Fail '登入 Vercel 沒有成功，請再執行一次' }
}
Say ('  登入身分：' + ($who.text -split "`n" | Select-Object -Last 1))

if (-not (Test-Path .vercel\project.json)) {
    Say "== 連到 Vercel 專案 $Project"
    $r = Vercel "link --yes --project $Project"
    if ($r.code -ne 0) { Fail ("連結專案失敗：`n" + $r.text) }
}

Say '== 2. 檢查雲端資料庫（Upstash Redis）'
$names = CloudNames
$storePattern = '(?m)^\s*(KV_REST_API_URL|UPSTASH_REDIS_REST_URL)\s'
$hasStore = $names -match $storePattern
while (-not $hasStore -and -not $Auto) {
    Say ''
    Say '  還沒接上資料庫。沒有它，ChatGPT 同步和你的設定在重新部署後都會不見。'
    Say '  請在瀏覽器做（接下來會自動打開專案頁）：'
    Say '    Storage → Create Database → Upstash for Redis'
    Say '    區域選 Tokyo（ap-northeast-1）、方案選 Free → 同意條款 → Connect（勾 Production）'
    Start-Process "https://vercel.com/$Team/$Project/stores"
    $ans = Read-Host '  弄好按 Enter；先不接資料庫就輸入 s 再按 Enter'
    if ($ans -eq 's') { break }
    $names = CloudNames
    $hasStore = $names -match $storePattern
}
if (-not $hasStore) {
    Warn '  警告：還沒接上 Upstash 資料庫，先照樣部署。設定、自訂情境和 ChatGPT 同步在重新部署後會不見。'
    Warn "  之後要接：https://vercel.com/$Team/$Project/stores → Create Database → Upstash for Redis，再執行一次 deploy.bat"
}

Say '== 3. 設定環境變數'
function OnCloud([string]$name) { return $names -match "(?m)^\s*$name\s" }

# 不用登入：打開網址就能用。以前設的通行碼拿掉（open 模式本來就不看它，留著只會讓人以為還有在擋）
SetCloudEnv 'AUTH_MODE' 'open'
if (OnCloud 'APP_PASSCODE') {
    $r = Vercel 'env rm APP_PASSCODE production --yes'
    if ($r.code -eq 0) { Say '  已拿掉 APP_PASSCODE（網站不用登入了）' }
    else { Warn ("  拿不掉 APP_PASSCODE（不影響使用，open 模式不看它）：`n" + $r.text) }
}
if (OnCloud 'ADMIN_PASSCODE') { Say '  雲端有 ADMIN_PASSCODE：練習不用登入；要改設定，先到「網址/login」輸入它' }

# 簽 cookie、加密 ChatGPT 憑證的亂碼：已經有就絕對不換
if (-not (OnCloud 'APP_SECRET')) { SetCloudEnv 'APP_SECRET' (NewSecret) -Sensitive }

# 同步密碼本機和雲端要一樣。雲端已經有就不換（讀不回來，換了本機同步會失敗）
$syncKey = LocalEnv 'CHATGPT_SYNC_KEY'
if (-not (OnCloud 'CHATGPT_SYNC_KEY')) {
    $newKey = -not $syncKey
    if ($newKey) { $syncKey = NewSecret }
    SetCloudEnv 'CHATGPT_SYNC_KEY' $syncKey -Sensitive
    if ($newKey) { SaveLocalEnv 'CHATGPT_SYNC_KEY' $syncKey }
} elseif (-not $syncKey) {
    Warn '  雲端已經有 CHATGPT_SYNC_KEY，本機 .env 卻沒有：這次不換。要從本機同步 ChatGPT，請在本機「雲端版」填同一組同步密碼'
}

$gemini = LocalEnv 'GEMINI_API_KEY'
if (-not $gemini -and -not (OnCloud 'GEMINI_API_KEY')) {
    if ($Auto) { Fail '本機 .env 和 Vercel 都沒有 GEMINI_API_KEY：雲端一定要有 Gemini 金鑰（備援大腦和即時通話都靠它）' }
    $g = Read-Host '  本機 .env 沒有 Gemini 金鑰，請貼上（AI Studio → Get API key）' -AsSecureString
    $gemini = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($g)).Trim()
    if (-not $gemini) { Fail '雲端一定要有 Gemini 金鑰（備援大腦和即時通話都靠它）' }
}
if ($gemini) { SetCloudEnv 'GEMINI_API_KEY' $gemini -Sensitive }

# 固定的設定：沒設過才設
$fixed = [ordered]@{ APP_MODE = 'cloud'; LIVE_ENABLED = '1' }
if (-not (OnCloud 'LLM_PROVIDER')) { $fixed['LLM_PROVIDER'] = 'gemini' }  # 同步 ChatGPT 後會自動換成 ChatGPT
foreach ($k in $fixed.Keys) { if (-not (OnCloud $k)) { SetCloudEnv $k $fixed[$k] } }
# 只有你自己用：「我的背景」手機和電腦共用一份（每次都設，確定是 shared）
SetCloudEnv 'PROFILE_SCOPE' 'shared'

# 本機有設的聲音和模型設定，雲端照抄，聽起來才會一樣
foreach ($k in 'TTS_PROVIDER', 'GEMINI_TTS_MODEL', 'GEMINI_MODEL', 'GEMINI_COACH_MODEL', 'GEMINI_COACH_REASONING', 'ELEVENLABS_MODEL', 'LIVEAVATAR_SANDBOX') {
    $v = LocalEnv $k
    if ($v) { SetCloudEnv $k $v }
}
foreach ($k in 'ELEVENLABS_API_KEY', 'YATING_API_KEY', 'LIVEAVATAR_API_KEY') {
    $v = LocalEnv $k
    if ($v) { SetCloudEnv $k $v -Sensitive }
}
# 真人版固定入口（/live）：RunPod 的 Pod 回報網址時用的簽名金鑰。本機沒有就產生一個存進 .env；Pod 的環境變數要設同一個
$liveLink = LocalEnv 'LIVE_LINK_SECRET'
if (-not $liveLink) { $liveLink = NewSecret; SaveLocalEnv 'LIVE_LINK_SECRET' $liveLink }
SetCloudEnv 'LIVE_LINK_SECRET' $liveLink -Sensitive
# 真人模式（HeyGen LiveAvatar）是雲端版的真人備案：有金鑰就打開（畫面上的開關還是可以關）
if ((LocalEnv 'LIVEAVATAR_API_KEY') -or (OnCloud 'LIVEAVATAR_API_KEY')) { SetCloudEnv 'LIVEAVATAR_ENABLED' '1' }

Say '== 4. 部署正式版（大約 1～2 分鐘）'
$r = Vercel 'deploy --prod --yes'
Say $r.text
if ($r.code -ne 0) { Fail '部署失敗，上面是 Vercel 的訊息' }
# 要用專案的正式網址：每次部署各自的長網址會被 Vercel 的 Deployment Protection 擋住，同步會失敗
$deployUrl = ''
if ($r.text -match 'Production:\s*(https://[^\s\[]+)') { $deployUrl = $Matches[1].TrimEnd('/') }
$seen = $r.text
if ($deployUrl) { $seen += "`n" + (Vercel "inspect $deployUrl").text }
$aliases = [regex]::Matches($seen, 'https://[a-z0-9.-]+\.vercel\.app') | ForEach-Object { $_.Value } |
    Where-Object { $_ -ne $deployUrl } | Select-Object -Unique | Sort-Object Length
$url = @($aliases)[0]
if (-not $url) { $url = $deployUrl; Say '  找不到專案的正式網址，先用這次部署的網址（同步可能會被擋，請到專案頁 Domains 確認）' }
if (-not $url) { Fail '找不到網址，請到 Vercel 專案頁看 Domains' }
SaveLocalEnv 'CLOUD_URL' $url
Say "  網址：$url（已寫進本機 .env 的 CLOUD_URL）"

Say '== 5. 自動檢查'
python tools/smoke.py $url --open
$smoke = $LASTEXITCODE

Say ''
Say '== 完成'
Say "  1. 用手機或瀏覽器打開 $url ，不用登入就能練習（有網址的人都能用，不要亂傳）"
Say '  2. 要讓雲端也用 ChatGPT：重開 start.bat → 左邊「AI 大腦」→「雲端版（Vercel）」'
Say '     網址和同步密碼已經填好，直接按「登入並同步到雲端」，用自己的帳號登入'
if (-not $hasStore) { Warn '  提醒：還沒接 Upstash，設定和 ChatGPT 同步在重新部署後會不見（上面的 V7 會沒過）' }
if ($smoke -ne 0) {
    Warn '  自動檢查有沒過的項目，請看上面標 FAIL 的那幾行'
    exit 2
}
exit 0
