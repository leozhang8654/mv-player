# -*- coding: utf-8 -*-
"""用 yt-dlp 搜索并下载官方 MV。

搜索策略:在 YouTube(备选 Bilibili)上搜索,对每个结果按
标题关键词/歌名匹配/时长等打分,得分达到阈值才认为是官方 MV。
"""
import json
import os
import re
import subprocess
import shutil
import threading
import unicodedata
import urllib.parse
from urllib.parse import urlparse

YTDLP = "yt-dlp"
SEARCH_COUNT = 8
ACCEPT_THRESHOLD = 8

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download-settings.json")
SETTINGS_LOCK = threading.RLock()
DEFAULT_SETTINGS = {"browser": "chrome", "profile": "", "allow_variant": True}
account_warning = ""


def get_settings():
    with SETTINGS_LOCK:
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                return validate_settings(json.load(f))
        except (OSError, ValueError, TypeError):
            return dict(DEFAULT_SETTINGS)


def validate_settings(data):
    if not isinstance(data, dict):
        raise ValueError("下载设置格式错误")
    browser = data.get("browser", "chrome")
    profile = data.get("profile", "")
    if browser not in ("chrome", "edge", "safari", "firefox", "none"):
        raise ValueError("不支持的浏览器")
    if not isinstance(profile, str) or not re.fullmatch(r"[\w .-]{0,80}", profile):
        raise ValueError("配置名称只能包含文字、数字、空格、点、下划线或连字符")
    allow_variant = data.get("allow_variant", True)
    if not isinstance(allow_variant, bool):
        raise ValueError("allow_variant 必须是布尔值")
    return {"browser": browser, "profile": profile.strip() if browser not in ("none", "safari") else "",
            "allow_variant": allow_variant}


def save_settings(data):
    global account_warning
    settings = validate_settings(data)
    with SETTINGS_LOCK:
        with open(SETTINGS_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False)
        os.replace(SETTINGS_FILE + ".tmp", SETTINGS_FILE)
        account_warning = ""
    return settings


def _is_youtube(target):
    host = (urlparse(target).hostname or "").lower()
    return (target.startswith("ytsearch") or host == "youtu.be"
            or host == "youtube.com" or host.endswith(".youtube.com"))


def _cookie_error(text):
    text = text.lower()
    return any(s in text for s in (
        "cookies database", "cookie database", "failed to decrypt", "could not decrypt",
        "keychain", "operation not permitted", "permission denied", "cookies from safari"))


def error_message(text):
    if _cookie_error(text):
        return ("无法读取浏览器登录状态。请确认已登录 YouTube；若 macOS 拒绝访问，"
                "在系统设置 → 隐私与安全性中允许后台下载程序访问 Chrome 数据，再重启后台服务。")
    if _is_bot_check(text):
        return "YouTube 要求登录或人机验证。请在 Chrome 打开该视频完成验证，再到下载设置检测连接。"
    if "403" in text or "PO Token" in text:
        return "YouTube 拒绝了视频流请求。请更新 yt-dlp，并在下载设置检查浏览器登录状态后重试。"
    if "Requested format is not available" in text or "challenge" in text.lower():
        return "无法解析可下载的视频格式。请更新 yt-dlp 和 Deno 后重试。"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    errors = [line for line in lines if "ERROR:" in line]
    # 不把带有签名参数的视频流 URL 展示到局域网页面。
    return re.sub(r"https?://\S+", "[链接]", " | ".join((errors or lines)[-2:]))[-400:] or "未知下载错误"


def _command(target, authenticated=True):
    cmd = [YTDLP, "--ignore-config", "--no-color", "--socket-timeout", "20",
           "--extractor-retries", "2", "--retries", "3"]
    if _is_youtube(target):
        deno = shutil.which("deno")
        if deno:
            cmd += ["--js-runtimes", "deno:" + deno]
        settings = get_settings()
        if authenticated and settings["browser"] != "none":
            browser = settings["browser"]
            if settings["profile"]:
                browser += ":" + settings["profile"]
            cmd += ["--cookies-from-browser", browser]
    return cmd


def _run(target, args, timeout=90):
    """账号优先；仅登录数据不可读时尝试公开内容，并保留提示。"""
    global account_warning
    cmd = _command(target)
    proc = subprocess.run(cmd + args + [target], capture_output=True, text=True, timeout=timeout)
    if proc.returncode and "--cookies-from-browser" in cmd and _cookie_error(proc.stderr):
        account_warning = error_message(proc.stderr) + " 当前已尝试未登录下载。"
        proc = subprocess.run(_command(target, False) + args + [target],
                              capture_output=True, text=True, timeout=timeout)
    return proc


def check_connection():
    """只检测解析与可用格式，不下载视频。"""
    global account_warning
    account_warning = ""
    target = "https://www.youtube.com/watch?v=DYptgVvkVLQ"
    try:
        proc = _run(target, ["--simulate", "--no-playlist", "--check-formats",
                             "-f", FORMAT, "--print", "%(title)s"], timeout=120)
        if proc.returncode:
            return {"ok": False, "message": error_message(proc.stderr), "warning": account_warning}
        return {"ok": True, "message": "测试视频解析成功，可用音视频格式已检测。",
                "warning": account_warning}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "message": "连接检测失败：" + error_message(str(e)), "warning": account_warning}


