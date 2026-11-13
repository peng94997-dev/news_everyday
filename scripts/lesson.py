"""每日一課：語法大綱の照合・学習進度・選課・課程生成・課程ページ。

fetch_news.py から呼ばれる。このモジュールは fetch_news を import しない（循環を避ける）。
"""
import json, re
from datetime import timedelta
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


USER_DIR = ROOT / "user"  # 個人データ（進度・単語帳・自分の課程）。git には入れない


def migrate_user_data(news_dir, user_dir):
    """user_dir を用意し、旧 news/vocab.md を user_dir へ移す（上書きはしない）。移したら True。"""
    (Path(user_dir) / "lessons").mkdir(parents=True, exist_ok=True)
    old, new = Path(news_dir) / "vocab.md", Path(user_dir) / "vocab.md"
    if old.exists() and not new.exists():
        old.rename(new)
        return True
    return False


# ---- 学習進度 ----------------------------------------------------------------
REVIEW_DAYS = [0, 1, 2, 4, 7, 15, 30, 60]  # 熟練度ごとの次回復習までの日数（単語帳と共通）
PROGRESS_NAME = "progress.json"
MIN_HITS, LOOKAHEAD, MAX_DUE = 2, 10, 5  # 新しい点は 2 件以上の見出しで／10 個先まで／復習が 5 個を超えたら復習課


def next_review(level, grade, today):
    """grade: 0=不认识 1=模糊 2=认识。新しい (level, due) を返す。"""
    level = {0: 0, 1: level, 2: min(level + 1, len(REVIEW_DAYS) - 1)}[grade]
    days = {0: 0, 1: 1, 2: REVIEW_DAYS[level]}[grade]
    return level, (today + timedelta(days=days)).isoformat()


def load_progress(path):
    path = Path(path)
    if not path.exists():
        return {"grammar": {}, "lessons": {}}
    try:
        prog = json.loads(path.read_text())
    except json.JSONDecodeError as e:  # 黙って空にすると学習記録が全部消えるので止める
        raise ValueError(f"学习进度文件损坏，请手动修复：{path}（{e}）") from e
    prog.setdefault("grammar", {})
    prog.setdefault("lessons", {})
    return prog


def save_progress(path, prog):
    Path(path).write_text(json.dumps(prog, ensure_ascii=False, indent=1))


def current_lesson(prog):
    """まだ「学完了」になっていない最新の課の日付。"""
    open_ = [d for d, x in prog["lessons"].items() if not x.get("done")]
    return max(open_) if open_ else None


def due_points(prog, syllabus, today):
    g = prog["grammar"]
    return [p["id"] for p in syllabus if p["id"] in g and g[p["id"]]["due"] <= today.isoformat()]


def plan_today(syllabus, prog, items, today):
    cur = current_lesson(prog)
    if cur:
        return {"action": "continue", "date": cur}
    due = due_points(prog, syllabus, today)
    if len(due) <= MAX_DUE:
        todo = [p for p in syllabus if p["id"] not in prog["grammar"]][:LOOKAHEAD + 1]
        for p in todo:
            hits = [x for x in items if match_point(p, x)]
            if len(hits) >= MIN_HITS:
                hits.sort(key=lambda x: x.get("published", ""), reverse=True)
                return {"action": "new", "point": p["id"], "examples": hits[:3]}
    if due:
        return {"action": "review", "points": due[:MAX_DUE]}
    known = {p["id"] for p in syllabus}
    learned = sorted((v["due"], k) for k, v in prog["grammar"].items() if k in known)
    if learned:
        return {"action": "review", "points": [k for _, k in learned[:3]]}
    return {"action": "none"}


def mark_done(prog, date, today):
    les = prog["lessons"].get(date)
    if not les or les.get("done"):
        return
    les["done"] = True
    if les.get("kind") == "new" and les.get("point") and les["point"] not in prog["grammar"]:
        prog["grammar"][les["point"]] = {"learned": today.isoformat(), "level": 1,
                                         "due": (today + timedelta(days=REVIEW_DAYS[1])).isoformat()}


def review_point(prog, point_id, grade, today):
    g = prog["grammar"].get(point_id)
    if g:
        g["level"], g["due"] = next_review(g["level"], grade, today)


def learned_words(vocab_words):
    return {w["word"] for w in vocab_words if int(w.get("level") or 0) >= 1}


def pick_new_words(examples, learned, n=5):
    out, seen = [], set()
    for x in examples:
        for t in x.get("tokens") or []:
            key = t.get("b") or t["s"]
            if t.get("lv") in ("N5", "N4") and key not in learned and key not in seen:
                seen.add(key)
                out.append(t)
    return out[:n]
