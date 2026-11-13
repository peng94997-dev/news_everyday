"""每日一課：語法大綱の照合・学習進度・選課・課程生成・課程ページ。

fetch_news.py から呼ばれる。このモジュールは fetch_news を import しない（循環を避ける）。
"""
import contextlib, json, re, subprocess, time
from datetime import datetime
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
        if not isinstance(p["match"], list):
            raise ValueError(f"语法大纲 {p['id']} 的 match 必须是规则列表")
        for rule in p["match"]:
            ok = isinstance(rule, dict) and (isinstance(rule.get("token"), dict) or isinstance(rule.get("note"), str)
                                             or isinstance(rule.get("regex"), str))
            if not ok:
                raise ValueError(f"语法大纲 {p['id']} 的规则写法不对：{rule}")
            if "regex" in rule:
                try:
                    re.compile(rule["regex"])
                except re.error as e:
                    raise ValueError(f"语法大纲 {p['id']} 的正则有误：{rule['regex']}（{e}）") from e
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
- 文字里要加引号时只用「」或“”，绝不能用英文双引号 \"（会破坏 JSON）。

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


class LessonOutputError(RuntimeError):
    """Claude の出力が使えない。raw に原文を持つ（logs/ に保存して調べられるように）。"""

    def __init__(self, msg, raw=""):
        super().__init__(msg)
        self.raw = raw


def parse_lesson_json(text, max_fixes=20, array=False):
    """出力から JSON オブジェクト（array=True なら配列）を取り出す。
    中文の引用に使われた未エスケープの " は “” に直して読み直す。"""
    m = re.search(r"\[.*\]" if array else r"\{.*\}", text, re.S)
    if not m:
        raise LessonOutputError("Claude 的输出里没有 JSON", raw=text)
    s = m.group(0)
    for n in range(max_fixes + 1):
        try:
            return json.loads(s)
        except json.JSONDecodeError as e:
            # 文字列が途中の " で終わったと解釈され、直後で区切りが無いと言われる → その " が犯人
            if n == max_fixes or e.pos == 0 or s[e.pos - 1] != '"' or "delimiter" not in e.msg:
                raise LessonOutputError(f"课程 JSON 解析失败：{e}", raw=text) from e
            s = s[:e.pos - 1] + ("“" if n % 2 == 0 else "”") + s[e.pos:]