def _is_bot_check(text):
    return "Sign in to confirm" in text or "not a bot" in text


def _is_transient(text):
    """暂时性故障:人机验证 / 流被中途掐断(403) / 网络抖动,换登录态或稍后重试通常能过。"""
    return (_is_bot_check(text) or "HTTP Error 403" in text
            or "Connection reset" in text or "timed out" in text)


class DownloadError(RuntimeError):
    def __init__(self, raw):
        self.bot_check = _is_bot_check(raw)
        self.transient = _is_transient(raw)
        super().__init__(error_message(raw))

# 视频高度上限 1080,优先 h264/mp4(浏览器兼容性最好)
FORMAT = "bv*[height<=1080]+ba/b[height<=1080]"
FORMAT_SORT = "res:1080,vcodec:h264,acodec:m4a,ext:mp4:m4a"

# 命中即扣分的词(说明不是官方 MV 或不是原版)
BAD_WORDS = [
    "cover", "翻唱", "翻奏", "lyric", "歌词", "歌詞", "karaoke", "伴奏",
    "instrumental", "reaction", "live", "现场", "現場", "演唱会", "演唱會",
    "concert", "remix", "纯音乐", "純音樂", "dance practice", "教学", "教程",
    "tutorial", "铃声", "鈴聲", "sped up", "speed up", "slowed", "nightcore",
    "8d", "piano", "钢琴", "吉他", "guitar", "合集", "串烧", "串燒", "audio",
    "acoustic", "ballroom", "unplugged", "orchestral", "reverb", "first take",
]

# 命中即加分的词
GOOD_WORDS = [
    ("official", 3), ("music video", 3), ("mv", 3), ("m/v", 3), ("官方", 2),
]

# 「确实是 MV」的证据词:一个都没有(且标题不是标准「歌手 - 歌名」命名)就重罚,
# 避免把 YouTube 自动生成的纯音频 Art Track / Official Audio 当成官方 MV
MV_EVIDENCE = ["mv", "m/v", "music video", "official video", "官方", "videoclip"]

# 频道名里出现这些词说明是搬运/粉丝/歌词频道,不是官方
FAN_CHANNEL_WORDS = ["fanmade", "fan made", "unofficial", "reupload", "re-upload", "非公式", "搬运",
                     "fan", "fanpage", "tribute", "lyrics", "lyric", "letra", "歌词", "歌詞"]

# 标题里出现这些词直接视为非官方
FAN_TITLE_WORDS = ["unofficial", "fanmade", "fan made", "fan video", "非官方", "非公式", "自制"]

# 标题去掉歌手/歌名/修饰词后还剩这些 → 不是 MV 本身(制作过程、合集、串烧、AI 生成……),直接否决
JUNK_WORDS = [
    "how i made", "making of", "behind the scenes", "compilation", "meme", "mashup", "mash up",
    "transition", "reaction", "tutorial", "ai", "amv", "teaser", "trailer", "snippet", "preview",
    "interview", "loop", "1 hour", "10 hours", "fan edit", "fanmade", "fan made", "tiktok", "shorts",
    "style",  # 「Alan Walker Style」之类的仿作
]

# 判断「剩余内容」时忽略的修饰词
DECOR_WORDS = set("""
official officiel oficial video videos videoclip clip music musicvideo mv m v pv hd hq 4k 8k uhd
1080p 720p 60fps full remastered remaster upscaled restored version ver shortened original explicit
clean uncensored ft feat featuring prod produced by dir directed the a an and with from x vevo
visualizer visualiser animated animation short film movie out now new single premiere
官方 高清 完整版 正式版 版 中字 字幕 中文 官方版 mv版 ミュージックビデオ 뮤직비디오
""".split())

# 二次判定时在视频描述/标签里找的 MV 证据(官方 MV 的描述常带职员表)
DEEP_EVIDENCE = MV_EVIDENCE + [
    "アニメーション", "映像", "director", "監督", "illustration", "illust",
    "animation", "movie", "出演", "动画",
]
# 描述里出现这些词直接判非官方
DEEP_REJECT = ["auto-generated by youtube", "provided to youtube by",
               "歌ってみた", "cover by", "covered by"]

REJECT = -99          # 歌名对不上 / 垃圾内容:任何加分都救不回来
REVIEW_MIN = 3        # 未达标但不低于此分的候选交给用户确认

_FEAT_RE = re.compile(
    r"[(\[（【]\s*(?:feat|ft)\.?[^)\]）】]*[)\]）】]?|(?:feat|ft)\.\s*.*$",
    re.IGNORECASE,
)


def _strip_feat(s):
    """去掉 (feat. xxx) 从句 —— feat 的写法千变万化(& / and / 位置不同),不参与匹配。"""
    return _FEAT_RE.sub("", s or "").strip()


