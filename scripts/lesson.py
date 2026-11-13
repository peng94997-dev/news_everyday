"""每日一課：語法大綱の照合・学習進度・選課・課程生成・課程ページ。

fetch_news.py から呼ばれる。このモジュールは fetch_news を import しない（循環を避ける）。
"""
import json, re, subprocess
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
MIN_HITS, LOOKAHEAD, MAX_DUE, SHORT_TITLE = 2, 10, 5, 40  # 新しい点は 2 件以上の見出しで／10 個先まで／復習が 5 個を超えたら復習課


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
                hits.sort(key=lambda x: len(x["title"]) > SHORT_TITLE)  # N5 には短い見出しが読みやすい
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


# ---- 課程生成 ----------------------------------------------------------------
LESSON_MODEL = "sonnet"
LESSON_PROMPT = """你是日语老师。学生是中国人，日语水平 N5，正在借真实的日语新闻标题一点一点学语法。
请为他写今天的一节课。要求：
- 讲解只能用学生已经学过的语法（列在下面）和最基础的术语；必须用到新语法时，用一句话顺带说明。
- 「why」要讲清楚“为什么这里这样用”，不能只翻译。
- 新词只从给出的候选词里挑 3–5 个。
- 只输出一个 JSON 对象，不要代码块或其他文字。

学生已经学过的语法：{learned}

{task}

输出格式：
{schema}"""
SCHEMA_MAIN = ('"main": {{"title": "从下面的标题里选一条作主句，原样照抄", "gist": "中文大意",\n'
               ' "reading": [{{"text": "按原文顺序切的日语片段，全部拼起来必须等于 title", "zh": "中文意思",'
               ' "why": "为什么这样用", "is_point": {is_point}}}]}}')
SCHEMA_NEW = ("{" + SCHEMA_MAIN.format(is_point="这一段是否用到今天的语法点 true/false") + ',\n'
              ' "point": {"explain": "用中文讲透今天的语法点", "examples": [{"title": "另一条标题原文",'
              ' "where": "语法点出现的片段", "zh": "中文意思"}]},\n'
              ' "words": [{"word": "单词", "kana": "假名", "zh": "中文", "note": "用法提示"}],\n'
              ' "quiz": {"title": "一条标题原文", "question": "关于今天语法点的一个小问题", "answer": "答案"}}')
SCHEMA_REVIEW = ("{" + SCHEMA_MAIN.format(is_point="这一段是否用到要复习的语法点 true/false") + ',\n'
                 ' "points": [{"id": "语法点 id", "explain": "简短复习讲解", "examples": [{"title": "标题原文",'
                 ' "where": "语法点出现的片段", "zh": "中文意思"}]}],\n'
                 ' "words": [{"word": "单词", "kana": "假名", "zh": "中文", "note": "用法提示"}]}')


def call_claude(prompt, model=LESSON_MODEL, timeout=900):
    r = subprocess.run(["claude", "-p", "--model", model, prompt], capture_output=True, text=True, timeout=timeout)
    m = re.search(r"\{.*\}", r.stdout, re.S)
    if r.returncode or not m:
        raise RuntimeError((r.stdout + r.stderr).strip()[:200])
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"课程 JSON 解析失败：{e}") from e


def today_items(news_dir, today):
    d = today.isoformat()
    f = Path(news_dir) / d / f"tokyo_news_{d}.json"
    return json.loads(f.read_text()) if f.exists() else []


def _titles(items):
    return "\n".join(f"- {x['title']}（{x.get('zh', '')}）" for x in items)