def call_claude(prompt, model=LESSON_MODEL, timeout=900):
    r = subprocess.run(["claude", "-p", "--model", model, prompt], capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise LessonOutputError(f"claude CLI 出错（返回码 {r.returncode}）：{(r.stdout + r.stderr).strip()[:200]}",
                                raw=r.stdout + r.stderr)
    return parse_lesson_json(r.stdout)


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


def _ruby(ruby_fn, text):
    """前後の空白を外して読みを付け、空白は読み無しの区切りとして戻す（janome は先頭の空白を落とす）。"""
    core = text.strip()
    lead, trail = text[:len(text) - len(text.lstrip())], text[len(text.rstrip()):]
    pairs = [list(p) for p in ruby_fn(core)] if core else []
    return ([[lead, ""]] if lead else []) + pairs + ([[trail, ""]] if trail else [])


def _save_raw(raw_dir, d, raw):
    """Claude の生出力を logs/ に保存し、ログに添える文を返す。"""
    if not raw:
        return ""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    f = raw_dir / f"lesson_raw_{d}_{datetime.now():%H%M%S}.txt"
    f.write_text(raw)
    return f"；原始输出已保存到 {f}"


def generate_today(news_dir, user_dir, today, vocab_words, ask=call_claude, ruby_fn=lambda s: [], log=print,
                   syllabus=None, lock=None, raw_dir=ROOT / "logs"):
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
    by_id = {p["id"]: p for p in syllabus}
    what = (f"新课 {plan['point']}（{by_id[plan['point']]['title']}），例句 {len(plan['examples'])} 条" if kind == "new"
            else f"复习课 {', '.join(plan['points'])}")
    learned = learned_words(vocab_words)
    new_words = pick_new_words(plan.get("examples") or items, learned)
    log(f"今日一课：{what}；调用 Claude（{LESSON_MODEL}），大约需要 1 分钟…")
    t0 = time.time()
    try:
        obj = ask(build_prompt(plan, syllabus, prog, learned, new_words, items))
    except Exception as e:
        log(f"今日一课生成失败（{time.time() - t0:.0f} 秒）：{e}" + _save_raw(raw_dir, d, getattr(e, "raw", None)))
        return None
    if not validate_lesson(obj, kind):
        log("今日一课生成失败：Claude 返回的内容格式不对" +
            _save_raw(raw_dir, d, json.dumps(obj, ensure_ascii=False, indent=1)))
        return None
    log(f"今日一课已生成：{obj['main']['title']}（{time.time() - t0:.0f} 秒）")
    obj.update(date=d, kind=kind, model=LESSON_MODEL, ruby=[_ruby(ruby_fn, r["text"]) for r in obj["main"]["reading"]])
    obj.update(point_id=plan.get("point"), point_ids=plan.get("points", []))
    # Claude を待つ間に他のリクエストが進度や課を書いたかもしれないので、読み直してから書く
    with lock or contextlib.nullcontext():
        if path.exists():
            return json.loads(path.read_text())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
        prog = load_progress(prog_path)
        prog["lessons"][d] = {"kind": kind, "point": plan.get("point"), "points": plan.get("points", []),
                              "done": False}
        save_progress(prog_path, prog)
    return obj


# ---- 課程ページ ----------------------------------------------------------------
def lesson_view(news_dir, user_dir, date, today, syllabus=None):
    """課程ページに渡すデータ。date が無ければ未完了の課 → 最新の課。"""
    user_dir = Path(user_dir)
    syllabus = syllabus if syllabus is not None else load_syllabus()
    prog = load_progress(user_dir / PROGRESS_NAME)
    files = {f.stem: f for f in (user_dir / "lessons").glob("*.json")}
    history = []
    for d in sorted(files, reverse=True):
        try:
            title = json.loads(files[d].read_text())["main"]["title"]
        except (ValueError, KeyError):
            continue
        history.append({"date": d, "title": title, "done": bool(prog["lessons"].get(d, {}).get("done"))})
    cur = current_lesson(prog)
    date = date if date in files else (cur if cur in files else (history[0]["date"] if history else None))
    return {"lesson": json.loads(files[date].read_text()) if date else None, "date": date,
            "done": bool(date and prog["lessons"].get(date, {}).get("done")),
            "review": review_examples(syllabus, prog, today_items(news_dir, today), today),
            "history": history,
            "can_generate": not cur and today.isoformat() not in files,  # 「生成今天的课」を出すか
            "status": {"current": cur, "due": len(due_points(prog, syllabus, today))}}


LESSON_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>今日一课</title>
<style>
/*__BASE_CSS__*/
.wrap { max-width: 760px; }
section { background: var(--surface); border: 1px solid var(--line); border-radius: 14px; padding: 18px 20px; margin-top: 18px; }
section h2 { font-size: 15px; margin: 0 0 12px; color: var(--muted); font-weight: 600; letter-spacing: .04em; }
.title { font-size: 22px; line-height: 2.2; font-weight: 600; word-break: break-word; }
.gist { font-size: 15px; margin-top: 6px; }
.seg { display: grid; grid-template-columns: minmax(90px, 34%) 1fr; gap: 12px; padding: 10px 0; border-top: 1px dashed var(--line); }
.seg:first-of-type { border-top: 0; }
.seg .ja { font-size: 18px; line-height: 2.1; word-break: break-word; }
.seg.pt .ja { background: #f3d36b55; border-radius: 6px; padding: 0 6px; }
.seg .zh { font-weight: 600; }
.seg .why { color: var(--muted); font-size: 14px; margin-top: 2px; }
.explain { font-size: 15px; white-space: pre-wrap; }
.ex { margin-top: 12px; padding: 10px 12px; background: var(--chip); border-radius: 10px; }
.ex .t { font-size: 16px; line-height: 2; } .ex .z { color: var(--muted); font-size: 13px; }
.word { display: flex; gap: 12px; align-items: center; padding: 8px 0; border-top: 1px dashed var(--line); flex-wrap: wrap; }
.word:first-of-type { border-top: 0; }
.word .w { font-size: 20px; font-weight: 700; } .word .k { color: var(--evt); }
.word .n { color: var(--muted); font-size: 13px; flex-basis: 100%; }
.word .btn { margin-left: auto; padding: 4px 12px; font-size: 13px; }
.rv { padding: 10px 0; border-top: 1px dashed var(--line); } .rv:first-of-type { border-top: 0; }
.rv .h { font-weight: 700; } .rv .b { color: var(--muted); font-size: 14px; }
.rv .ops { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
.done-bar { display: flex; justify-content: center; margin-top: 22px; }
.done-bar .btn { font-size: 16px; padding: 12px 28px; }
.hist a { display: block; color: var(--text); text-decoration: none; padding: 6px 0; border-top: 1px dashed var(--line); font-size: 14px; }
.hist a:first-of-type { border-top: 0; }
.hist a.cur { font-weight: 700; }
.hist .d { color: var(--muted); margin-right: 8px; }
details.ans summary { cursor: pointer; color: var(--evt); margin-top: 8px; }
@media (max-width: 560px) { .seg { grid-template-columns: 1fr; gap: 2px; } .title { font-size: 19px; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div><h1>今日一课</h1><div class="sub" id="sub">每天一个语法点，认真读完</div></div>
    <div style="display:flex;gap:10px"><a class="navlink" href="index.html">新闻</a><a class="navlink" href="vocab.html">单词本</a></div>
  </header>
  <div class="notice" id="fileNotice" hidden>今日一课需要本地服务，请运行
    <code>python3 scripts/fetch_news.py --serve</code>，然后访问 <code>http://localhost:8765/lesson.html</code></div>
  <div id="main"></div>
</div>
<div class="toast" id="toast"></div>
<script>
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const toast = msg => { const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove("show"), 2200); };
const api = (path, opt) => fetch(path, opt).then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(new Error(j.error || r.status))));
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
const ruby = (pairs, text) => pairs && pairs.length
  ? pairs.map(([t, r]) => r ? `<ruby>${esc(t)}<rt>${esc(r)}</rt></ruby>` : esc(t)).join("") : esc(text);
let V = null;

async function load(date) {
  try { V = await api("/api/lesson" + (date ? "?date=" + date : "")); }
  catch (e) { $("#main").innerHTML = `<div class="empty">读取失败：${esc(e.message)}</div>`; return; }
  render();
}

function render() {
  const L = V.lesson, m = $("#main");
  if (!L) {
    m.innerHTML = `<section><div class="empty">还没有课程。抓取新闻后会自动生成，也可以现在生成。<br><br>
      <button class="btn primary" id="gen">生成今天的课</button></div></section>` + reviewHtml() + histHtml();
    $("#gen").onclick = generate; bindReview(); return;
  }
  $("#sub").textContent = `${L.date} · ${L.kind === "new" ? "新课" : "复习课"}${V.done ? " · 已学完 ✓" : ""}`;
  const main = L.main;
  let h = `<section><h2>① 今日标题</h2><div class="title">${ruby(L.ruby && L.ruby.flat ? L.ruby.flat() : null, main.title)}</div>
    <div class="gist">${esc(main.gist)}</div></section>`;
  h += `<section><h2>② 逐词精读</h2>` + main.reading.map((r, i) =>
    `<div class="seg${r.is_point ? " pt" : ""}"><div class="ja">${ruby(L.ruby && L.ruby[i], r.text)}</div>
     <div><div class="zh">${esc(r.zh)}</div><div class="why">${esc(r.why)}</div></div></div>`).join("") + `</section>`;
  const pts = L.kind === "new" ? [L.point] : (L.points || []);
  h += `<section><h2>③ ${L.kind === "new" ? "今日语法点" : "复习的语法点"}</h2>` + pts.map(p =>
    `<div class="explain">${esc(p.explain)}</div>` + (p.examples || []).map(e =>
      `<div class="ex"><div class="t">${esc(e.title).replace(esc(e.where), `<mark>${esc(e.where)}</mark>`)}</div>
       <div class="z">${esc(e.zh)}</div></div>`).join("")).join("<hr>") + `</section>`;
  if ((L.words || []).length) h += `<section><h2>④ 今日新词</h2>` + L.words.map((w, i) =>
    `<div class="word"><span class="w">${esc(w.word)}</span><span class="k">${esc(w.kana)}</span><span>${esc(w.zh)}</span>
     <button class="btn" data-w="${i}">加入单词本</button>${w.note ? `<div class="n">${esc(w.note)}</div>` : ""}</div>`).join("") + `</section>`;
  h += reviewHtml();
  if (L.quiz) h += `<section><h2>⑥ 小测验</h2><div class="ex"><div class="t">${esc(L.quiz.title)}</div></div>
    <p>${esc(L.quiz.question)}</p><details class="ans"><summary>看答案</summary><p>${esc(L.quiz.answer)}</p></details></section>`;
  h += `<div class="done-bar"><button class="btn primary" id="done" ${V.done ? "disabled" : ""}>${V.done ? "已学完 ✓" : "学完了"}</button></div>`;
  if (V.can_generate) h = `<section><div class="empty">今天的课还没有生成。<br><br>
    <button class="btn primary" id="gen">生成今天的课</button></div></section>` + h;
  h += histHtml();
  m.innerHTML = h;
  if ($("#gen")) $("#gen").onclick = generate;
  $("#done").onclick = async () => {
    try { V = await post("/api/lesson/done", { date: L.date }); toast("记下了，明天会提醒你复习"); render(); }
    catch (e) { toast("保存失败：" + e.message); }
  };
  m.querySelectorAll("[data-w]").forEach(b => b.onclick = async () => {
    const w = L.words[+b.dataset.w];
    try { await post("/api/vocab", { word: w.word, kana: w.kana, zh: w.zh, sentence: main.title, sentence_zh: main.gist });
      b.disabled = true; b.textContent = "已加入"; } catch (e) { toast("加入失败：" + e.message); }
  });
  bindReview();
}

function reviewHtml() {
  if (!V.review.length) return "";
  return `<section><h2>⑤ 复习（${V.review.length}）</h2>` + V.review.map(r =>
    `<div class="rv" data-id="${esc(r.id)}"><div class="h">${esc(r.title)}</div><div class="b">${esc(r.brief)}</div>
     ${r.example ? `<div class="ex"><div class="t">${ruby(r.example.ruby, r.example.title)}</div><div class="z">${esc(r.example.zh)}
       · 在「${r.hits.map(esc).join("、")}」</div></div>` : ""}
     <div class="ops"><button class="btn" data-g="0">不认识</button><button class="btn" data-g="1">模糊</button>
       <button class="btn primary" data-g="2">认识</button></div></div>`).join("") + `</section>`;
}

function bindReview() {
  document.querySelectorAll(".rv [data-g]").forEach(b => b.onclick = async () => {
    const rv = b.closest(".rv");
    try { await post("/api/grammar/review", { id: rv.dataset.id, grade: +b.dataset.g }); rv.remove(); toast("已记录"); }
    catch (e) { toast("保存失败：" + e.message); }
  });
}

function histHtml() {
  if (!V.history.length) return "";
  return `<section class="hist"><h2>历史课程</h2>` + V.history.map(x =>
    `<a href="#${x.date}" class="${x.date === V.date ? "cur" : ""}"><span class="d">${x.date}</span>${esc(x.title)} ${x.done ? "✓" : "· 未学完"}</a>`).join("") + `</section>`;
}

async function generate() {
  const b = $("#gen"); b.disabled = true; b.textContent = "正在生成（约 1 分钟）…";
  try { V = await post("/api/lesson/generate"); render(); }
  catch (e) { toast("生成失败：" + e.message); b.disabled = false; b.textContent = "生成今天的课"; }
}

window.onhashchange = () => load(location.hash.slice(1));
if (!location.protocol.startsWith("http")) $("#fileNotice").hidden = false;
else load(location.hash.slice(1));
</script>
</body>
</html>
"""


def grammar_marks(syllabus, prog, days):
    """{見出し: [学過の語法点]}。ニュース一覧の「学过」標記用（Claude は呼ばない）。"""
    learned = [p for p in syllabus if p["id"] in prog["grammar"]]
    marks = {}
    for items in days.values():
        for x in items:
            got = [{"id": p["id"], "title": p["title"], "learned": prog["grammar"][p["id"]]["learned"], "hits": h}
                   for p in learned if (h := match_point(p, x))]
            if got:
                marks[x["title"]] = got
    return marks