def _feat_names(title):
    """从歌名的 feat. 从句里提取演唱者(V曲的歌姬名是重要匹配线索)。"""
    m = _FEAT_RE.search(title or "")
    if not m:
        return []
    inner = re.sub(r"^[(\[(【]?\s*(?:feat|ft)\.?\s*", "", m.group(0), flags=re.IGNORECASE)
    inner = inner.strip(")])】 ")
    return _artist_parts(inner)


def _artist_parts(artist):
    """把「A, B & C feat. D」拆成单个歌手,多人协作曲按任意一人命中计分。"""
    parts = [p.strip() for p in
             re.split(r"[,&×/、]|\bfeat\.?\b|\bft\.?\b", artist or "", flags=re.IGNORECASE)
             if p.strip()]
    return parts or ([artist] if artist else [])


try:  # 繁简体统一成简体再比较(官方MV标题常用繁体)
    from opencc import OpenCC
    _t2s = OpenCC("t2s").convert
except Exception:
    _t2s = lambda s: s


def _fold(s):
    """繁转简、转小写、去掉重音符号(GIVĒON = Giveon, Beyoncé = Beyonce)。"""
    s = unicodedata.normalize("NFKD", _t2s(s or "").lower())
    return unicodedata.normalize("NFKC", "".join(c for c in s if not unicodedata.combining(c)))


def _norm(s):
    """繁转简、去掉空白和标点、转小写 —— 用于中英文歌名的宽松匹配。"""
    return re.sub(r"[\W_]+", "", _fold(s), flags=re.UNICODE)


def _words(s):
    """繁转简、转小写、标点换成空格 —— 用于按整词匹配。"""
    return " ".join(re.findall(r"[^\W_]+", _fold(s)))


_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


def _contains_phrase(text, phrase):
    """phrase 作为完整词组出现在 text 里(避免 Bad 命中 Badguy);中日韩文按子串。"""
    p = _words(phrase)
    if not p:
        return False
    t = _words(text)
    if _CJK_RE.search(p):
        return p.replace(" ", "") in t.replace(" ", "")
    return (" %s " % p) in (" %s " % t)


def _has_word(text, word):
    """latin 单词按词边界匹配(避免 live 命中 alive),容忍复数;中文繁转简后子串匹配。"""
    if re.fullmatch(r"[a-z0-9/ ]+", word):
        return re.search(r"(?<![a-z])" + re.escape(word) + r"s?(?![a-z])", text) is not None
    return word in _t2s(text)


# ---------- 歌名的版本后缀 ----------
# 不影响内容的后缀:去掉后仍是同一首歌的同一版本
_SAFE_SUFFIX_RE = re.compile(
    r"remaster|radio\s*(?:edit|version|mix)|single\s*(?:version|edit)|album\s*version"
    r"|bonus\s*track|explicit|clean|\bmono\b|\bstereo\b|original\s*(?:mix|version)"
    r"|^\s*from\b|deluxe|video\s*(?:edit|version)", re.I)
# 改变内容的后缀:这一版没有 MV 时可退而用原版 MV(扣分)
_VARIANT_RE = re.compile(
    r"remix|\bmix\b|version|\bedit\b|rework|\bvip\b|acoustic|\blive\b|slowed|reverb|sped\s*up"
    r"|nightcore|instrumental|\bdemo\b|extended|cover|unplugged|orchestral|piano", re.I)
_GROUP_RE = re.compile(r"\s*(?:[(\[（【「『]([^)\]）】」』]*)[)\]）】」』]|\s[-–—]\s+(.+)$)")
VARIANT_PENALTY = 2


def title_forms(title, allow_variant=True):
    """歌名的几种可接受写法 [(词组, 扣分)],从严到宽。

    1. 原名(去 feat.)  2. 再去掉 Remaster / Radio Edit 等无关后缀
    3. 去掉所有括号和横线后缀(Remix / Version 等不同版本扣分,可由设置关闭)
    """
    base = _strip_feat(title) or title
    forms = [(base, 0)]
    safe, loose, variant = base, base, False
    for m in _GROUP_RE.finditer(base):
        inner = (m.group(1) or m.group(2) or "").strip()
        if _SAFE_SUFFIX_RE.search(inner):
            safe = safe.replace(m.group(0), " ")
        elif _VARIANT_RE.search(inner):
            variant = True
        loose = loose.replace(m.group(0), " ")
    for text, pen in ((safe, 0), (loose, VARIANT_PENALTY if variant else 0)):
        text = re.sub(r"\s+", " ", text).strip(" -–—")
        if len(_norm(text)) < 2 or any(_norm(text) == _norm(f) for f, _ in forms):
            continue
        if pen and not allow_variant:
            continue
        forms.append((text, pen))
    return forms


