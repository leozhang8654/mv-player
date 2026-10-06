# -*- coding: utf-8 -*-
"""用 iTunes Search API 查歌曲的参考时长和原文名(免费、无需 key)。

例:YOASOBI「Gunjou」在日区叫「群青」,TK from Ling tosite sigure 在日区叫
「TK from 凛として時雨」—— 官方 MV 标题用的是原文,只拿罗马音永远匹配不上。
做法:先在美区搜到这首歌拿 trackId,再按 id 到日/韩/台区查本地化名称。
"""
import json
import time
import urllib.parse
import urllib.request

import downloader

SEARCH_STORES = ["us", "jp", "kr", "tw"]   # 依次尝试搜索,找到为止
ALIAS_STORES = ["jp", "kr", "tw", "cn"]    # 找到后按 id 查这些区的本地化名称
API = "https://itunes.apple.com/"
PAUSE = 0.4  # iTunes 限流约 20 次/分钟


def _get(path, params):
    url = API + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "MVPlayer/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    time.sleep(PAUSE)
    return data.get("results") or []


def _pick(results, q):
    """在搜索结果里挑出这首歌:歌名任一写法对上,且歌手对上。"""
    for r in results:
        name, artist = r.get("trackName") or "", r.get("artistName") or ""
        title_ok = q.match_title(name) or any(
            downloader._contains_phrase(f, name) for f, p in q.forms if not p)
        if title_ok and (not q.artist or q.artist_hit(artist) or downloader.SongQuery(
                name, artist).artist_hit(q.artist)):
            return r
    return None


@downloader.cached("catalog")
def lookup(title, artist):
    """返回 {"duration": 秒, "titles": [原文歌名], "artists": [原文歌手], "pairs": [(歌手, 歌名)]};
    查不到这首歌返回 {};网络失败抛异常(调用方下次再试)。"""
    q = downloader.SongQuery(title, artist)
    term = ("%s %s" % (artist, q.core)).strip()
    hit = None
    for store in SEARCH_STORES:
        hit = _pick(_get("search", {"term": term, "entity": "song", "limit": 10,
                                    "country": store}), q)
        if hit:
            break
    if not hit:
        return {}
    out = {"duration": round(hit["trackTimeMillis"] / 1000) if hit.get("trackTimeMillis") else None,
           "titles": [], "artists": [], "pairs": []}
    seen_t, seen_a = {downloader._norm(title)}, {downloader._norm(artist)}
    for store in ALIAS_STORES:
        try:
            local = _get("lookup", {"id": hit["trackId"], "country": store})
        except Exception:
            continue
        if not local:
            continue
        name, who = local[0].get("trackName") or "", local[0].get("artistName") or ""
        nt, na = downloader._norm(name), downloader._norm(who)
        new = False
        if name and nt not in seen_t:
            seen_t.add(nt)
            out["titles"].append(name)
            new = True
        if who and na not in seen_a:
            seen_a.add(na)
            out["artists"].append(who)
            new = True
        if new:
            out["pairs"].append([who, downloader.SongQuery(name, who).core])
    return out
