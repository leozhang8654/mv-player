#!/bin/bash
# MV 播放器 macOS 一键安装:下载最新版 → 校验 → 装进「应用程序」→ 打开。
#
#   curl -fsSL https://raw.githubusercontent.com/leozhang8654/mv-player/main/install-macos.sh | bash
#
# 用命令行下载的文件不带「来自互联网」标记,所以不会触发 macOS 的「无法验证开发者」拦截,
# 也不用去「隐私与安全性」里点「仍要打开」。重复执行即升级到最新版(曲库和视频不受影响)。
set -euo pipefail

REPO="leozhang8654/mv-player"
APP_NAME="MV播放器.app"
BASE="https://github.com/$REPO/releases/latest/download"

# Apple 芯片(包括在 Rosetta 下运行的终端)用 AppleSilicon 版,否则用 Intel 版
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ]; then
    ASSET="MVPlayer-macOS-AppleSilicon.dmg"
else
    ASSET="MVPlayer-macOS-Intel.dmg"
fi

# 默认装到 /Applications;没有写权限(非管理员账户)时装到 ~/Applications
DEST="${MV_INSTALL_DIR:-/Applications}"
if [ ! -w "$DEST" ]; then
    DEST="$HOME/Applications"
    mkdir -p "$DEST"
fi

TMP="$(mktemp -d)"
cleanup() {
    hdiutil detach -quiet "$TMP/mnt" 2>/dev/null || true
    rm -rf "$TMP"
}
trap cleanup EXIT

echo "▸ 下载 $ASSET"
curl -fL --progress-bar --retry 3 -o "$TMP/$ASSET" "$BASE/$ASSET"
curl -fsSL --retry 3 -o "$TMP/SHA256SUMS.txt" "$BASE/SHA256SUMS.txt"

echo "▸ 校验文件"
EXPECTED="$(grep " $ASSET\$" "$TMP/SHA256SUMS.txt" | awk '{print $1}')"
ACTUAL="$(shasum -a 256 "$TMP/$ASSET" | awk '{print $1}')"
if [ -z "$EXPECTED" ] || [ "$EXPECTED" != "$ACTUAL" ]; then
    echo "✗ 校验失败,下载的文件不完整或被篡改,请重试。" >&2
    exit 1
fi

echo "▸ 安装到 $DEST"
hdiutil attach -quiet -nobrowse -mountpoint "$TMP/mnt" "$TMP/$ASSET"
# 正在运行的旧版先退出
osascript -e 'quit app id "io.github.leozhang8654.mvplayer"' >/dev/null 2>&1 || true
rm -rf "${DEST:?}/$APP_NAME"
cp -R "$TMP/mnt/$APP_NAME" "$DEST/"
# 万一之前手动下载过、残留了隔离标记,一并清掉
xattr -dr com.apple.quarantine "$DEST/$APP_NAME" 2>/dev/null || true

VERSION="$(defaults read "$DEST/$APP_NAME/Contents/Info" CFBundleShortVersionString 2>/dev/null || echo "?")"
echo "✓ MV 播放器 $VERSION 已安装:$DEST/$APP_NAME"
echo "  曲库和下载的视频保存在「影片 / MV播放器」文件夹。"

if [ -z "${MV_NO_OPEN:-}" ]; then
    open "$DEST/$APP_NAME"
fi