class SongQuery:
    """一首歌的检索上下文:可接受的歌名写法、歌手别名、参考时长。"""

    def __init__(self, title, artist, catalog=None, duration=None, allow_variant=True):
        catalog = catalog or {}
        self.title, self.artist = title, artist or ""
        self.duration = duration or catalog.get("duration")
        self.forms = list(title_forms(title, allow_variant))
        for alt in catalog.get("titles") or []:
            for f, p in title_forms(alt, allow_variant):
                if not any(_norm(f) == _norm(x) for x, _ in self.forms):
                    self.forms.append((f, p))
        self.forms.sort(key=lambda fp: fp[1])
        names = [self.artist] + list(catalog.get("artists") or [])
        self.artists = []
        for n in names:
            for p in [n] + _artist_parts(n):
                if len(_norm(p)) >= 2 and p not in self.artists:
                    self.artists.append(p)
        self.feats = _feat_names(title)
        self.requested = (title + " " + self.artist).lower()
        own = title_forms(title, allow_variant)
        # 搜索用的核心歌名:去掉 Remaster 等无关后缀(不用别名,别名单独成搜索词)
        self.core = own[1][0] if len(own) > 1 and own[1][1] == 0 else own[0][0]
        self.alt_pairs = [(a, t) for a, t in (catalog.get("pairs") or [])]

    def match_title(self, text):
        """返回命中的 (歌名写法, 扣分),都没命中返回 None。"""
        for form, pen in self.forms:
            if _contains_phrase(text, form):
                return form, pen
        return None

    def artist_hit(self, *texts):
        """任意一位歌手(含别名)出现在任意一段文本里。"""
        normed = [_norm(t) for t in texts if t]
        return any(_norm(a) in n for a in self.artists for n in normed)


def _channel_core(channel):
    """频道名去掉首尾的 official / VEVO / music / channel 等修饰,便于和歌手名比对。"""
    c = _norm(channel)
    decor = ("official", "vevo", "music", "channel", "tv", "官方", "官方频道")
    changed = True
    while changed and c:
        changed = False
        for d in decor:
            if c.endswith(d) and len(c) > len(d):
                c, changed = c[:-len(d)], True
            if c.startswith(d) and len(c) > len(d):
                c, changed = c[len(d):], True
    return c


def artist_channel_keys(channel, q):
    """频道是哪些歌手本人的频道(返回归一化的歌手名集合;合办频道如「YOASOBI and Echoes」也算)。"""
    if not channel:
        return set()
    names = {_norm(a) for a in q.artists}
    pieces = [channel] + re.split(r"\s+(?:and|x|×|&|-)\s+|[,&×/、]", channel, flags=re.I)
    return {_channel_core(p) for p in pieces if p.strip()} & names


def channel_kind(channel, q):
    """'artist':歌手本人频道;'contains':频道名含歌手名但还有别的字(可能是粉丝号);None:无关。"""
    if not channel:
        return None
    if artist_channel_keys(channel, q):
        return "artist"
    return "contains" if q.artist_hit(channel) else None


def _strip_names(raw_title, q, matched):
    """标题去掉歌手、歌名、feat. 演唱者后剩下的文字(前后带空格,便于整词查找)。"""
    s = " %s " % _words(raw_title)
    for phrase in [matched] + q.artists + q.feats:
        p = _words(phrase)
        if not p:
            continue
        s = s.replace(p, " ") if _CJK_RE.search(p) else s.replace(" %s " % p, " ")
    return " %s " % " ".join(s.split())


def extra_content(raw_title, q, matched):
    """标题去掉歌手、歌名、feat.、修饰词后剩下的词。"""
    return [w for w in _strip_names(raw_title, q, matched).split()
            if w not in DECOR_WORDS and not w.isdigit() and len(w) > 1]


# 说明是「另一个版本」的词(BAD_WORDS 的子集):原版存在时应让位
VARIANT_WORDS = ["remix", "live", "acoustic", "ballroom", "unplugged", "orchestral", "slowed",
                 "sped up", "reverb", "nightcore", "instrumental", "piano", "cover", "first take"]


def variant_words(raw_title, q):
    """标题里有、但请求的歌名里没有的版本词(如原曲请求却搜到 Acoustic 版)。"""
    t = raw_title.lower()
    return [w for w in VARIANT_WORDS if _has_word(t, w) and not _has_word(q.requested, w)]


def extra_feats(raw_title, q):
    """视频标题 feat. 的歌手里,请求的歌手/feat. 名单中都没有的那些。"""
    known = [_norm(a) for a in q.artists + q.feats]
    out = []
    for part in _feat_names(raw_title):
        n = _norm(re.split(r"[(\[（【]", part)[0])
        if len(n) >= 2 and not any(k in n or n in k for k in known):
            out.append(part)
    return out


def content_verdict(raw_title, q):
    """只看标题判断内容:('reject', 原因) / ('ok', 命中的歌名写法)。"""
    hit = q.match_title(raw_title)
    if hit is None:
        return "reject", "歌名不符"
    words, p = " %s " % _words(raw_title), _words(hit[0])
    if not _CJK_RE.search(p) and re.search(r" %s x [^\W_]| [^\W_]+ x %s " % (re.escape(p), re.escape(p)), words):
        return "reject", "串烧:" + hit[0] + " x …"
    extra = _strip_names(raw_title, q, hit[0])
    for w in JUNK_WORDS:
        if (" %s " % w) in extra and not _has_word(q.requested, w):
            return "reject", "非 MV 内容:" + w
    return "ok", hit


