# -*- coding: utf-8 -*-
"""生成 DMG 安装窗口的背景图(background.png + background@2x.png)。

窗口 660×480:上方标题,中间「App → 应用程序」拖拽箭头,下方提示首次打开要看的说明。
图标位置与 packaging/dmg/settings.py 一致。需要 Pillow 和 macOS 自带字体;生成结果已入库,
只有改设计时才需要重新运行:python3 packaging/dmg/make_background.py
"""
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 660, 480
S = 2  # 先按 2 倍画,再缩出 1 倍
CJK = "/System/Library/Fonts/Hiragino Sans GB.ttc"   # index 0 = W3, 1 = W6
PINK = (236, 72, 153)

# 与 settings.py 保持一致
APP_X, APPS_X, ROW_Y = 170, 490, 190
README_Y = 392


def font(size, bold=False):
    return ImageFont.truetype(CJK, size * S, index=1 if bold else 0)


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def luminance(rgb):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


# 中等亮度的品牌渐变:Finder 的图标名称在浅色模式是黑字、深色模式是白字,
# 背景亮度控制在 0.17 左右,两种字色的对比度都约 4.5:1,哪种模式都看得清
TOP_LEFT, BOTTOM_RIGHT = (122, 108, 178), (156, 90, 156)


def draw():
    for c in (TOP_LEFT, BOTTOM_RIGHT):
        assert 0.15 <= luminance(c) <= 0.21, luminance(c)
    img = Image.new("RGB", (W * S, H * S))
    px = img.load()
    for y in range(H * S):
        for x in range(W * S):
            px[x, y] = lerp(TOP_LEFT, BOTTOM_RIGHT, (x / (W * S) * 0.6 + y / (H * S) * 0.4))
    # 柔和的高光,增加层次
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse((-160 * S, -220 * S, 360 * S, 200 * S), fill=(255, 255, 255, 26))
    g.ellipse((380 * S, 320 * S, 860 * S, 700 * S), fill=PINK + (40,))
    glow = glow.filter(ImageFilter.GaussianBlur(80 * S))
    img = Image.alpha_composite(img.convert("RGBA"), glow)
    d = ImageDraw.Draw(img)

    def center_text(y, text, f, fill):
        w = d.textlength(text, font=f)
        d.text(((W * S - w) / 2, y * S), text, font=f, fill=fill)

    white, soft = (255, 255, 255), (238, 232, 250)
    center_text(34, "把 MV 播放器拖进「应用程序」", font(22, bold=True), white)
    center_text(68, "拖动左边的图标到右边的文件夹,即完成安装", font(13), soft)

    # 拖拽箭头
    x0, x1, y = (APP_X + 78) * S, (APPS_X - 80) * S, ROW_Y * S
    d.rounded_rectangle((x0, y - 3 * S, x1, y + 3 * S), radius=3 * S, fill=white)
    d.polygon([(x1 + 16 * S, y), (x1 - 4 * S, y - 15 * S), (x1 - 4 * S, y + 15 * S)], fill=white)

    # 分隔线与首次打开提示
    d.line((90 * S, 292 * S, (W - 90) * S, 292 * S), fill=(255, 255, 255, 90), width=S)
    center_text(304, "首次打开前,请先双击下方的「首次打开必读」", font(13, bold=True), white)
    center_text(325, "本应用未经 Apple 公证,需要在「终端」里执行一条命令才能打开", font(12), soft)
    return img.convert("RGB")


def main():
    big = draw()
    big.save(os.path.join(HERE, "background@2x.png"), optimize=True)
    big.resize((W, H), Image.LANCZOS).save(os.path.join(HERE, "background.png"), optimize=True)
    print("✓ packaging/dmg/background.png, background@2x.png")


if __name__ == "__main__":
    main()
