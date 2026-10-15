# 一鍵部署到 Vercel：設定環境變數 → 部署正式版 → 把雲端網址和同步密碼寫回本機 .env → 自動檢查
# 由 deploy.bat 執行；可以重複跑，已經設過的亂碼不會被換掉（換掉會讓雲端的 ChatGPT 憑證讀不到）

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$Project = 'ai-sales-coach'
$Team = 'h23881956s-projects'

function Say($text) { Write-Host $text }
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

function NewSecret { python -c "import secrets; print(secrets.token_urlsafe(32))" }

Say '== 1. 檢查 Vercel 登入'
$who = Vercel 'whoami'
if ($who.code -ne 0) {
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
function CloudNames { (Vercel 'env ls production').text }
$names = CloudNames
while ($names -notmatch '(?m)^\s*(KV_REST_API_URL|UPSTASH_REDIS_REST_URL)\s') {
    Say ''
    Say '  還沒接上資料庫。沒有它，ChatGPT 同步和你的設定在重新部署後都會不見。'
    Say '  請在瀏覽器做（接下來會自動打開專案頁）：'
    Say '    Storage → Create Database → Upstash for Redis'
    Say '    區域選 Tokyo（ap-northeast-1）、方案選 Free → 同意條款 → Connect（勾 Production）'
    Start-Process "https://vercel.com/$Team/$Project/stores"
    $ans = Read-Host '  弄好按 Enter；先不接資料庫就輸入 s 再按 Enter'
    if ($ans -eq 's') { break }
    $names = CloudNames
}

Say '== 3. 設定環境變數'
function OnCloud([string]$name) { return $names -match "(?m)^\s*$name\s" }

if (-not (OnCloud 'APP_PASSCODE')) {
    $want = $true
} else {
    $want = (Read-Host '  已經有通行碼了，要換一組嗎？(y/N)') -eq 'y'
}
if ($want) {
    while ($true) {
        $s1 = Read-Host '  請設定登入網站用的通行碼（8 個字以上，輸入時不會顯示）' -AsSecureString
        $s2 = Read-Host '  再輸入一次' -AsSecureString
        $p1 = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s1))
        $p2 = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s2))
        if ($p1 -ne $p2) { Say '  兩次不一樣，再來一次'; continue }
        if ($p1.Trim().Length -lt 8) { Say '  太短了，至少 8 個字'; continue }
        break
    }
    SetCloudEnv 'APP_PASSCODE' $p1.Trim() -Sensitive
    $passcode = $p1.Trim()
} else {
    $passcode = ''
}

if (-not (OnCloud 'APP_SECRET')) { SetCloudEnv 'APP_SECRET' (NewSecret) -Sensitive }

# 同步密碼本機和雲端要一樣；本機沒有的話（雲端那份讀不回來）就兩邊一起換
$syncKey = LocalEnv 'CHATGPT_SYNC_KEY'
if (-not $syncKey -or -not (OnCloud 'CHATGPT_SYNC_KEY')) {
    if (-not $syncKey) { $syncKey = NewSecret }
    SetCloudEnv 'CHATGPT_SYNC_KEY' $syncKey -Sensitive
    SaveLocalEnv 'CHATGPT_SYNC_KEY' $syncKey
}

$gemini = LocalEnv 'GEMINI_API_KEY'
if (-not $gemini -and -not (OnCloud 'GEMINI_API_KEY')) {
    $g = Read-Host '  本機 .env 沒有 Gemini 金鑰，請貼上（AI Studio → Get API key）' -AsSecureString
    $gemini = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($g)).Trim()
    if (-not $gemini) { Fail '雲端一定要有 Gemini 金鑰（備援大腦和即時通話都靠它）' }
}
if ($gemini) { SetCloudEnv 'GEMINI_API_KEY' $gemini -Sensitive }

# 固定的設定：只有你自己用，所以「我的背景」手機和電腦共用一份
$fixed = [ordered]@{ APP_MODE = 'cloud'; LIVE_ENABLED = '1'; PROFILE_SCOPE = 'shared' }
if (-not (OnCloud 'LLM_PROVIDER')) { $fixed['LLM_PROVIDER'] = 'gemini' }  # 同步 ChatGPT 後會自動換成 ChatGPT
foreach ($k in $fixed.Keys) { if (-not (OnCloud $k)) { SetCloudEnv $k $fixed[$k] } }

# 本機有設的聲音和模型設定，雲端照抄，聽起來才會一樣
foreach ($k in 'TTS_PROVIDER', 'GEMINI_TTS_MODEL', 'GEMINI_MODEL', 'GEMINI_COACH_MODEL', 'GEMINI_COACH_REASONING', 'ELEVENLABS_MODEL', 'LIVEAVATAR_SANDBOX') {
    $v = LocalEnv $k
    if ($v) { SetCloudEnv $k $v }
}
foreach ($k in 'ELEVENLABS_API_KEY', 'YATING_API_KEY', 'LIVEAVATAR_API_KEY') {
    $v = LocalEnv $k
    if ($v) { SetCloudEnv $k $v -Sensitive }
}

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
if ($passcode) {
    # 通行碼走環境變數，避開 PowerShell 5.1 傳參數時引號、空白的問題
    $env:AIC_PASSCODE = $passcode
    python -c "import os, subprocess, sys; sys.exit(subprocess.call([sys.executable, 'tools/smoke.py', sys.argv[1], os.environ['AIC_PASSCODE']]))" $url
    Remove-Item Env:AIC_PASSCODE
} else {
    Say '  這次沒有換通行碼，略過自動檢查（要檢查就執行：python tools/smoke.py 網址 通行碼）'
}

Say ''
Say '== 完成'
Say "  1. 用手機或瀏覽器打開 $url ，輸入通行碼"
Say '  2. 要讓雲端也用 ChatGPT：重開 start.bat → 左邊「AI 大腦」→「雲端版（Vercel）」'
Say '     網址和同步密碼已經填好，直接按「登入並同步到雲端」，用自己的帳號登入'