def score_entry(entry, q):
    """给一条搜索结果打分;歌名对不上或是垃圾内容返回 REJECT。"""
    raw_title = entry.get("title") or ""
    t = raw_title.lower()
    channel = (entry.get("channel") or entry.get("uploader") or "").lower()
    duration = entry.get("duration") or 0
    verified = entry.get("channel_is_verified") is True

    verdict, hit = content_verdict(raw_title, q)
    if verdict == "reject":
        return REJECT
    form, form_penalty = hit

    score = 4 - form_penalty
    kind = channel_kind(channel, q)
    if q.artist_hit(raw_title, channel):
        score += 2
    # 歌手本人频道:官方性最强的信号之一
    if kind == "artist":
        score += 2
    # feat. 的演唱者(如V曲歌姬)出现在结果标题里(官方惯例「曲名 / 重音テト」)
    if any(_contains_phrase(raw_title, v) for v in q.feats):
        score += 2
    # 标题恰好是标准「歌手 - 歌名」原样(厂牌官方上传的惯用命名,粉丝上传通常会加料)
    n_entry, n_entry_core = _norm(raw_title), _norm(_strip_feat(raw_title))
    nt = _norm(form)
    canonical = kind is not None and any(
        n in (_norm(a) + nt, nt + _norm(a)) for a in q.artists for n in (n_entry, n_entry_core))
    if canonical:
        score += 3
    has_evidence = any(_has_word(t, w) for w in MV_EVIDENCE)
    if has_evidence:
        score += 1  # 明说是 MV 的优先(同分时胜过纯音频/Visualizer)
    if (_has_word(t, "visualizer") or _has_word(t, "visualiser")) and not _has_word(q.requested, "visualizer"):
        score -= 2  # Visualizer 能看,但有正式 MV 时应让位
    for word, pts in GOOD_WORDS:
        if _has_word(t, word):
            score += pts
    bad_hit = False
    for word in BAD_WORDS:
        if _has_word(q.requested, word):  # 歌名本身含这个词时不扣分
            continue
        if _has_word(t, word):
            score -= 4
            bad_hit = True
    # 不同版本(Acoustic / Live / Remix…)再额外扣分,确保原版 MV 存在时一定胜出,
    # 只有这一版时也大多落到「待确认」而不是自动下载
    if variant_words(raw_title, q):
        score -= 4
    # 视频标题里 feat. 了请求里没有的歌手 → 多半是另一个版本(合作 Remix 版),扣分
    if extra_feats(raw_title, q):
        score -= 3
    # 缺 MV 证据词的罚分:标准命名、或歌手本人频道上传(且无负面词)可豁免
    if not canonical and not (kind == "artist" and not bad_hit) and not has_evidence:
        score -= 5
    if "official" in channel or verified:
        score += 1
    if channel.endswith(" - topic"):
        score -= 5  # YouTube 自动生成的纯音频频道
    if any(_has_word(channel, w) for w in FAN_CHANNEL_WORDS):
        score -= 6
    if any(_has_word(t, w) for w in FAN_TITLE_WORDS):
        score -= 6
    # 上传账号可信度:歌手本人频道 / 认证账号 / 名字带 official·官方·VEVO 不罚;
    # 频道名只是「包含」歌手名(如「某某 Fan France」)罚一半;完全无关罚满
    if not (kind == "artist" or (verified and (kind or has_evidence)) or "official" in channel
            or "官方" in channel or "vevo" in channel):
        score -= 2 if kind == "contains" else 4
    # 标题里多出一大串无关的词(其他歌名、合作说明……)轻微扣分
    if len(extra_content(raw_title, q, form)) >= 4:
        score -= 1
    score += _duration_score(duration, q.duration)
    return score


def _duration_score(duration, ref):
    """有参考时长(Apple Music / iTunes)时按与原曲的差距打分,否则用通用区间。"""
    if not duration:
        return 0
    if ref:
        if duration < 0.6 * ref:
            return -6   # 片段 / Shorts / 预告:不到原曲六成长,基本不是完整 MV
        if duration < 0.8 * ref:
            return -1   # MV 常用较短的 Radio Edit 剪辑,轻罚
        if duration > ref + 240:
            return -5   # 合集 / 十几分钟的长篇电影版(有正常长度的 MV 时应让位)
        if duration > ref + 90:
            return -1   # 带剧情前奏的 MV 常见,轻罚
        # 不给「时长完全吻合」加分:和原曲一样长的往往正是纯音频上传,MV 常带前奏
        return 0
    if 60 <= duration <= 720:
        return 1
    return -3 if duration < 60 else -2


# 各来源使用的搜索词模板,从精确到宽泛逐级降级
# ({base}=歌手+核心歌名;{title}=纯核心歌名,应对歌手写法和频道用名对不上的情况)
QUERY_TEMPLATES = {
    "youtube": ["{base} official MV", "{base} official video", "{base} MV", "{title}"],
    "bilibili": ["{base} 官方MV", "{base} MV", "{title}"],
}
SEARCH_PREFIX = {"youtube": "ytsearch", "bilibili": "bilisearch"}