def build_prompt(plan, syllabus, prog, learned, new_words, items=()):
    by_id = {p["id"]: p for p in syllabus}
    done = [by_id[k]["title"] for k in prog["grammar"] if k in by_id] or ["（还没有，今天是第一课）"]
    words = "、".join(f"{t.get('b') or t['s']}（{t.get('m') or ''}）" for t in new_words) or "（无）"
    if plan["action"] == "new":
        p = by_id[plan["point"]]
        task = (f"今天的语法点：{p['title']}\n说明：{p['brief']}\n\n用到这个点的标题：\n{_titles(plan['examples'])}"
                f"\n\n候选新词：{words}")
        schema = SCHEMA_NEW
    else:
        lines = []
        for k in plan["points"]:
            ex = [x for x in items if match_point(by_id[k], x)][:2]
            lines.append(f"【{k}】{by_id[k]['title']}：{by_id[k]['brief']}\n{_titles(ex) or '（今天的标题里没有）'}")
        task = "今天是复习课，要复习这些语法点：\n" + "\n\n".join(lines) + \
               f"\n\n主句从上面的标题里选一条。\n\n候选新词：{words}"
        schema = SCHEMA_REVIEW
    return LESSON_PROMPT.format(learned="、".join(done), task=task, schema=schema)


def _nospace(s):
    return re.sub(r"\s", "", s)


def validate_lesson(obj, kind):
    try:
        main = obj["main"]
        ok = bool(main["title"] and main["gist"] and main["reading"])
        ok &= all(r["text"] and r["zh"] and "why" in r for r in main["reading"])
        ok &= _nospace("".join(r["text"] for r in main["reading"])) == _nospace(main["title"])
        ok &= isinstance(obj.get("words", []), list)
        if kind == "new":
            ok &= bool(obj["point"]["explain"]) and isinstance(obj["point"]["examples"], list)
        else:
            ok &= bool(obj["points"]) and all(p["id"] and p["explain"] for p in obj["points"])
        return bool(ok)
    except (KeyError, TypeError):
        return False


def review_examples(syllabus, prog, items, today):
    by_id = {p["id"]: p for p in syllabus}
    out = []
    for k in due_points(prog, syllabus, today):
        p = by_id[k]
        hit = next(((x, h) for x in items if (h := match_point(p, x))), None)
        out.append({"id": k, "title": p["title"], "brief": p["brief"],
                    "example": {f: hit[0].get(f) for f in ("title", "zh", "ruby")} if hit else None,
                    "hits": hit[1] if hit else []})
    return out


def generate_today(news_dir, user_dir, today, vocab_words, ask=call_claude, ruby_fn=lambda s: [], log=print,
                   syllabus=None):
    """今日の課を生成して user_dir/lessons/<today>.json に保存する。生成しなかったら None。"""
    user_dir, d = Path(user_dir), today.isoformat()
    path = user_dir / "lessons" / f"{d}.json"
    if path.exists():
        return json.loads(path.read_text())
    syllabus = syllabus if syllabus is not None else load_syllabus()
    prog_path = user_dir / PROGRESS_NAME
    prog = load_progress(prog_path)
    items = today_items(news_dir, today)
    if not items:
        log("今日一课：今天还没有新闻，跳过")
        return None
    plan = plan_today(syllabus, prog, items, today)
    if plan["action"] == "continue":
        log(f"今日一课：{plan['date']} 的课还没学完，先不推进")
        return None
    if plan["action"] == "none":
        log("今日一课：今天的标题里没有合适的语法点")
        return None
    kind = "new" if plan["action"] == "new" else "review"
    learned = learned_words(vocab_words)
    new_words = pick_new_words(plan.get("examples") or items, learned)
    try:
        obj = ask(build_prompt(plan, syllabus, prog, learned, new_words, items))
    except Exception as e:
        log(f"今日一课生成失败：{e}")
        return None
    if not validate_lesson(obj, kind):
        log("今日一课生成失败：Claude 返回的内容格式不对")
        return None
    obj.update(date=d, kind=kind, model=LESSON_MODEL, ruby=[ruby_fn(r["text"]) for r in obj["main"]["reading"]])
    obj.update(point_id=plan.get("point"), point_ids=plan.get("points", []))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    prog["lessons"][d] = {"kind": kind, "point": plan.get("point"), "points": plan.get("points", []), "done": False}
    save_progress(prog_path, prog)
    return obj
