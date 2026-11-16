"""句子解析：自分で入力した日本語の文を、janome の分詞結果と規則だけで解析する（AI・ネットワークは使わない）。

fetch_news.py から呼ばれる。このモジュールは fetch_news を import しない（分詞・注音の関数は引数で受け取る）。
"""
import re
from pathlib import Path

from lesson import match_point

ROOT = Path(__file__).resolve().parent.parent
PATTERNS_PATH = ROOT / "data" / "sentence_patterns.json"
MAX_LEN = 200
NO_ENTRY = "（辞书里没有）"

ROLE_ZH = {
    "topic": "话题（说到……）", "subject": "主语（谁／什么）", "liked": "对象（喜欢、会、想要的是什么）",
    "object": "宾语（动作的对象）", "ni": "时间／地点／对象", "de": "地点／手段", "with": "和……一起",
    "quote": "引用", "dir": "方向", "from": "起点（从……）", "until": "终点（到……）", "of": "修饰后面的名词（……的）",
    "also": "也", "modifier": "修饰后面的名词", "nominal": "动作 → 名词（“……这件事”）",
    "te": "……然后／……着（连接下一个动作）", "adverb": "修饰后面的动作或形容词", "time": "时间",
    "reason": "原因", "contrast": "转折（但是）", "concession": "即使……也／……也可以", "predicate": "谓语",
    "unknown": "未识别",
}
OPEN_BRACKETS = set("「『（(【")
QUOTE_VERBS = {"思う", "言う", "考える", "書く", "聞く", "話す", "答える", "いう"}
LIKE_WORDS = {"好き", "嫌い", "上手", "下手", "分かる", "わかる", "できる", "ほしい", "欲しい"}
PARTICLE_ROLE = {"は": "topic", "を": "object", "に": "ni", "で": "de", "へ": "dir", "から": "from", "まで": "until",
                 "の": "of", "も": "also", "て": "te", "ので": "reason", "けど": "contrast", "けれど": "contrast",
                 "けれども": "contrast"}


def split_sentences(text):
    """。！？!? の後ろと改行で文に分ける（句読点は残す）。"""
    return [s.strip() for s in re.split(r"(?<=[。！？!?])|\n", text) if s and s.strip()]


def _independent(t):
    return t["k"] in ("n", "pn", "v", "a", "num") and t.get("j") not in ("動詞,非自立", "名詞,接尾")


def _is_adverb(t):
    return t.get("j", "").startswith("副詞")


def _starts_new(cur, t):
    """t から新しい意群を始めるか。"""
    if t["s"] in OPEN_BRACKETS:
        return True  # 開き括弧は次の意群の頭に
    if all(x["s"] in OPEN_BRACKETS for x in cur):
        return False
    if not _independent(t):
        return False  # 助詞・助動詞・記号・非自立動詞（〜ている の い）は前にくっつける
    last = cur[-1]
    if t["k"] == "v" and t.get("b") == "する" and last["k"] == "n":
        return False  # 勉強＋する
    if any(x["k"] == "pt" for x in cur) and last["k"] in ("pt", "sym"):
        return True
    if last["k"] in ("v", "a", "ax"):
        return True  # 活用語で終わっている
    if _is_adverb(last) or last.get("j") == "名詞,副詞可能" or _is_adverb(t):
        return True  # 副詞・時を表す名詞は単独の意群
    return False


def chunk_tokens(tokens):
    chunks = []
    for t in tokens:
        if not chunks or _starts_new(chunks[-1], t):
            chunks.append([t])
        else:
            chunks[-1].append(t)
    return chunks


def _core(chunk):
    return [t for t in chunk if t["k"] != "sym"]


JP_CHAR = re.compile(r"[ぁ-んァ-ヶ一-鿿々]")