# ---------- 搜索结果缓存(离线回归测试用) ----------
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "search-cache")
# record:正常联网并记录;replay:只读缓存(缺失视为无结果);online:读缓存,缺失再联网并记录
CACHE_MODE = "record"
LIVE_CALLS = 0  # 实际联网次数(评估工具据此决定要不要放慢)


def _cache_path(kind, key):
    import hashlib
    return os.path.join(CACHE_DIR, kind, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")


def cached(kind):
    """给联网函数套一层缓存;第一个参数(或 key 参数)作为缓存键。"""
    def wrap(fn):
        def inner(*args, **kwargs):
            key = "|".join(str(a) for a in args)
            path = _cache_path(kind, key)
            if CACHE_MODE in ("replay", "online") and os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    return json.load(f)["value"]
            if CACHE_MODE == "replay":
                return None
            global LIVE_CALLS
            LIVE_CALLS += 1
            value = fn(*args, **kwargs)
            if value is None:
                return None  # 失败(被拦/超时)不记录,下次重新请求
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"key": key, "value": value}, f, ensure_ascii=False)
            except OSError:
                pass
            return value
        inner.__wrapped__ = fn
        return inner
    return wrap


def _parse_entries(stdout):
    out = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        url = e.get("url") or e.get("webpage_url") or ""
        if e.get("_type") in (None, "url", "video") and "/playlist" not in url and "list=" not in url:
            out.append({k: e.get(k) for k in (
                "url", "webpage_url", "title", "channel", "uploader", "channel_id",
                "channel_is_verified", "duration", "view_count")} | {"thumbnail": _thumb(e)})
    return out


def _thumb(e):
    thumbs = e.get("thumbnails") or []
    if thumbs:
        return thumbs[-1].get("url")
    return e.get("thumbnail")


@cached("search")
def _search_target(target):
    args = ["--dump-json", "--flat-playlist", "--no-warnings", "--ignore-errors",
            "--sleep-requests", "0.5", "--playlist-end", str(SEARCH_COUNT)]
    proc = _run(target, args, timeout=90)
    entries = _parse_entries(proc.stdout)
    # 一条结果都没有且 yt-dlp 报错 → 是搜索本身失败(断网/被拦),不是「搜不到」
    if not entries and proc.returncode != 0:
        raise DownloadError(proc.stderr)
    return entries


def _search(source, query, n=SEARCH_COUNT):
    return _search_target("%s%d:%s" % (SEARCH_PREFIX[source], n, query)) or []


def channel_search(channel_id, query):
    """在已知的官方频道内搜索(频道搜索页)。"""
    url = "https://www.youtube.com/channel/%s/search?query=%s" % (
        channel_id, urllib.parse.quote(query))
    return _search_target(url) or []


@cached("deep")
def _deep_info(url):
    proc = _run(url, ["-j", "--no-warnings", "--no-playlist"], timeout=60)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        e = json.loads(proc.stdout.splitlines()[0])
    except ValueError:
        return None
    return {k: e.get(k) for k in ("description", "tags", "channel_is_verified",
                                  "channel_follower_count", "channel_id", "channel")}


def deep_check(url, q):
    """拉取单个视频的完整元数据,以上传账号可信度为核心做二次判定。

    返回 {"bonus": 加减分, "artist": 描述里是否出现歌手, "channel_id", "verified"};
    返回 None 表示确认是纯音频/翻唱,直接否决。
    """
    e = _deep_info(url)
    if not e:
        return {"bonus": 0, "artist": False, "channel_id": None, "verified": None}
    desc = e.get("description") or ""
    if any(w in desc.lower() for w in DEEP_REJECT):
        return None
    blob = (desc + " " + " ".join(e.get("tags") or [])).lower()
    verified = e.get("channel_is_verified") is True
    followers = e.get("channel_follower_count")
    related = channel_kind(e.get("channel") or "", q) is not None
    # 粉丝多不代表官方(推广号动辄百万订阅):只对和歌手相关的频道算数
    trusted = verified or (related and (followers or 0) >= 100_000)
    bonus = 0
    if trusted:
        bonus += 4  # 认证账号或 10 万+ 订阅:可信的官方/厂牌
        if any(_has_word(blob, w) for w in DEEP_EVIDENCE):
            bonus += 3  # 且描述/标签里有 MV 证据
    elif followers is not None and followers < 50_000:
        bonus -= 3  # 明确的小号:大概率搬运/自制
    artist_in_desc = bool(q.artist) and q.artist_hit(blob)
    if artist_in_desc:
        bonus += 2  # 描述职员表里出现了歌手
    return {"bonus": bonus, "artist": artist_in_desc,
            "channel_id": e.get("channel_id"), "verified": verified}


