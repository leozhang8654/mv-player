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
import socket
import subprocess
import threading
import time
import urllib.request
import uuid

from flask import Flask, jsonify, request, send_from_directory

import catalog
import downloader
import paths

BASE = paths.CODE_DIR          # 程序文件(static/ 等)
MEDIA_DIR = os.path.join(paths.DATA_DIR, "media")
THUMB_DIR = os.path.join(MEDIA_DIR, "thumbs")
SUBS_DIR = os.path.join(MEDIA_DIR, "subs")
LIBRARY = os.path.join(paths.DATA_DIR, "library.json")
SOURCES = ["youtube", "bilibili"]
PORT = int(os.environ.get("MV_PORT") or 8471)

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
    """从 Apple Music 歌单/专辑页面提取 [(歌手, 歌名, 时长秒), ...]。

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
                        ms = o.get("duration")
                        tracks.append(key + (round(ms / 1000) if isinstance(ms, (int, float)) and ms > 0 else None,))
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
            capture_output=True, encoding="utf-8", errors="replace", timeout=120,
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
        if ((s["status"] == "failed" or (s["status"] == "done" and s.get("recheck")))
                and s.get("auto_retry_at") and s["auto_retry_at"] <= now):
            s["status"] = "pending"
            s["auto_retry_at"] = None
            changed = True
    if changed:
        save_state()


def make_progress(song):
    def on_progress(pct):
        with lock:
            song["progress"] = pct
    return on_progress


AUDIT_VERSION = 2


def audit_library():
    """用新的检测规则复查已有结果(每个版本只跑一次):

    - 已下载但标题不符(错歌/合集/制作过程/AI 视频)→ 删除并重新搜索,且不再选它
    - 已下载但是另一个版本(Acoustic/Live…)→ 重新搜索,原版找不到时保留现有文件
    - 之前没找到 MV 的 → 按新规则(去后缀、原文名、频道内搜索)再找一次
    """
    with lock:
        todo = [dict(s) for s in state["songs"]
                if s.get("audit_v", 0) < AUDIT_VERSION and not s.get("forced")]
    for snap in todo:
        action = None
        if snap["status"] == "done" and snap.get("video_url"):
            title = snap.get("video_title") or ""
            q = song_query(snap)
            verdict, _ = downloader.content_verdict(title, q)
            if verdict == "reject" and "catalog" not in snap:
                ensure_catalog(snap)  # 可能只是缺原文名,补查后再判一次
                q = song_query(snap)
                verdict, _ = downloader.content_verdict(title, q)
            if verdict == "reject":
                action = "drop"
            elif downloader.variant_words(title, q):
                action = "recheck"
        elif snap["status"] in ("no_mv", "review"):
            action = "recheck"
        with lock:
            for s in state["songs"]:
                if s["id"] != snap["id"]:
                    continue
                if "catalog" in snap:
                    s.setdefault("catalog", snap["catalog"])
                s["audit_v"] = AUDIT_VERSION
                if s["status"] not in ("done", "no_mv", "review"):
                    break  # 期间已被重新排队/处理
                if action == "drop":
                    rejected = s.setdefault("rejected_urls", [])
                    if s["video_url"] not in rejected:
                        rejected.append(s["video_url"])
                    drop_static_media(s)
                if action:
                    s["status"] = "pending"
                    s["progress"] = 0
            save_state()
    wake.set()


def static_backfill():
    """检查历史下载里的静态画面视频,排队重新找真正的 MV(找不到则删除,标记无 MV)。"""
    with lock:
        # 旧版本保留下来的静态视频:已经搜过没有更好的,直接删
        for s in state["songs"]:
            if s["status"] == "done" and s.get("static") and not s.get("forced"):
                drop_static_media(s)
                s["status"] = "no_mv"
        save_state()
        todo = [dict(s) for s in state["songs"]
                if s["status"] == "done" and s.get("video_file")
                and not s.get("forced") and not s.get("motion_checked")]
    for snap in todo:
        static = downloader.is_static_video(os.path.join(MEDIA_DIR, snap["video_file"]))
        with lock:
            for s in state["songs"]:
                if s["id"] != snap["id"] or s["status"] != "done":
                    continue
                s["motion_checked"] = True
                s["static"] = static
                if static and s.get("video_url"):
                    rejected = s.setdefault("rejected_urls", [])
                    if s["video_url"] not in rejected:
                        rejected.append(s["video_url"])
                    s["status"] = "pending"
            save_state()
        wake.set()


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


MAX_STATIC_SKIPS = 3  # 下到静态画面后最多换几个候选


def _finish_download(song, path, url):
    """新视频已确认可用:替换旧成品,生成封面/响度/字幕并标记完成。"""
    old = song.get("video_file")
    if old and old != os.path.basename(path):
        try:
            os.remove(os.path.join(MEDIA_DIR, old))
        except OSError:
            pass
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
        song["static"] = False
        song["motion_checked"] = True
        song["recheck"] = False
        song["retry_count"] = 0
        song["auto_retry_at"] = None
        save_state()


def drop_static_media(song):
    """删掉静态画面视频及其封面/字幕(调用方需持锁)。"""
    cleanup_song_media(song["id"], song.get("video_file"))
    song["video_file"] = None
    song["thumb"] = False
    song["subs"] = []
    song["static"] = False


def _set_status(song, status, **fields):
    with lock:
        song["status"] = status
        song.update(fields)
        save_state()


def ensure_catalog(song):
    """查一次 iTunes 拿参考时长和原文名;网络失败就跳过,下次处理时再查。"""
    if "catalog" in song:
        return
    try:
        cat = catalog.lookup(song["title"], song["artist"])
    except Exception:
        return
    with lock:
        song["catalog"] = cat or {}
        save_state()


def song_query(song):
    return downloader.SongQuery(
        song["title"], song["artist"], song.get("catalog"), song.get("duration"),
        downloader.get_settings().get("allow_variant", True))


def remember_channel(entry, q):
    """自动选中的视频来自歌手本人频道 → 记住频道 id,同歌手的歌以后先在频道内搜。"""
    cid, name = entry.get("channel_id"), entry.get("channel") or entry.get("uploader")
    if not cid or not name:
        return
    with lock:
        chans = state.setdefault("artist_channels", {})
        for key in downloader.artist_channel_keys(name, q):
            chans[key] = {"id": cid, "name": name}


def known_channels(song, q):
    """该歌手已知的官方频道 [(id, 名称)]。

    没记录过时,从库里找一首同歌手、且来自歌手本人频道的已下载歌,查一次它的频道 id。
    """
    chans = state.setdefault("artist_channels", {})
    keys = [downloader._norm(a) for a in q.artists]
    if not any(k in chans for k in keys):
        with lock:
            peers = [s for s in state["songs"]
                     if s is not song and s["status"] == "done" and s.get("channel")
                     and "youtu" in (s.get("video_url") or "")
                     and downloader.artist_channel_keys(s["channel"], q)]
        for peer in peers[:1]:
            info = None
            try:
                info = downloader.fetch_video_info(peer["video_url"])
            except Exception:
                pass
            with lock:
                for key in downloader.artist_channel_keys(peer["channel"], q):
                    # 查不到也记下(id 为空),避免同歌手的每首歌都去查一遍
                    chans[key] = {"id": (info or {}).get("channel_id"), "name": peer["channel"]}
                save_state()
    out = []
    for k in keys:
        info = chans.get(k)
        if info and info.get("id") and (info["id"], info["name"]) not in out:
            out.append((info["id"], info["name"]))
    return out


def _candidate(score, entry, source):
    return {"url": downloader._entry_url(entry), "title": entry.get("title"),
            "channel": entry.get("channel") or entry.get("uploader"),
            "duration": entry.get("duration"), "thumbnail": entry.get("thumbnail"),
            "score": score, "source": source}


def _file_exists(song):
    return bool(song.get("video_file")) and os.path.exists(os.path.join(MEDIA_DIR, song["video_file"]))


VIDEO_FIELDS = ("video_url", "video_title", "channel", "source", "score")


def process_song(song):
    # 复查已有视频时,下载失败要能恢复成原来的样子
    before = {k: song.get(k) for k in VIDEO_FIELDS}
    try:
        if song.get("forced") and song.get("video_url"):
            # 用户手动指定/确认的链接:跳过搜索直接下载,静态画面也照收
            url = song["video_url"]
            _set_status(song, "downloading", progress=0, source="manual", score=None,
                        candidates=None)
            path = downloader.download(url, MEDIA_DIR, song["id"], make_progress(song))
            _finish_download(song, path, url)
            return
        if song.get("static"):
            # 静态画面旧文件不要了,先删再找真正的 MV
            with lock:
                drop_static_media(song)
                save_state()
        ensure_catalog(song)
        q = song_query(song)
        rejected = song.setdefault("rejected_urls", [])
        ranked = downloader.search_mv(q, SOURCES, exclude=set(rejected),
                                      channels=known_channels(song, q))
        accepted = [c for c in ranked if c[0] >= downloader.ACCEPT_THRESHOLD]
        for score, entry, source in accepted[:MAX_STATIC_SKIPS]:
            url = downloader._entry_url(entry)
            fields = dict(source=source, score=score, video_title=entry.get("title"),
                          video_url=url, channel=entry.get("channel") or entry.get("uploader"),
                          candidates=None, error=None)
            if url == song.get("video_url") and _file_exists(song):
                # 复查时选中的就是现有文件,不用重下
                _set_status(song, "done", progress=100, recheck=False, **fields)
                remember_channel(entry, q)
                return
            _set_status(song, "downloading", progress=0, **fields)
            # 先下到 <id>.mp4,确认不是静态画面才替换旧成品
            path = downloader.download(url, MEDIA_DIR, song["id"], make_progress(song))
            if not downloader.is_static_video(path):
                _finish_download(song, path, url)
                remember_channel(entry, q)
                return
            # 一张封面配音频,不算 MV:删掉,排除这个链接后换下一个候选
            downloader.cleanup_song_files(MEDIA_DIR, song["id"])
            with lock:
                rejected.append(url)
                song["status"] = "searching"
                save_state()
        review = [_candidate(*c) for c in ranked
                  if c[0] >= downloader.REVIEW_MIN
                  and downloader._entry_url(c[1]) not in rejected][:3]
        if review and any(c["url"] == song.get("video_url") for c in review) and _file_exists(song):
            # 复查:现有视频仍在候选里且没有更好的 → 保持原样
            _set_status(song, "done", progress=100, recheck=False)
            return
        with lock:
            if song.get("video_file"):
                drop_static_media(song)
            # 没有可用视频了:清掉旧视频信息,免得界面上还显示错的标题
            song.update(video_url=None, video_title=None, channel=None, score=None)
            save_state()
        if review:
            # 有像样但不够确定的候选:交给用户在「待确认」里挑
            _set_status(song, "review", candidates=review)
        else:
            _set_status(song, "no_mv", candidates=None)
    except Exception as e:
        global cooldown_until
        msg = str(e)
        bot_check = getattr(e, "bot_check", False) or "Sign in to confirm" in msg or "not a bot" in msg
        transient = (getattr(e, "transient", False) or bot_check or "HTTP Error 403" in msg
                     or "Connection reset" in msg or "timed out" in msg)
        with lock:
            if not song.get("forced") and _file_exists(song) and song.get("video_url"):
                # 复查/换版本时失败:原视频还在,先恢复可播放,到点再自动复查
                song.update(before, status="done", progress=100, recheck=True)
            else:
                song["status"] = "failed"
            if bot_check:
                # 整个队列冷却一段时间,别让后面的歌挨个撞墙
                cooldown_until = time.time() + BOT_COOLDOWN
            if transient:
                # 暂时性故障,定时自动重试:15分钟起,逐次翻倍,最多8轮
                song["retry_count"] = song.get("retry_count", 0) + 1
                wait_min = min(60, 15 * (2 ** (song["retry_count"] - 1)))
                cause = msg[:180]
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


def local_settings_request():
    # 登录状态属于运行后台的 Mac；不允许局域网或跨站页面更改它。
    from urllib.parse import urlparse
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return False
    if urlparse(request.host_url).hostname not in ("localhost", "127.0.0.1", "::1"):
        return False
    origin = request.headers.get("Origin")
    return not origin or origin == request.host_url.rstrip("/")


@app.route("/api/download-settings", methods=["GET", "POST"])
def api_download_settings():
    if not local_settings_request():
        return jsonify({"error": "请在运行 MV 播放器的 Mac 上打开下载设置"}), 403
    if request.method == "POST":
        try:
            settings = downloader.save_settings(request.get_json(silent=True))
        except (ValueError, TypeError) as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(settings)
    return jsonify({**downloader.get_settings(), "warning": downloader.account_warning})


connection_check_lock = threading.Lock()


@app.route("/api/download-settings/check", methods=["POST"])
def api_check_download():
    if not local_settings_request():
        return jsonify({"error": "请在运行 MV 播放器的 Mac 上检测连接"}), 403
    if not connection_check_lock.acquire(blocking=False):
        return jsonify({"error": "正在检测连接，请稍候"}), 409
    try:
        return jsonify(downloader.check_connection())
    finally:
        connection_check_lock.release()


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
                entries.extend({"artist": a, "title": t, "duration": d}
                               for a, t, d in fetch_apple_tracks(m.group(0)))
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
                    "duration": item.get("duration"),
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


@app.route("/api/songs/<song_id>/choose", methods=["POST"])
def api_choose_candidate(song_id):
    """「待确认」的歌:用户选定一个候选(url),或都不要(url 为空 → 标记无 MV)。"""
    url = (request.get_json(silent=True) or {}).get("url")
    with lock:
        for s in state["songs"]:
            if s["id"] != song_id:
                continue
            if s["status"] != "review":
                return jsonify({"error": "这首歌不在待确认状态"}), 409
            if not url:
                s["status"] = "no_mv"
                s["candidates"] = None
                save_state()
                return jsonify({"ok": True})
            cand = next((c for c in s.get("candidates") or [] if c["url"] == url), None)
            if cand is None:
                return jsonify({"error": "不是候选视频"}), 400
            s.update(video_url=url, video_title=cand.get("title"), channel=cand.get("channel"),
                     forced=True, status="pending", progress=0, error=None, candidates=None)
            save_state()
            wake.set()
            return jsonify({"ok": True})
    return jsonify({"error": "歌曲不存在"}), 404


@app.route("/api/songs/<song_id>/retry", methods=["POST"])
def api_retry_song(song_id):
    with lock:
        for s in state["songs"]:
            if s["id"] == song_id and s["status"] in ("no_mv", "failed", "review"):
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


def lan_ip():
    """本机在局域网里的 IP(不真正发包,只是让系统选出口地址)。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def main():
    load_state()
    migrate_filenames()
    migrate_zh_subs()
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=media_backfill, daemon=True).start()
    threading.Thread(target=lambda: (audit_library(), static_backfill()), daemon=True).start()
    print("本机访问:  http://127.0.0.1:%d" % PORT)
    ip = lan_ip()
    if ip:
        print("同一 Wi-Fi 的手机/iPad 访问:  http://%s:%d" % (ip, PORT))
    app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
