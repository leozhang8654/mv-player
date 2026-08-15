# -*- coding: utf-8 -*-
"""MV 播放器本地服务:多歌单管理 + 后台搜索下载 + 视频播放。

数据模型:
- state["songs"]     全局歌曲池,同一首歌(歌手,歌名)只存在一份、只下载一次
- state["playlists"] 歌单列表,每个歌单存有序的 song_ids
歌曲不再被任何歌单引用时,自动删除其视频/封面文件。
"""
import json
import os
import random
import re
import subprocess
import threading
import time
import urllib.request
import uuid

from flask import Flask, jsonify, request, send_from_directory

import downloader

BASE = os.path.dirname(os.path.abspath(__file__))
MEDIA_DIR = os.path.join(BASE, "media")
THUMB_DIR = os.path.join(MEDIA_DIR, "thumbs")
SUBS_DIR = os.path.join(MEDIA_DIR, "subs")
LIBRARY = os.path.join(BASE, "library.json")
SOURCES = ["youtube", "bilibili"]
PORT = 8471

os.makedirs(THUMB_DIR, exist_ok=True)
os.makedirs(SUBS_DIR, exist_ok=True)

app = Flask(__name__, static_folder="static", static_url_path="/static")
lock = threading.RLock()
wake = threading.Event()
state = {"playlists": [], "songs": []}

# 放缓下载节奏,避免被 YouTube 反爬标记(比速度更重要)
SONG_PAUSE_RANGE = (8, 20)   # 每首歌之间随机停顿秒数
BOT_COOLDOWN = 300           # 触发人机验证后整个队列冷却秒数
cooldown_until = 0.0


def new_id():
    return uuid.uuid4().hex[:12]


def load_state():
    global state
    if os.path.exists(LIBRARY):
        try:
            with open(LIBRARY, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (ValueError, OSError):
            pass
    # 旧版(单歌单)数据迁移
    if "playlists" not in state:
        songs = state.get("songs", [])
        state = {
            "playlists": ([{"id": new_id(), "name": "我的歌单",
                            "song_ids": [s["id"] for s in songs]}] if songs else []),
            "songs": songs,
        }
    # 上次运行中断的任务重新排队
    for s in state["songs"]:
        if s["status"] in ("searching", "downloading"):
            s["status"] = "pending"
            s["progress"] = 0


def save_state():
    tmp = LIBRARY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LIBRARY)


def find_playlist(pl_id):
    for p in state["playlists"]:
        if p["id"] == pl_id:
            return p
    return None


def song_in_state(song_id):
    return any(s["id"] == song_id for s in state["songs"])


def song_basename(song):
    """按「歌名 - 歌手」生成文件名主体,过滤文件系统非法字符。"""
    label = song["title"] + (" - " + song["artist"] if song["artist"] else "")
    label = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", label).strip(" .")
    return label[:150] or song["id"]


def rename_to_label(song, path):
    """把下载好的 <id>.mp4 改名成「歌名 - 歌手.mp4」,返回最终文件名。"""
    ext = os.path.splitext(path)[1].lstrip(".") or "mp4"
    name = "%s.%s" % (song_basename(song), ext)
    target = os.path.join(MEDIA_DIR, name)
    if os.path.abspath(target) == os.path.abspath(path):
        return os.path.basename(path)
    if os.path.exists(target):  # 撞名 → 加短 id 后缀
        name = "%s [%s].%s" % (song_basename(song), song["id"][:6], ext)
        target = os.path.join(MEDIA_DIR, name)
    try:
        os.rename(path, target)
        return name
    except OSError:
        return os.path.basename(path)


try:
    from opencc import OpenCC
    _t2s_convert = OpenCC("t2s").convert
except Exception:
    _t2s_convert = None


def classify_zh_vtt(path):
    """读字幕内容判断简繁:繁转简后变化明显 → 繁体,否则简体。"""
    if _t2s_convert is None:
        return "zh-Hans"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            sample = f.read(6000)
    except OSError:
        return "zh-Hans"
    converted = _t2s_convert(sample)
    diff = sum(1 for a, b in zip(sample, converted) if a != b)
    return "zh-Hant" if diff > 10 else "zh-Hans"