def fetch_video_info(url):
    """拉取单个视频的标题、频道等信息(通过链接添加歌曲、学习官方频道用),失败返回 None。"""
    proc = _run(url, ["-j", "--no-warnings", "--no-playlist"], timeout=60)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        e = json.loads(proc.stdout.splitlines()[0])
    except ValueError:
        return None
    return {"title": e.get("title"), "channel": e.get("channel") or e.get("uploader"),
            "channel_id": e.get("channel_id"), "verified": e.get("channel_is_verified") is True}


def fetch_subs(url, media_dir, song_id):
    """只抓字幕不下视频(给历史下载补字幕用)。

    返回 True 表示请求成功(即使该视频没有字幕),False 表示被拦/失败。
    """
    template = os.path.join(media_dir, "%s.%%(ext)s" % song_id)
    args = [
        "--skip-download", "--write-subs",
        "--sub-langs", "all,-live_chat", "--convert-subs", "vtt",
        "-o", template, "--no-playlist", "--no-warnings",
    ]
    try:
        proc = _run(url, args, timeout=120)
    except Exception:
        return False
    if proc.returncode != 0:
        return False
    return True


def _entry_url(e):
    return e.get("url") or e.get("webpage_url")


def _queries(source, q):
    """按从精确到宽泛排好的搜索词。"""
    base = ("%s %s" % (q.artist, q.core)).strip()
    queries = [t.format(base=base, title=q.core) for t in QUERY_TEMPLATES[source]]
    extra = []
    # 原文歌名/歌手(如 YOASOBI「群青」、TK from 凛として時雨)
    for a, t in q.alt_pairs:
        extra.append("%s %s" % (a, t))
    # V曲官方标题惯例「曲名 / 歌姬」,用歌姬名搜通常一击即中
    if q.feats:
        extra.append("%s %s" % (q.core, q.feats[0]))
    queries[1:1] = extra
    # 这一版没有 MV 时,用原版歌名再搜一次
    loose = [f for f, p in q.forms if p]
    if loose:
        queries.append("%s %s official video" % (q.artist, loose[0]))
    seen, out = set(), []
    for x in queries:
        if x.strip() and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def search_mv(q, sources, exclude=(), channels=()):
    """搜索并打分,返回按分数从高到低排好的候选 [(score, entry, source)](已去掉被否决的)。

    channels 是该歌手已知的官方频道 [(channel_id, 频道名)],先在频道内搜;
    任一阶段出现达标结果就不再继续往宽泛的搜索词降级。
    """
    scored = {}
    search_ok = False
    last_err = None

    def add(entries, source, channel_name=None):
        for e in entries:
            url = _entry_url(e)
            if not url or url in exclude:
                continue
            if channel_name and not e.get("channel"):
                e = dict(e, channel=channel_name)
            s = score_entry(e, q)
            if s <= REJECT:
                continue
            e = dict(e, _raw=s)
            if q.artist and not q.artist_hit(e.get("title") or "", e.get("channel") or e.get("uploader") or ""):
                # 同名但别的歌手的歌(如翻唱版请求搜到原唱 MV):不自动下载,至多待确认
                s = min(s, ACCEPT_THRESHOLD - 1)
            if url not in scored or s > scored[url][0]:
                scored[url] = (s, e, source)

    def good_enough():
        return any(s >= ACCEPT_THRESHOLD for s, _, _ in scored.values())

    for channel_id, channel_name in channels:
        for title in dict.fromkeys(f for f, p in q.forms if not p):
            try:
                add(channel_search(channel_id, title), "youtube", channel_name)
                search_ok = True
            except Exception as e:
                last_err = e
        if good_enough():
            break
    for source in sources:
        if good_enough():
            break
        for query in _queries(source, q):
            try:
                entries = _search(source, query)
                search_ok = True
            except Exception as e:
                last_err = e
                continue
            add(entries, source)
            if good_enough():
                break
    if not search_ok:
        # 所有来源都没搜成功 → 环境问题,抛错让歌进入 failed(可重试),而不是误标「无MV」
        if last_err:
            raise last_err
        raise RuntimeError("搜索请求全部失败，请检查网络")

    ranked = sorted(scored.values(), key=lambda c: -c[0])
    if ranked and ranked[0][0] < ACCEPT_THRESHOLD:
        # 差一点达标的候选(典型:官方上传但标题没写 MV)→ 拉完整元数据二次判定。
        # 歌名已经过硬门槛,二次判定只能给「对的歌」加分,救不回错歌
        seen_channels, picked = set(), []
        for c in ranked:
            if c[0] < 0:
                break
            ch = (c[1].get("channel") or c[1].get("uploader") or "").lower()
            if ch in seen_channels:
                continue
            seen_channels.add(ch)
            picked.append(c)
            if len(picked) >= 3:
                break
        for s0, e0, src0 in picked:
            url0 = _entry_url(e0)
            try:
                r = deep_check(url0, q)
            except Exception:
                continue
            if r is None:
                scored.pop(url0, None)
                continue
            e1 = dict(e0, channel_id=e0.get("channel_id") or r["channel_id"],
                      channel_is_verified=e0.get("channel_is_verified") or r["verified"])
            s1 = e0.get("_raw", s0) + r["bonus"]
            # 走二次判定通道达标必须确认过歌手(标题/频道/描述任一处)
            artist_ok = (not q.artist) or r["artist"] or q.artist_hit(
                e0.get("title") or "", e0.get("channel") or e0.get("uploader") or "")
            if s1 >= ACCEPT_THRESHOLD and not artist_ok:
                s1 = ACCEPT_THRESHOLD - 1
            scored[url0] = (s1, e1, src0)
        ranked = sorted(scored.values(), key=lambda c: -c[0])
    return ranked


