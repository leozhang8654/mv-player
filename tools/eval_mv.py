# -*- coding: utf-8 -*-
"""MV 检测的离线回归评估。

用 tests/fixtures/mv_labels.json 里人工标注的对错,重放缓存的搜索结果,
统计改动打分规则后「下对 / 下错 / 待确认 / 漏掉」各多少首 —— 不用重新请求 YouTube。

  python3 tools/eval_mv.py            只用缓存(没缓存的歌按「无结果」算)
  python3 tools/eval_mv.py --online   缺缓存时联网搜索并记录(第一次建缓存用,较慢)
  python3 tools/eval_mv.py --only Jackson   只看歌手/歌名含该文字的歌

缓存在 search-cache/(后台下载时也会自动记录)。
"""
import argparse
import json
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import catalog  # noqa: E402
import downloader as dl  # noqa: E402

LABELS = os.path.join(ROOT, "tests", "fixtures", "mv_labels.json")


def decide(ranked):
    if ranked and ranked[0][0] >= dl.ACCEPT_THRESHOLD:
        return "accept", ranked[0]
    if any(c[0] >= dl.REVIEW_MIN for c in ranked):
        return "review", ranked[0]
    return "none", None


def classify(label, kind, pick):
    good = {v["url"] for v in label["good"]}
    bad = {v["url"] for v in label["bad"]}
    if kind == "accept":
        url = dl._entry_url(pick[1])
        if url in good:
            return "correct"
        if url in bad:
            return "WRONG"
        return "changed" if good else "unverified"
    if kind == "review":
        return "review"
    return "MISSED" if label["expect"] == "mv" else "none"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--online", action="store_true")
    ap.add_argument("--only")
    args = ap.parse_args()
    dl.CACHE_MODE = "online" if args.online else "replay"

    with open(LABELS, encoding="utf-8") as f:
        labels = json.load(f)
    if args.only:
        labels = [l for l in labels if args.only.lower() in (l["artist"] + l["title"]).lower()]
    counts, rows = {}, []
    for i, label in enumerate(labels, 1):
        live_before = dl.LIVE_CALLS
        try:
            cat = catalog.lookup(label["title"], label["artist"]) or {}
        except Exception:
            cat = {}
        q = dl.SongQuery(label["title"], label["artist"], cat)
        try:
            ranked = dl.search_mv(q, ["youtube", "bilibili"], exclude=set(label.get("static") or []))
        except Exception as e:
            ranked = []
            print("  搜索失败:", label["artist"], "-", label["title"], e, file=sys.stderr)
        kind, pick = decide(ranked)
        verdict = classify(label, kind, pick)
        counts[verdict] = counts.get(verdict, 0) + 1
        if verdict != "correct":
            got = "%s [%s] (%d)" % (pick[1].get("title"), pick[1].get("channel"), pick[0]) if pick else "-"
            rows.append("%-10s %s - %s  =>  %s" % (verdict, label["artist"], label["title"], got))
        if args.online and dl.LIVE_CALLS > live_before:
            print("\r%d/%d" % (i, len(labels)), end="", file=sys.stderr)
            time.sleep(random.uniform(4, 8))  # 放慢节奏,避免触发 YouTube / iTunes 限流
    if args.online:
        print(file=sys.stderr)
    for r in sorted(rows):
        print(r)
    total = sum(counts.values())
    print("\n共 %d 首:" % total, ", ".join("%s %d" % kv for kv in sorted(counts.items())))
    wrong = counts.get("WRONG", 0)
    decided = counts.get("correct", 0) + wrong + counts.get("changed", 0)
    if decided:
        print("自动下载里确认下错的比例:%.1f%%(changed 需人工看一眼)" % (100.0 * wrong / decided))


if __name__ == "__main__":
    main()
