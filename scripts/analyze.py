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


def _particle_role(pt, i, chunks):
    s, conj = pt["s"], pt.get("j") == "助詞,接続助詞"
    if s == "が":
        if conj:
            return "contrast"
        pred = chunks[-1]
        return "liked" if any((t.get("b") or t["s"]) in LIKE_WORDS for t in pred) else "subject"
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
        if not core:
            role = "unknown"
        elif i == len(chunks) - 1:
            role = "predicate"
        else:
            lt = core[-1]
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