def find_mv(title, artist, sources, exclude=(), q=None, channels=()):
    """返回得分最高的 (score, entry, source),没有任何结果时返回 None。"""
    ranked = search_mv(q or SongQuery(title, artist), sources, exclude, channels)
    return ranked[0] if ranked else None


# 静态画面判定:每 5 秒取一帧缩成小灰度图,相邻帧明显不同的比例低于此值即视为
# 「一张封面配音频」(Art Track / Official Audio),真正的 MV 和动态 Visualizer 通常 > 0.4
STATIC_SAMPLE_SECS = 5
STATIC_CHANGE_RATIO = 0.2
_SW, _SH = 32, 18


def is_static_video(path):
    """视频画面基本不动则返回 True;无法判定(太短/解码失败)返回 False。"""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path,
             "-vf", "fps=1/%d,scale=%d:%d,format=gray" % (STATIC_SAMPLE_SECS, _SW, _SH),
             "-f", "rawvideo", "-"],
            capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        return False
    n = _SW * _SH
    data = proc.stdout
    frames = [data[i:i + n] for i in range(0, len(data) - n + 1, n)]
    if len(frames) < 4:
        return False
    changed = sum(
        sum(abs(x - y) for x, y in zip(a, b)) / n > 4
        for a, b in zip(frames, frames[1:]))
    return changed / (len(frames) - 1) < STATIC_CHANGE_RATIO


def cleanup_song_files(media_dir, song_id, keep=None, keep_subs=False):
    """删除该歌曲的所有落盘文件(半成品、分流中间文件等)。

    keep 指定要保留的文件名;keep_subs=True 时保留 .vtt 字幕(等待归档)。
    """
    try:
        for name in os.listdir(media_dir):
            if not name.startswith(song_id + ".") or name == keep:
                continue
            if keep_subs and name.endswith(".vtt"):
                continue
            try:
                os.remove(os.path.join(media_dir, name))
            except OSError:
                pass
    except OSError:
        pass


def _stream_download(cmd, progress_cb):
    """执行下载命令,流式解析进度,返回 (退出码, 输出尾部)。"""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    tail = []
    for line in proc.stdout:
        tail.append(line.rstrip())
        tail = tail[-15:]
        m = re.search(r"\[download\]\s+([\d.]+)%", line)
        if m and progress_cb:
            try:
                progress_cb(float(m.group(1)))
            except Exception:
                pass
    return proc.wait(), tail


def _find_output(media_dir, song_id, code):
    """只接受成功退出且同时有音视频流的最终 MP4，避免半成品被误报成功。"""
    path = os.path.join(media_dir, "%s.mp4" % song_id)
    if code != 0 or not os.path.isfile(path) or os.path.getsize(path) == 0:
        return None
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", path],
            capture_output=True, text=True, timeout=30)
        kinds = {s.get("codec_type") for s in json.loads(probe.stdout).get("streams", [])}
        if probe.returncode or not {"audio", "video"}.issubset(kinds):
            return None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    cleanup_song_files(media_dir, song_id, keep="%s.mp4" % song_id, keep_subs=True)
    return path


def download(url, media_dir, song_id, progress_cb=None):
    """账号优先下载；字幕单独获取，字幕失败不影响视频。"""
    global account_warning
    template = os.path.join(media_dir, "%s.%%(ext)s" % song_id)
    args = [
        url, "-f", FORMAT, "-S", FORMAT_SORT,
        "--merge-output-format", "mp4", "--remux-video", "mp4",
        "-o", template, "--newline", "--no-playlist",
        "--limit-rate", "5M", "--sleep-requests", "0.75",
        "--fragment-retries", "3", "--abort-on-unavailable-fragments",
    ]
    cmd = _command(url)
    cleanup_song_files(media_dir, song_id)
    code, tail = _stream_download(cmd + args, progress_cb)
    if code and "--cookies-from-browser" in cmd and _cookie_error(" ".join(tail)):
        account_warning = error_message(" ".join(tail)) + " 当前已尝试未登录下载。"
        cleanup_song_files(media_dir, song_id)
        code, tail = _stream_download(_command(url, False) + args, progress_cb)
    result = _find_output(media_dir, song_id, code)
    if result:
        fetch_subs(url, media_dir, song_id)
        return result
    cleanup_song_files(media_dir, song_id)
    if code == 0:
        raise RuntimeError("下载文件不完整或缺少音视频流，请重试；请确认 ffmpeg / ffprobe 已安装。")
    raise DownloadError("\n".join(tail))