def _normalize_zh_sub(dirpath, name, song_id):
    """把笼统的 <id>.zh.vtt 按内容改名为 zh-Hans/zh-Hant,返回最终语言代码。"""
    lang = name[len(song_id) + 1:-4]
    if lang.lower() != "zh":
        return name, lang
    src = os.path.join(dirpath, name)
    new_lang = classify_zh_vtt(src)
    new_name = "%s.%s.vtt" % (song_id, new_lang)
    try:
        os.replace(src, os.path.join(dirpath, new_name))
        return new_name, new_lang
    except OSError:
        return name, lang


def collect_subs(song_id):
    """把下载时落在 media/ 的 <id>.<lang>.vtt 归档到 subs/,返回语言列表。

    语言代码只写 zh 的,按内容自动识别为 zh-Hans / zh-Hant。
    """
    langs = []
    try:
        for name in os.listdir(MEDIA_DIR):
            if name.startswith(song_id + ".") and name.endswith(".vtt"):
                lang = name[len(song_id) + 1:-4]
                try:
                    os.replace(os.path.join(MEDIA_DIR, name),
                               os.path.join(SUBS_DIR, name))
                except OSError:
                    continue
                _, lang = _normalize_zh_sub(SUBS_DIR, name, song_id)
                langs.append(lang)
    except OSError:
        pass
    # 之前已归档的也算上(顺带把历史的 zh 归一化)
    try:
        for name in os.listdir(SUBS_DIR):
            if name.startswith(song_id + ".") and name.endswith(".vtt"):
                _, lang = _normalize_zh_sub(SUBS_DIR, name, song_id)
                if lang not in langs:
                    langs.append(lang)
    except OSError:
        pass
    return sorted(langs)


def cleanup_song_media(song_id, video_file=None):
    """删掉一首歌落盘的所有东西:视频(含改名后的)、半成品、封面、字幕。"""
    downloader.cleanup_song_files(MEDIA_DIR, song_id)
    if video_file:
        try:
            os.remove(os.path.join(MEDIA_DIR, video_file))
        except OSError:
            pass
    try:
        os.remove(os.path.join(THUMB_DIR, song_id + ".jpg"))
    except OSError:
        pass
    try:
        for name in os.listdir(SUBS_DIR):
            if name.startswith(song_id + "."):
                os.remove(os.path.join(SUBS_DIR, name))
    except OSError:
        pass


def migrate_filenames():
    """把历史下载的 <id>.mp4 迁移成「歌名 - 歌手.mp4」(启动时执行一次)。"""
    changed = False
    for s in state["songs"]:
        vf = s.get("video_file")
        if s["status"] != "done" or not vf:
            continue
        src = os.path.join(MEDIA_DIR, vf)
        if not os.path.exists(src):
            continue
        ext = os.path.splitext(vf)[1].lstrip(".") or "mp4"
        want = "%s.%s" % (song_basename(s), ext)
        if vf == want:
            continue
        target = os.path.join(MEDIA_DIR, want)
        if os.path.exists(target):
            want = "%s [%s].%s" % (song_basename(s), s["id"][:6], ext)
            target = os.path.join(MEDIA_DIR, want)
            if os.path.exists(target):
                continue
        try:
            os.rename(src, target)
            s["video_file"] = want
            changed = True
        except OSError:
            pass
    if changed:
        save_state()


def gc_songs():
    """清掉不再被任何歌单引用的歌(调用方需持锁)。"""
    referenced = set()
    for pl in state["playlists"]:
        referenced.update(pl["song_ids"])
    kept = []
    for s in state["songs"]:
        if s["id"] in referenced:
            kept.append(s)
        elif s["status"] not in ("searching", "downloading"):
            cleanup_song_media(s["id"], s.get("video_file"))
        # 正在处理中的歌由 process_song 收尾时清理
    state["songs"] = kept