def _likes_next(i, chunks):
    """が の後ろ、好き などの述語までの間に別の動詞・形容詞や が／を が無いか（あれば が はそちらの主語）。"""
    for c in chunks[i + 1:-1]:
        core = _core(c)
        if core and (core[-1]["k"] in ("v", "a", "ax") or core[-1]["s"] in ("が", "を")):
            return False
    return any((t.get("b") or t["s"]) in LIKE_WORDS for t in chunks[-1])


def _particle_role(pt, i, chunks):
    s, conj = pt["s"], pt.get("j") == "助詞,接続助詞"
    if s == "が":
        if conj:
            return "contrast"
        return "liked" if _likes_next(i, chunks) else "subject"
    if s == "と":
        nxt = _core(chunks[i + 1]) if i + 1 < len(chunks) else []
        quoted = "」" in "".join(x["s"] for x in chunks[i]) or (nxt and (nxt[0].get("b") or nxt[0]["s"]) in QUOTE_VERBS)
        return "quote" if quoted else "with"
    if s == "も":
        core = _core(chunks[i])
        if len(core) > 1 and core[-2]["s"] in ("て", "で") and core[-2].get("j") == "助詞,接続助詞":
            return "concession"  # 〜ても（いい）
    if s == "から" and conj:
        return "reason"
    if s == "で" and conj:
        return "te"  # 読んで など
    return PARTICLE_ROLE.get(s, "unknown")


def label_roles(chunks):
    roles = []
    for i, c in enumerate(chunks):
        core = _core(c)
        nxt = _core(chunks[i + 1]) if i + 1 < len(chunks) else []
        lt = core[-1] if core else None
        if not core or not JP_CHAR.search("".join(t["s"] for t in core)):
            role = "unknown"  # 日本語が無い（英語・数字だけ など）
        elif i == len(chunks) - 1 and not (lt["k"] == "pt" and "終助詞" not in lt.get("j", "")):
            role = "predicate"  # 最後の意群（終助詞 か・ね・よ で終わってもよい）
        else:
            if lt["k"] == "pt":
                role = _particle_role(lt, i, chunks)
            elif (lt["k"] == "v" or (lt["k"] == "ax" and lt["s"] == "た")) and nxt:
                if nxt[0].get("j") == "名詞,非自立" and nxt[0]["s"] in ("こと", "の"):
                    role = "nominal"
                else:
                    role = "modifier" if nxt[0]["k"] in ("n", "pn", "num") else "unknown"
            elif (lt["k"] == "a" or (lt["k"] == "ax" and lt["s"] == "な")) and nxt and nxt[0]["k"] in ("n", "pn"):
                role = "modifier"
            elif _is_adverb(lt):
                role = "adverb"
            elif lt.get("j") == "名詞,副詞可能":
                role = "time"
            else:
                role = "unknown"
        roles.append(role)
    return roles


def _words(tokens):
    out, seen = [], set()
    for t in tokens:
        if not _independent(t):
            continue
        base = t.get("b") or t["s"]
        if base in seen:
            continue
        seen.add(base)
        out.append({"word": t["s"], "base": base, "kana": t.get("r", ""), "zh": t.get("m") or NO_ENTRY,
                    "pos": t.get("p", ""), "jlpt": t.get("lv", "")})
    return out


def analyze_sentence(text, tokenize_fn, ruby_fn, patterns, syllabus, learned_ids):
    tokens = tokenize_fn(text)
    chunks = chunk_tokens(tokens)
    roles = label_roles(chunks)
    item = {"title": text, "tokens": tokens}
    pats = []
    for p in patterns:
        hits = match_point(p, item)
        if hits:
            pats.append({"id": p["id"], "title": p["title"], "brief": p["brief"], "hits": hits,
                         "learned": p.get("syllabus") in learned_ids})
    learned = [{"id": p["id"], "title": p["title"], "hits": h} for p in syllabus
               if p["id"] in learned_ids and p["level"] != "headline" and (h := match_point(p, item))]
    return {"text": text, "ruby": ruby_fn(text),
            "chunks": [{"text": "".join(t["s"] for t in c), "role": r, "role_zh": ROLE_ZH[r],
                        "zh": "、".join(t["m"] for t in c if _independent(t) and t.get("m"))}
                       for c, r in zip(chunks, roles)],
            "patterns": pats, "learned": learned, "words": _words(tokens)}


