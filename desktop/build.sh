#!/bin/bash
# 把 MV 播放器打包成 macOS App。用法: desktop/build.sh [安装目录]
# 不带参数只在 desktop/build/ 里生成,带参数则额外拷一份过去(如 /Applications)。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
BUILD="$HERE/build"
APP="$BUILD/MV播放器.app"
BIN_NAME="MVPlayer"

rm -rf "$BUILD"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

echo "▸ 生成图标"
swiftc -O -o "$BUILD/makeicon" "$HERE/Sources/MakeIcon.swift"
"$BUILD/makeicon" "$BUILD/AppIcon.iconset" >/dev/null
iconutil -c icns "$BUILD/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"

echo "▸ 编译"
swiftc -O -target arm64-apple-macos12.0 \
    -framework Cocoa -framework WebKit \
    -o "$APP/Contents/MacOS/$BIN_NAME" \
    "$HERE/Sources/MVPlayer.swift" "$HERE/Sources/AppDelegate.swift" "$HERE/Sources/main.swift"

echo "▸ 写 Info.plist"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>                 <string>MV播放器</string>
    <key>CFBundleDisplayName</key>          <string>MV 播放器</string>
    <key>CFBundleExecutable</key>           <string>$BIN_NAME</string>
    <key>CFBundleIdentifier</key>           <string>com.leozhang.mvplayer</string>
    <key>CFBundleIconFile</key>             <string>AppIcon</string>
    <key>CFBundlePackageType</key>          <string>APPL</string>
    <key>CFBundleShortVersionString</key>   <string>1.0</string>
    <key>CFBundleVersion</key>              <string>1</string>
    <key>CFBundleDevelopmentRegion</key>    <string>zh_CN</string>
    <key>NSPrincipalClass</key>             <string>NSApplication</string>
    <key>NSHighResolutionCapable</key>      <true/>
    <key>LSMinimumSystemVersion</key>       <string>12.0</string>
    <key>NSHumanReadableCopyright</key>     <string>本地播放器,仅供个人使用</string>
    <!-- 项目目录:App 靠它找到 app.py 和 .venv -->
    <key>MVProjectRoot</key>                <string>$ROOT</string>
    <!-- 允许访问 http://127.0.0.1:8471 这种本地明文地址 -->
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsLocalNetworking</key>            <true/>
        <key>NSAllowsArbitraryLoadsInWebContent</key> <true/>
    </dict>
    <key>NSLocalNetworkUsageDescription</key>
    <string>用于访问本机上的 MV 播放器服务。</string>
    <key>NSSupportsAutomaticTermination</key> <false/>
    <key>NSSupportsSuddenTermination</key>    <false/>
</dict>
</plist>
PLIST

echo "▸ 签名(本机 ad-hoc)"
codesign --force --sign - --timestamp=none "$APP" >/dev/null 2>&1 || echo "  (签名跳过,不影响本机运行)"

echo "✓ 打包完成: $APP"

if [ $# -ge 1 ]; then
    DEST="$1"
    rm -rf "$DEST/MV播放器.app"
    cp -R "$APP" "$DEST/"
    echo "✓ 已安装到 $DEST/MV播放器.app"
fi