def parse_line(line):
    """把 '歌手 - 歌名'(或只有歌名)的一行解析成 (artist, title)。"""
    line = re.sub(r"^\s*\d+[.、)\]]\s*", "", line.strip())  # 去掉行首编号
    line = line.strip()
    if not line:
        return None
    # 优先按「两侧带空格的横线」切分,避免拆坏 G-Dragon / A-Lin 这类名字;
    # 没有再看不带空格的横线,但只在恰好一条时才切
    parts = re.split(r"\s+[-—–]\s+", line, maxsplit=1)
    if len(parts) == 1:
        bare = re.split(r"[-—–]", line)
        parts = bare if len(bare) == 2 else [line]
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        artist, title = parts[0].strip(), parts[1].strip()
    else:
        artist, title = "", line
    title = title.strip("《》「」\"'")
    artist = artist.strip("《》「」\"'")
    return artist, title


APPLE_URL_RE = re.compile(r"https://(?:embed\.)?music\.apple\.com/[^\s\"'<>]+")
VIDEO_URL_RE = re.compile(
    r"https?://(?:www\.|m\.)?"
    r"(?:youtube\.com/(?:watch|shorts/)\S+|youtu\.be/\S+"
    r"|bilibili\.com/video/\S+|b23\.tv/\S+)"
)
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")


def fetch_apple_tracks(url):
    """从 Apple Music 歌单/专辑页面提取 [(歌手, 歌名), ...]。

    嵌入页(embed.music.apple.com)是纯 JS 壳,换成主站同路径页面,
    里面有服务端渲染的 serialized-server-data JSON,歌曲对象带 duration 字段。
    """
    url = url.replace("embed.music.apple.com", "music.apple.com").split("?")[0]
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    html = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace")
    m = re.search(
        r'<script type="application/json" id="serialized-server-data">(.*?)</script>',
        html, re.S,
    )
    if not m:
        raise RuntimeError("页面里没有歌单数据(歌单可能未公开分享)")
    tracks, seen = [], set()

    def walk(o):
        if isinstance(o, dict):
            if "artistName" in o and "duration" in o:
                t = o.get("title") or o.get("name")
                if isinstance(t, str) and t.strip():
                    key = (o["artistName"].strip(), t.strip())
                    if key not in seen:
                        seen.add(key)
                        tracks.append(key)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(json.loads(m.group(1)))
    if not tracks:
        raise RuntimeError("没有解析到任何歌曲")
    return tracks


def make_thumb(song_id, video_file):
    """从视频里截一帧做封面,成功返回 True。"""
    src = os.path.join(MEDIA_DIR, video_file)
    dst = os.path.join(THUMB_DIR, song_id + ".jpg")
    for ss in ("5", "1"):  # 先取第 5 秒,太短的视频退回第 1 秒
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", ss, "-i", src,
                 "-frames:v", "1", "-vf", "scale=320:-2", dst],
                capture_output=True, timeout=30,
            )
        except Exception:
            return False
        if os.path.exists(dst):
            return True
    return False


