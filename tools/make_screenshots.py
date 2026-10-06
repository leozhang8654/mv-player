# -*- coding: utf-8 -*-
"""生成 README 用的界面截图(演示数据,不会读写真实曲库)。

做法:在临时目录里造一份演示曲库(渐变色的合成视频代替真实 MV),另起一个
服务实例,用本机 Chrome(Playwright 驱动)截图,再加上窗口外框和阴影。

需要:pip install flask opencc-python-reimplemented playwright pillow;
      本机装有 Google Chrome 和 ffmpeg。
用法:python3 tools/make_screenshots.py      → docs/images/*.png
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

from PIL import Image, ImageDraw, ImageFilter
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "images")
PORT = 8611
BASE = "http://127.0.0.1:%d/" % PORT

# (歌手, 歌名, 渐变色 1, 渐变色 2) —— 视频是合成的渐变动画,不含任何真实 MV 画面
DEMO = [
    ("YOASOBI", "群青", "0x2b5cff", "0x9be7ff"),
    ("The Weeknd", "Blinding Lights", "0xff0844", "0x3a0ca3"),
    ("周杰伦", "晴天", "0xffb347", "0xff5e62"),
    ("Daft Punk", "Get Lucky", "0xf9d423", "0xff4e50"),
    ("宇多田ヒカル", "One Last Kiss", "0x8e2de2", "0x4a00e0"),
    ("BLACKPINK", "JUMP", "0xff6fd8", "0x3813c2"),
    ("Taylor Swift", "Shake It Off", "0x43e97b", "0x38f9d7"),
    ("陈奕迅", "孤勇者", "0x434343", "0xd31027"),
    ("Imagine Dragons", "Believer", "0xf83600", "0xf9d423"),
    ("米津玄師", "Lemon", "0xf7ff00", "0xdb36a4"),
    ("Ed Sheeran", "Shape of You", "0x00c6ff", "0x0072ff"),
    ("Billie Eilish", "bad guy", "0x0f2027", "0x8bc34a"),
]
PLAYLISTS = [
    ("通勤路上", list(range(12))),
    ("华语经典", [2, 7, 0, 4]),
    ("日系动漫", [0, 4, 9, 5]),
    ("健身房", [3, 8, 1, 6]),
]


def sh(*cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def build_demo(data):
    media = os.path.join(data, "media")
    os.makedirs(os.path.join(media, "thumbs"), exist_ok=True)
    os.makedirs(os.path.join(media, "subs"), exist_ok=True)
    songs = []
    for i, (artist, title, c0, c1) in enumerate(DEMO):
        sid = "demo%02d" % i
        video = "%s - %s.mp4" % (title, artist)
        path = os.path.join(media, video)
        sh("ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i", "gradients=s=1280x720:c0=%s:c1=%s:speed=0.02:d=20,format=yuv420p" % (c0, c1),
           "-f", "lavfi", "-i", "sine=f=220:d=20", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
           "-c:a", "aac", "-shortest", path)
        sh("ffmpeg", "-y", "-v", "error", "-ss", "4", "-i", path, "-frames:v", "1",
           "-vf", "scale=320:-2", os.path.join(media, "thumbs", sid + ".jpg"))
        songs.append({
            "id": sid, "artist": artist, "title": title, "status": "done", "progress": 100,
            "video_file": video, "video_title": "%s - %s (Official Music Video)" % (artist, title),
            "video_url": None, "forced": False, "source": "youtube", "error": None, "thumb": True,
            "channel": artist, "score": 14, "loudness": -14.0, "subs": [], "audit_v": 2,
            "motion_checked": True, "static": False,
        })
    songs.append({
        "id": "review1", "artist": "Dance Fruits Music", "title": "Toosie Slide", "status": "review",
        "progress": 0, "video_file": None, "video_title": None, "video_url": None, "forced": False,
        "source": None, "error": None, "thumb": False, "audit_v": 2,
        "candidates": [
            {"url": "https://www.youtube.com/watch?v=demo1", "title": "Drake - Toosie Slide (Official Music Video)",
             "channel": "Drake", "duration": 250, "thumbnail": None, "score": 7, "source": "youtube"},
            {"url": "https://www.youtube.com/watch?v=demo2", "title": "Toosie Slide (Dance Fruits Remake)",
             "channel": "Dance Fruits Music", "duration": 168, "thumbnail": None, "score": 4, "source": "youtube"},
        ],
    })
    songs.append({
        "id": "nomv1", "artist": "Demo Artist", "title": "Untitled Demo", "status": "no_mv",
        "progress": 0, "video_file": None, "video_title": None, "video_url": None, "forced": False,
        "source": None, "error": None, "thumb": False, "audit_v": 2,
    })
    ids = [s["id"] for s in songs]
    # 歌单 id 必须是十六进制(前端路由 #/p/<hex>)
    playlists = [{"id": "a0d%03d" % i, "name": name, "song_ids": [ids[j] for j in idx]}
                 for i, (name, idx) in enumerate(PLAYLISTS)]
    playlists[0]["song_ids"] += ["review1", "nomv1"]
    with open(os.path.join(data, "library.json"), "w", encoding="utf-8") as f:
        json.dump({"playlists": playlists, "songs": songs}, f, ensure_ascii=False)


def frame(src, dst, width=1600):
    """给截图加上 macOS 风格窗口外框、圆角和阴影,再缩到 README 合适的宽度。"""
    shot = Image.open(src).convert("RGBA")
    s = shot.width / 1280  # 截图倍率
    bar, radius, pad = int(38 * s), int(14 * s), int(56 * s)
    w, h = shot.width, shot.height + bar
    win = Image.new("RGBA", (w, h), (24, 26, 33, 255))
    win.paste(shot, (0, bar))
    d = ImageDraw.Draw(win)
    for k, color in enumerate(((255, 95, 87), (254, 188, 46), (40, 200, 64))):
        cx, cy, r = int((22 + k * 20) * s), bar // 2, int(6 * s)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius, fill=255)
    canvas = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((pad, pad + int(10 * s), pad + w, pad + h + int(10 * s)),
                                             radius, fill=(0, 0, 0, 150))
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(int(22 * s))))
    canvas.paste(win, (pad, pad), mask)
    canvas = canvas.resize((width, round(canvas.height * width / canvas.width)), Image.LANCZOS)
    canvas.save(dst, optimize=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as raw:
        build_demo(data)
        env = dict(os.environ, MV_DATA_DIR=data, MV_PORT=str(PORT))
        server = subprocess.Popen([sys.executable, os.path.join(ROOT, "app.py")], cwd=ROOT, env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(BASE + "api/state", timeout=1)
                    break
                except OSError:
                    time.sleep(0.2)
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="chrome")
                ctx = browser.new_context(viewport={"width": 1280, "height": 800},
                                          device_scale_factor=2, color_scheme="dark", locale="zh-CN")
                page = ctx.new_page()

                def live_states(route):
                    # 演示「正在下载 / 正在搜索」:只改发给页面的状态,不动服务端
                    resp = route.fetch()
                    state = resp.json()
                    for s in state["songs"]:
                        if s["id"] == "demo10":
                            s.update(status="downloading", progress=63)
                        elif s["id"] == "demo11":
                            s.update(status="searching", progress=0)
                    route.fulfill(response=resp, json=state)

                page.route("**/api/state", live_states)
                shots = []

                page.goto(BASE)
                page.wait_for_timeout(1500)
                page.screenshot(path=os.path.join(raw, "home.png"))
                shots.append("home")

                page.locator('.pl-card[data-id="a0d000"] .pl-name').click()
                page.wait_for_selector("li.song.done")
                page.wait_for_timeout(800)
                page.locator("li.song.done").nth(1).click()
                page.wait_for_timeout(2500)
                page.screenshot(path=os.path.join(raw, "player.png"))
                shots.append("player")

                page.locator("li.song.review").first.click()
                page.wait_for_timeout(800)
                page.screenshot(path=os.path.join(raw, "review.png"))
                shots.append("review")
                browser.close()
            for name in shots:
                frame(os.path.join(raw, name + ".png"), os.path.join(OUT, "screenshot-%s.png" % name))
                print("✓ docs/images/screenshot-%s.png" % name)
        finally:
            server.terminate()


if __name__ == "__main__":
    main()
