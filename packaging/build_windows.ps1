# 构建 Windows 安装包(在 Windows 上用 PowerShell 7 运行):
#   dist\MVPlayer-Windows-x64-Setup.exe   安装版(需要 Inno Setup 6)
#   dist\MVPlayer-Windows-x64.zip         便携版(解压即用)
# 文件名不带版本号,README 里的「下载最新版」链接才能一直有效
#
# 用法: pwsh packaging\build_windows.ps1 -Version 1.0.0
# 需要: Python 3.10+;会联网下载 yt-dlp / ffmpeg / ffprobe / deno。
param([string]$Version = "0.0.0-dev")
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # 进度条会让 Invoke-WebRequest 慢几十倍

$Version = $Version.TrimStart("v")
$Root = Split-Path -Parent $PSScriptRoot
$Work = Join-Path $Root "build\windows"
$Dist = Join-Path $Root "dist"
$Python = if ($env:PYTHON) { $env:PYTHON } else { "python" }

function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what 失败(退出码 $LASTEXITCODE)" } }
function Fetch($url, $out) {
    for ($i = 1; $i -le 6; $i++) {
        try { Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing; return }
        catch { if ($i -eq 6) { throw } ; Start-Sleep -Seconds 15 }
    }
}

if (Test-Path $Work) { Remove-Item -Recurse -Force $Work }
New-Item -ItemType Directory -Force -Path $Work, $Dist | Out-Null

Write-Host "▸ 1/5 打包 Python 后端(PyInstaller)"
& $Python -m venv "$Work\venv"; Check "创建虚拟环境"
$Py = "$Work\venv\Scripts\python.exe"
& $Py -m pip install -q --upgrade pip; Check "升级 pip"
& $Py -m pip install -q -r "$Root\requirements.txt" pyinstaller; Check "安装依赖"
& $Py -m PyInstaller --noconfirm --clean --log-level WARN `
    --name MVPlayer --onedir --console --icon "$Root\packaging\icon.ico" `
    --distpath "$Work\pyi-dist" --workpath "$Work\pyi-build" --specpath "$Work" `
    --paths "$Root" --add-data "$Root\static;static" --collect-data opencc `
    --hidden-import app --hidden-import catalog --hidden-import downloader --hidden-import paths `
    "$Root\launcher.py"
Check "PyInstaller"
$App = "$Work\pyi-dist\MVPlayer"
$Bin = "$App\bin"
New-Item -ItemType Directory -Force -Path $Bin | Out-Null

Write-Host "▸ 2/5 下载附带工具"
Fetch "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" "$Bin\yt-dlp.exe"
Fetch "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" "$Work\ffmpeg.zip"
Expand-Archive -Path "$Work\ffmpeg.zip" -DestinationPath "$Work\ffmpeg" -Force
$FFBin = Get-ChildItem -Path "$Work\ffmpeg" -Recurse -Filter ffmpeg.exe | Select-Object -First 1
Copy-Item (Join-Path $FFBin.DirectoryName "ffmpeg.exe") $Bin
Copy-Item (Join-Path $FFBin.DirectoryName "ffprobe.exe") $Bin
Fetch "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip" "$Work\deno.zip"
Expand-Archive -Path "$Work\deno.zip" -DestinationPath $Bin -Force
Copy-Item "$Root\THIRD_PARTY_NOTICES.md" $App
@"
MV 播放器 $Version

双击 MVPlayer.exe 启动,会自动在浏览器里打开播放器(http://127.0.0.1:8471)。
关闭黑色的命令行窗口即退出;下载到一半的歌下次启动会继续。

曲库和下载的视频保存在:%USERPROFILE%\Videos\MV播放器
首次启动如果 Windows 防火墙询问,允许「专用网络」即可让同一 Wi-Fi 的手机访问。

项目主页:https://github.com/leozhang8654/mv-player
"@ | Out-File -Encoding utf8 "$App\使用说明.txt"

Write-Host "▸ 3/5 自检"
& "$App\MVPlayer.exe" --smoke --data-dir "$Work\smoke-data"; Check "自检"

Write-Host "▸ 4/5 便携版 zip"
$Zip = "$Dist\MVPlayer-Windows-x64.zip"
if (Test-Path $Zip) { Remove-Item $Zip }
Compress-Archive -Path $App -DestinationPath $Zip -CompressionLevel Optimal

Write-Host "▸ 5/5 安装版(Inno Setup)"
$Iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $Iscc) {
    foreach ($p in "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                   "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") {
        if (Test-Path $p) { $Iscc = $p; break }
    }
}
if ($Iscc) {
    & $Iscc /Q "/DAppVersion=$Version" "/DSourceDir=$App" "/DOutputDir=$Dist" `
        "/DIconFile=$Root\packaging\icon.ico" "$Root\packaging\windows\installer.iss"
    Check "Inno Setup"
} else {
    Write-Warning "没找到 Inno Setup,跳过安装版(choco install innosetup 可安装)"
}
Get-ChildItem $Dist | ForEach-Object { Write-Host ("✓ {0} ({1:N0} MB)" -f $_.Name, ($_.Length / 1MB)) }