def measure_loudness(video_file):
    """用 ffmpeg 测视频音轨的平均响度(dB),用于播放端自动均衡音量。"""
    src = os.path.join(MEDIA_DIR, video_file)
    try:
        proc = subprocess.run(
            ["ffmpeg", "-i", src, "-vn", "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", proc.stderr)
        if m:
            return float(m.group(1))
    except Exception:
        pass
    return None


def media_backfill():
    """给历史下载补生成封面、响度和字幕(启动时跑一次)。"""
    with lock:
        todo = [dict(s) for s in state["songs"]
                if s["status"] == "done" and s.get("video_file")
                and (not s.get("thumb") or s.get("loudness") is None
                     or "subs" not in s)]
    for snap in todo:
        song_id = snap["id"]
        thumb_ok = loudness = subs = None
        if not snap.get("thumb"):
            thumb_ok = make_thumb(song_id, snap["video_file"])
        if snap.get("loudness") is None:
            loudness = measure_loudness(snap["video_file"])
        if "subs" not in snap and snap.get("video_url"):
            # 匿名补抓字幕;被人机验证拦住就先跳过,下次启动再试
            if downloader.fetch_subs(snap["video_url"], MEDIA_DIR, song_id):
                subs = collect_subs(song_id)
            time.sleep(3)
        with lock:
            for s in state["songs"]:
                if s["id"] == song_id:
                    if thumb_ok is not None and not s.get("thumb"):
                        s["thumb"] = thumb_ok
                    if loudness is not None and s.get("loudness") is None:
                        s["loudness"] = loudness
                    if subs is not None:
                        s["subs"] = subs
            save_state()


def requeue_due_retries():
    """到了自动重试时间的失败歌曲重新排队(调用方需持锁)。"""
    now = time.time()
    changed = False
    for s in state["songs"]:
        if (s["status"] == "failed" and s.get("auto_retry_at")
                and s["auto_retry_at"] <= now):
            s["status"] = "pending"
            s["auto_retry_at"] = None
            changed = True
    if changed:
        save_state()


def worker():
    while True:
        # 任何异常(包括磁盘写满导致 save_state 失败)都不能杀死唯一的下载线程
        try:
            song = None
            with lock:
                requeue_due_retries()
                # 人机验证冷却期内不派新任务
                if time.time() >= cooldown_until:
                    for s in state["songs"]:
                        if s["status"] == "pending":
                            song = s
                            s["status"] = "searching"
                            save_state()
                            break
            if song is None:
                wake.wait(timeout=5)
                wake.clear()
                continue
            process_song(song)
            # 每首歌之间随机停顿,放缓整体节奏
            time.sleep(random.uniform(*SONG_PAUSE_RANGE))
        except Exception as e:
            print("worker 异常(3 秒后继续):", e)
            time.sleep(3)


def process_song(song):
    try:
        if song.get("forced") and song.get("video_url"):
            # 用户手动指定的链接:跳过搜索直接下载
            url = song["video_url"]
            source, score, video_title = "manual", None, song.get("video_title")
        else:
            best = downloader.find_mv(song["title"], song["artist"], SOURCES)
            if best is None or best[0] < downloader.ACCEPT_THRESHOLD:
                with lock:
                    song["status"] = "no_mv"
                    save_state()
                return
            score, entry, source = best
            url = entry.get("url") or entry.get("webpage_url")
            video_title = entry.get("title")
            song["channel"] = entry.get("channel") or entry.get("uploader")
        with lock:
            song["status"] = "downloading"
            song["progress"] = 0
            song["source"] = source
            song["score"] = score
            song["video_title"] = video_title
            song["video_url"] = url
            save_state()

        def on_progress(pct):
            with lock:
                song["progress"] = pct

        # 换版本重下时先清掉旧成品文件
        if song.get("video_file"):
            try:
                os.remove(os.path.join(MEDIA_DIR, song["video_file"]))
            except OSError:
                pass
        path = downloader.download(url, MEDIA_DIR, song["id"], on_progress)
        subs = collect_subs(song["id"])
        video_file = rename_to_label(song, path)
        thumb_ok = make_thumb(song["id"], video_file)
        loudness = measure_loudness(video_file)
        with lock:
            song["status"] = "done"
            song["progress"] = 100
            song["video_file"] = video_file
            song["thumb"] = thumb_ok
            song["loudness"] = loudness
            song["subs"] = subs
            song["retry_count"] = 0
            song["auto_retry_at"] = None
            save_state()
    except Exception as e:
        global cooldown_until
        msg = str(e)
        bot_check = "Sign in to confirm" in msg or "not a bot" in msg
        transient = (bot_check or "HTTP Error 403" in msg
                     or "Connection reset" in msg or "timed out" in msg)
        with lock:
            song["status"] = "failed"
            if bot_check:
                # 整个队列冷却一段时间,别让后面的歌挨个撞墙
                cooldown_until = time.time() + BOT_COOLDOWN
            if transient:
                # 暂时性故障,定时自动重试:15分钟起,逐次翻倍,最多8轮
                song["retry_count"] = song.get("retry_count", 0) + 1
                wait_min = min(60, 15 * (2 ** (song["retry_count"] - 1)))
                cause = ("YouTube 触发人机验证(下载太频繁被暂时限制)" if bot_check
                         else "下载被 YouTube 中途拒绝(临时限流)")
                if song["retry_count"] <= 8:
                    song["auto_retry_at"] = time.time() + wait_min * 60
                    msg = "%s,约 %d 分钟后自动重试;也可点 ↻ 立即试" % (cause, wait_min)
                else:
                    song["auto_retry_at"] = None
                    msg = "%s,多次未成功已停止自动重试,请稍后手动点 ↻" % cause
            song["error"] = msg[:300]
            save_state()
    finally:
        # 处理期间歌曲被移出所有歌单 → 清掉刚落盘的文件,不留孤儿
        with lock:
            if not song_in_state(song["id"]):
                cleanup_song_media(song["id"], song.get("video_file"))


@app.route("/")
def index():
    return send_from_directory(os.path.join(BASE, "static"), "index.html")


@app.route("/api/state")
def api_state():
    with lock:
        return jsonify(state)


# ---------- 歌单 ----------

@app.route("/api/playlists", methods=["POST"])
def api_create_playlist():
    name = ((request.get_json(silent=True) or {}).get("name") or "").strip()
    if not name:
        return jsonify({"error": "歌单名不能为空"}), 400
    pl = {"id": new_id(), "name": name[:60], "song_ids": []}
    with lock:
        state["playlists"].append(pl)
        save_state()
    return jsonify(pl)


@app.route("/api/playlists/<pl_id>", methods=["PATCH"])
def api_rename_playlist(pl_id):
    name = ((request.get_json(silent=True) or {}).get("name") or "").strip()
    if not name:
        return jsonify({"error": "歌单名不能为空"}), 400
    with lock:
        pl = find_playlist(pl_id)
        if pl is None:
            return jsonify({"error": "歌单不存在"}), 404
        pl["name"] = name[:60]
        save_state()
    return jsonify(pl)


@app.route("/api/playlists/<pl_id>", methods=["DELETE"])
def api_delete_playlist(pl_id):
    with lock:
        pl = find_playlist(pl_id)
        if pl is None:
            return jsonify({"error": "歌单不存在"}), 404
        state["playlists"].remove(pl)
        gc_songs()
        save_state()
    return jsonify({"ok": True})


@app.route("/api/playlists/<pl_id>/reorder", methods=["POST"])
def api_reorder_playlist(pl_id):
    ids = (request.get_json(silent=True) or {}).get("song_ids")
    with lock:
        pl = find_playlist(pl_id)
        if pl is None:
            return jsonify({"error": "歌单不存在"}), 404
        if not isinstance(ids, list) or sorted(ids) != sorted(pl["song_ids"]):
            # 必须是同一批歌的重新排列,防止并发修改时把歌顺没了
            return jsonify({"error": "顺序列表与歌单内容不一致,请刷新后重试"}), 409
        pl["song_ids"] = ids
        save_state()
    return jsonify({"ok": True})


# ---------- 歌单内的歌 ----------

@app.route("/api/playlists/<pl_id>/songs", methods=["POST"])
def api_add_songs(pl_id):
    text = (request.get_json(silent=True) or {}).get("text", "")
    # 第一遍:展开所有行(Apple Music / 视频链接需要联网,放在锁外)
    entries = []  # {"artist", "title", "url"(可选,直接下载该链接)}
    errors = []
    for line in text.splitlines():
        m = APPLE_URL_RE.search(line)
        if m:
            try:
                entries.extend({"artist": a, "title": t}
                               for a, t in fetch_apple_tracks(m.group(0)))
            except Exception as e:
                errors.append("Apple Music 导入失败: %s" % e)
            continue
        vm = VIDEO_URL_RE.search(line)
        if vm:
            url = vm.group(0).rstrip(">)]}'\"")
            info = None
            try:
                info = downloader.fetch_video_info(url)
            except Exception:
                pass
            if info and info.get("title"):
                # 视频标题形如「歌手 - 歌名」就拆开;否则频道当歌手、全标题当歌名
                parts = re.split(r"\s+[-—–]\s+", info["title"], maxsplit=1)
                if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                    artist, title = parts[0].strip(), parts[1].strip()
                else:
                    artist, title = (info.get("channel") or "").strip(), info["title"].strip()
                entries.append({"artist": artist, "title": title, "url": url})
            else:
                errors.append("链接信息获取失败(可稍后重试): %s" % url[:60])
            continue
        parsed = parse_line(line)
        if parsed is not None:
            entries.append({"artist": parsed[0], "title": parsed[1]})
    added = 0
    with lock:
        pl = find_playlist(pl_id)
        if pl is None:
            return jsonify({"error": "歌单不存在"}), 404
        by_key = {(s["artist"], s["title"]): s for s in state["songs"]}
        by_url = {s["video_url"]: s for s in state["songs"] if s.get("video_url")}
        for item in entries:
            key = (item["artist"], item["title"])
            song = by_url.get(item.get("url")) or by_key.get(key)
            if song is None:
                song = {
                    "id": new_id(),
                    "artist": item["artist"],
                    "title": item["title"],
                    "status": "pending",
                    "progress": 0,
                    "video_file": None,
                    "video_title": None,
                    "video_url": item.get("url"),
                    "forced": bool(item.get("url")),
                    "source": None,
                    "error": None,
                    "thumb": False,
                }
                state["songs"].append(song)
                by_key[key] = song
                if item.get("url"):
                    by_url[item["url"]] = song
            if song["id"] not in pl["song_ids"]:
                pl["song_ids"].append(song["id"])
                added += 1
        if added:
            save_state()
    wake.set()
    return jsonify({"added": added, "error": "; ".join(errors) if errors else None})


@app.route("/api/playlists/<pl_id>/songs/<song_id>", methods=["DELETE"])
def api_remove_song(pl_id, song_id):
    with lock:
        pl = find_playlist(pl_id)
        if pl is None or song_id not in pl["song_ids"]:
            return jsonify({"error": "不存在"}), 404
        pl["song_ids"].remove(song_id)
        gc_songs()
        save_state()
    return jsonify({"ok": True})


@app.route("/api/songs/<song_id>/set_url", methods=["POST"])
def api_set_url(song_id):
    """手动指定视频链接,跳过自动搜索直接下载(也可用于纠正匹配错误的歌)。"""
    url = ((request.get_json(silent=True) or {}).get("url") or "").strip()
    if not re.match(
        r"^https?://(www\.|m\.)?(youtube\.com|youtu\.be|bilibili\.com|b23\.tv)/\S+$", url
    ):
        return jsonify({"error": "仅支持 YouTube / Bilibili 视频链接"}), 400
    with lock:
        for s in state["songs"]:
            if s["id"] == song_id:
                if s["status"] in ("searching", "downloading"):
                    return jsonify({"error": "正在处理中,稍后再试"}), 409
                s["video_url"] = url
                s["forced"] = True
                s["status"] = "pending"
                s["progress"] = 0
                s["error"] = None
                save_state()
                wake.set()
                return jsonify({"ok": True})
    return jsonify({"error": "歌曲不存在"}), 404


@app.route("/api/songs/<song_id>/retry", methods=["POST"])
def api_retry_song(song_id):
    with lock:
        for s in state["songs"]:
            if s["id"] == song_id and s["status"] in ("no_mv", "failed"):
                s["status"] = "pending"
                s["progress"] = 0
                s["error"] = None
                save_state()
                wake.set()
                return jsonify({"ok": True})
    return jsonify({"ok": False}), 404


@app.route("/media/<path:filename>")
def media(filename):
    if filename.endswith(".vtt"):
        # <track> 元素要求 text/vtt,系统 mimetypes 可能不认识
        return send_from_directory(MEDIA_DIR, filename, mimetype="text/vtt")
    return send_from_directory(MEDIA_DIR, filename, conditional=True)


def migrate_zh_subs():
    """把历史抓取的笼统 zh 字幕归一化为 zh-Hans / zh-Hant(启动时执行)。"""
    changed = False
    for s in state["songs"]:
        if s.get("subs") and any(l.lower() == "zh" for l in s["subs"]):
            s["subs"] = collect_subs(s["id"])
            changed = True
    if changed:
        save_state()


load_state()
migrate_filenames()
migrate_zh_subs()
threading.Thread(target=worker, daemon=True).start()
threading.Thread(target=media_backfill, daemon=True).start()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=PORT, threaded=True)