def analyze_text(text, tokenize_fn, ruby_fn, patterns, syllabus, learned_ids):
    """入力全体を文ごとに解析する。空・長すぎる入力は ValueError。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("请输入一句日语")
    if len(text) > MAX_LEN:
        raise ValueError(f"一次最多 {MAX_LEN} 个字，请分几次解析")
    return {"sentences": [analyze_sentence(s, tokenize_fn, ruby_fn, patterns, syllabus, learned_ids)
                          for s in split_sentences(text)]}


ANALYZE_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>句子解析</title>
<style>
/*__BASE_CSS__*/
.wrap { max-width: 820px; }
.inbox { margin-top: 20px; display: grid; gap: 10px; }
.inbox textarea { font-size: 18px; min-height: 84px; }
.inbox .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.inbox .hint { color: var(--muted); font-size: 12px; }
.recent { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
.recent .chip { font-size: 12px; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
section { background: var(--surface); border: 1px solid var(--line); border-radius: 14px; padding: 18px 20px; margin-top: 18px; }
section h2 { font-size: 15px; margin: 0 0 12px; color: var(--muted); font-weight: 600; }
.sent { font-size: 22px; line-height: 2.2; font-weight: 600; word-break: break-word; }
.chunks { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.chunk { border: 1px solid var(--line); border-radius: 10px; padding: 8px 12px; background: var(--chip); min-width: 72px; }
.chunk .ja { font-size: 18px; font-weight: 600; }
.chunk .role { font-size: 12px; color: var(--evt); margin-top: 2px; }
.chunk .role.unknown { color: var(--muted); }
.chunk .zh { font-size: 13px; color: var(--muted); }
h3 { font-size: 13px; color: var(--muted); margin: 18px 0 6px; font-weight: 600; }
.pat { padding: 8px 0; border-top: 1px dashed var(--line); } .pat:first-of-type { border-top: 0; }
.pat .t { font-weight: 700; } .pat .b { color: var(--muted); font-size: 14px; }
.tag { font-size: 11px; color: #8a6514; background: #f3ecd9; border-radius: 6px; padding: 0 6px; margin-left: 6px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
td { padding: 6px 4px; border-top: 1px dashed var(--line); vertical-align: middle; }
td.w { font-size: 17px; font-weight: 700; white-space: nowrap; } td.k { color: var(--evt); }
td .btn { padding: 3px 10px; font-size: 12px; }
.err { color: var(--imm); margin-top: 8px; }
@media (max-width: 560px) { td.p { display: none; } .sent { font-size: 19px; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div><h1>句子解析</h1><div class="sub">输入一句日语，本机解析，不用 AI</div></div>
    <div style="display:flex;gap:10px;flex-wrap:wrap"><a class="navlink" href="index.html">新闻</a>
      <a class="navlink" href="lesson.html">今日一课</a><a class="navlink" href="vocab.html">单词本</a></div>
  </header>
  <div class="notice" id="fileNotice" hidden>句子解析需要本地服务，请运行
    <code>python3 scripts/fetch_news.py --serve</code>，然后访问 <code>http://localhost:8765/analyze.html</code></div>
  <div class="inbox">
    <textarea id="text" lang="ja" placeholder="例：私は映画を見ることが好きです。" maxlength="200"></textarea>
    <div class="row"><button class="btn primary" id="go">解析</button>
      <span class="hint">回车解析，Shift+回车换行；一次最多 200 字，可以输入几句</span></div>
    <div class="err" id="err"></div>
    <div class="recent" id="recent"></div>
  </div>
  <div id="out"></div>
</div>
<div class="toast" id="toast"></div>
<script>
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const toast = msg => { const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove("show"), 2200); };
const post = (path, body) => fetch(path, { method: "POST", body: JSON.stringify(body) })
  .then(r => r.json().then(j => r.ok ? j : Promise.reject(new Error(j.error || r.status))));
const ruby = (pairs, text) => pairs && pairs.length
  ? pairs.map(([t, r]) => r ? `<ruby>${esc(t)}<rt>${esc(r)}</rt></ruby>` : esc(t)).join("") : esc(text);
const KEY = "analyze-recent";
const loadRecent = () => { try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (e) { return []; } };
const saveRecent = t => { try { localStorage.setItem(KEY, JSON.stringify([t, ...loadRecent().filter(x => x !== t)].slice(0, 10))); } catch (e) {} };
let words = [];

function showRecent() {
  $("#recent").innerHTML = loadRecent().map((t, i) => `<button class="chip" data-r="${i}" title="${esc(t)}">${esc(t)}</button>`).join("");
  document.querySelectorAll("[data-r]").forEach(b => b.onclick = () => { $("#text").value = loadRecent()[+b.dataset.r]; go(); });
}

async function go() {
  const text = $("#text").value.trim();
  $("#err").textContent = "";
  if (!text) { $("#err").textContent = "请输入一句日语"; return; }
  let r;
  try { r = await post("/api/analyze", { text }); }
  catch (e) { $("#err").textContent = e.message; return; }
  saveRecent(text); showRecent();
  words = [];
  $("#out").innerHTML = r.sentences.map(s => {
    const base = words.length; words.push(...s.words.map(w => ({ ...w, sentence: s.text })));
    return `<section><div class="sent" lang="ja">${ruby(s.ruby, s.text)}</div>
      <h3>句子结构</h3><div class="chunks">${s.chunks.map(c => `<div class="chunk"><div class="ja" lang="ja">${esc(c.text)}</div>
        <div class="role ${c.role}">${esc(c.role_zh)}</div>${c.zh ? `<div class="zh">${esc(c.zh)}</div>` : ""}</div>`).join("")}</div>
      ${s.patterns.length ? `<h3>句型</h3>` + s.patterns.map(p => `<div class="pat"><div class="t">${esc(p.title)}${p.learned ? `<span class="tag">学过</span>` : ""}</div>
        <div class="b">${esc(p.brief)} · 在「${p.hits.map(esc).join("、")}」</div></div>`).join("") : ""}
      ${s.learned.length ? `<h3>你学过的语法点</h3>` + s.learned.map(p => `<div class="pat"><div class="t">${esc(p.title)}<span class="tag">学过</span></div>
        <div class="b">在「${p.hits.map(esc).join("、")}」</div></div>`).join("") : ""}
      ${s.words.length ? `<h3>单词</h3><table>${s.words.map((w, i) => `<tr><td class="w" lang="ja">${esc(w.word)}</td><td class="k">${esc(w.kana)}</td>
        <td>${esc(w.zh)}</td><td class="p">${esc(w.pos)}</td><td>${w.jlpt ? `<span class="lvb ${esc(w.jlpt)}">${esc(w.jlpt)}</span>` : ""}</td>
        <td><button class="btn" data-w="${base + i}">加入单词本</button></td></tr>`).join("")}</table>` : ""}</section>`;
  }).join("");
  document.querySelectorAll("[data-w]").forEach(b => b.onclick = async () => {
    const w = words[+b.dataset.w];
    try { await post("/api/vocab", { word: w.base, kana: w.kana, zh: w.zh === "（辞书里没有）" ? "" : w.zh, sentence: w.sentence });
      b.disabled = true; b.textContent = "已加入"; } catch (e) { toast("加入失败：" + e.message); }
  });
}

$("#go").onclick = go;
$("#text").onkeydown = e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); go(); } };
if (!location.protocol.startsWith("http")) $("#fileNotice").hidden = false;
showRecent();
</script>
</body>
</html>
"""
