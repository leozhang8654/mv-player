#!/bin/bash
# 构建可分发的 macOS 安装包:dist/MVPlayer-macOS-AppleSilicon.dmg 或 MVPlayer-macOS-Intel.dmg
# (文件名不带版本号,README 里的「下载最新版」链接才能一直有效;版本号写在 App 信息里)
#
# 用法: packaging/build_macos.sh [版本号]      (默认 0.0.0-dev)
# 构建机是什么架构就打什么架构(Apple Silicon → arm64,Intel → x86_64)。
# 需要:python3、Xcode 命令行工具;会联网下载 yt-dlp / ffmpeg / ffprobe / deno。
set -euo pipefail

VERSION="${1:-0.0.0-dev}"
VERSION="${VERSION#v}"
ARCH="$(uname -m)"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$ROOT/build/macos-$ARCH"
DIST="$ROOT/dist"
APP="$WORK/MV播放器.app"
PYTHON="${PYTHON:-python3}"

case "$ARCH" in
  arm64)  FF_ARCH=arm64; DENO_ARCH=aarch64; LABEL=AppleSilicon ;;
  x86_64) FF_ARCH=amd64; DENO_ARCH=x86_64;  LABEL=Intel ;;
  *) echo "不支持的架构: $ARCH"; exit 1 ;;
esac

rm -rf "$WORK"
mkdir -p "$WORK" "$DIST"

echo "▸ 1/6 打包 Python 后端(PyInstaller)"
"$PYTHON" -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install -q --upgrade pip
"$WORK/venv/bin/pip" install -q -r "$ROOT/requirements.txt" pyinstaller
"$WORK/venv/bin/pyinstaller" --noconfirm --clean --log-level WARN \
    --name MVPlayerServer --onedir --console \
    --distpath "$WORK/pyi-dist" --workpath "$WORK/pyi-build" --specpath "$WORK" \
    --paths "$ROOT" \
    --add-data "$ROOT/static:static" \
    --collect-data opencc \
    --hidden-import app --hidden-import catalog --hidden-import downloader --hidden-import paths \
    "$ROOT/launcher.py"
BACKEND="$WORK/pyi-dist/MVPlayerServer"
BIN="$BACKEND/bin"
mkdir -p "$BIN"

echo "▸ 2/6 下载附带工具"
# 构建机偶尔会 DNS 解析失败:所有错误都重试,间隔拉长
fetch() { curl -fsSL --retry 6 --retry-delay 15 --retry-all-errors -o "$1" "$2"; }
fetch "$BIN/yt-dlp" "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos"
for tool in ffmpeg ffprobe; do
    fetch "$WORK/$tool.zip" "https://ffmpeg.martin-riedl.de/redirect/latest/macos/$FF_ARCH/release/$tool.zip"
    unzip -o -q "$WORK/$tool.zip" -d "$BIN"
done
fetch "$WORK/deno.zip" "https://github.com/denoland/deno/releases/latest/download/deno-$DENO_ARCH-apple-darwin.zip"
unzip -o -q "$WORK/deno.zip" -d "$BIN"
chmod +x "$BIN"/*

echo "▸ 3/6 自检"
"$BACKEND/MVPlayerServer" --smoke --data-dir "$WORK/smoke-data"

echo "▸ 4/6 编译原生窗口外壳"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O -o "$WORK/makeicon" "$ROOT/desktop/Sources/MakeIcon.swift"
"$WORK/makeicon" "$WORK/AppIcon.iconset" >/dev/null
iconutil -c icns "$WORK/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"
swiftc -O -target "$ARCH-apple-macos12.0" \
    -framework Cocoa -framework WebKit \
    -o "$APP/Contents/MacOS/MVPlayer" \
    "$ROOT/desktop/Sources/MVPlayer.swift" "$ROOT/desktop/Sources/AppDelegate.swift" "$ROOT/desktop/Sources/main.swift"
cp -R "$BACKEND" "$APP/Contents/Resources/backend"
cp "$ROOT/THIRD_PARTY_NOTICES.md" "$APP/Contents/Resources/"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>                 <string>MV播放器</string>
    <key>CFBundleDisplayName</key>          <string>MV 播放器</string>
    <key>CFBundleExecutable</key>           <string>MVPlayer</string>
    <key>CFBundleIdentifier</key>           <string>io.github.leozhang8654.mvplayer</string>
    <key>CFBundleIconFile</key>             <string>AppIcon</string>
    <key>CFBundlePackageType</key>          <string>APPL</string>
    <key>CFBundleShortVersionString</key>   <string>$VERSION</string>
    <key>CFBundleVersion</key>              <string>$VERSION</string>
    <key>CFBundleDevelopmentRegion</key>    <string>zh_CN</string>
    <key>NSPrincipalClass</key>             <string>NSApplication</string>
    <key>NSHighResolutionCapable</key>      <true/>
    <key>LSMinimumSystemVersion</key>       <string>12.0</string>
    <key>NSHumanReadableCopyright</key>     <string>仅供个人学习与使用</string>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsLocalNetworking</key>            <true/>
        <key>NSAllowsArbitraryLoadsInWebContent</key> <true/>
    </dict>
    <key>NSLocalNetworkUsageDescription</key>
    <string>用于访问本机上的 MV 播放器服务,以及让同一 Wi-Fi 下的手机访问。</string>
    <key>NSSupportsAutomaticTermination</key> <false/>
    <key>NSSupportsSuddenTermination</key>    <false/>
</dict>
</plist>
PLIST

echo "▸ 5/6 签名(ad-hoc)"
codesign --force --deep --sign - --timestamp=none "$APP"
codesign --verify --deep --strict "$APP"

echo "▸ 6/6 制作 DMG(带背景与拖拽箭头的安装窗口)"
"$WORK/venv/bin/pip" install -q dmgbuild
DMG="$DIST/MVPlayer-macOS-$LABEL.dmg"
rm -f "$DMG"
"$WORK/venv/bin/dmgbuild" -s "$ROOT/packaging/dmg/settings.py" \
    -D app="$APP" \
    -D readme="$ROOT/packaging/dmg/首次打开必读.txt" \
    -D background="$ROOT/packaging/dmg/background.png" \
    "MV 播放器" "$DMG"
echo "✓ $DMG ($(du -h "$DMG" | cut -f1))"
