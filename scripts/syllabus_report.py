#!/usr/bin/env python3
"""語法大綱の照合レポート：各語法点が既存の見出しに何件当たるかを、大綱の順に表示する。

使い方: python3 scripts/syllabus_report.py
"""
import json
from pathlib import Path

from lesson import ROOT, load_syllabus, match_point


def main():
    items = {}
    for f in sorted((ROOT / "news").glob("*/tokyo_news_*.json")):
        for x in json.loads(f.read_text()):
            items.setdefault(x["title"], x)
    items = list(items.values())
    zero = []
    print(f"标题 {len(items)} 条\n")
    for i, p in enumerate(load_syllabus(), 1):
        hits = [(x, h) for x in items if (h := match_point(p, x))]
        if not hits:
            zero.append(p["id"])
        ex = f"{hits[0][1]} ← {hits[0][0]['title']}" if hits else ""
        print(f"{i:>2}. [{p['level']:<8}] {p['id']:<18} {len(hits):>3}  {ex[:70]}")
    print(f"\n命中 0 条：{', '.join(zero) or '无'}")


if __name__ == "__main__":
    main()
