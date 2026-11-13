"""每日一課：語法大綱の照合・学習進度・選課・課程生成・課程ページ。

fetch_news.py から呼ばれる。このモジュールは fetch_news を import しない（循環を避ける）。
"""
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYLLABUS_PATH = ROOT / "data" / "grammar_syllabus.json"
POINT_FIELDS = ("id", "level", "title", "brief", "match")


def load_syllabus(path=SYLLABUS_PATH):
    """大綱を読み込んで検証する。不正なら ValueError。"""
    pts = json.loads(Path(path).read_text())
    seen = set()
    for i, p in enumerate(pts):
        missing = [f for f in POINT_FIELDS if not p.get(f)]
        if missing:
            raise ValueError(f"语法大纲第 {i + 1} 项（{p.get('id', '?')}）缺少字段：{', '.join(missing)}")
        if p["id"] in seen:
            raise ValueError(f"语法大纲里 id 重复：{p['id']}")
        seen.add(p["id"])
    return pts


def match_point(point, item):
    """見出し item でこの語法点が使われている原文片段を返す（空なら不一致）。"""
    hits = []
    toks = item.get("tokens") or []
    for rule in point["match"]:
        if "token" in rule:
            hits += [t["s"] for t in toks if all(t.get(k) == v for k, v in rule["token"].items())]
        elif "note" in rule:
            hits += [t["s"] for t in toks if rule["note"] in (t.get("note") or "")]
        elif "regex" in rule:
            hits += [m.group(0) for m in re.finditer(rule["regex"], item["title"])]
    return list(dict.fromkeys(hits))
