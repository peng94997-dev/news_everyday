# 句子解析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `analyze.html` 输入框：输入任意日语句子，在本机用 janome 和规则秒出解析，包括意群、作用、词表、句型和学过的语法点，不调用任何 AI 或网络服务。

**Architecture:** 新模块 `scripts/analyze.py` 是纯函数，负责分句、切意群、标作用、匹配句型和组装结果。分词、注音函数由 `fetch_news` 作为参数传入，所以 `analyze.py` 不导入 `fetch_news`，和 `lesson.py` 的做法相同。`fetch_news.tokenize` 加一个 `headline` 开关和一个原始词性字段 `j`。句型表复用语法大纲的格式，以及 `lesson.load_syllabus` 和 `lesson.match_point`。

**Tech Stack:** Python 3（`/opt/miniconda3/bin/python3`）、janome、标准库 unittest、原生 HTML/JS。

**Spec:** `docs/superpowers/specs/2026-11-16-sentence-analyzer-design.md`

## Global Constraints

- 测试命令：`cd /Users/cs-pc-027/Documents/projects/news_everyday && /opt/miniconda3/bin/python3 -m unittest discover -s tests -v`
- 不新增依赖；解析路径上绝不调用 Claude、`translate()`、MyMemory 或任何网络服务。
- 单次输入最多 200 字，空输入或只有空白时报错（接口返回 400）。
- 界面文字用简体中文；JSON 用 `ensure_ascii=False, indent=1`。
- 规则没把握时，作用标为 `unknown`（显示「未识别」），不硬给解释。
- 解析时不使用只适用于新闻标题的提示，`level` 为 `headline` 的大纲语法点不参与「学过」标记。

## Review Focus

1. 输入里没有日语（英文、数字、表情符号）→ 不崩溃，返回结果，作用标为「未识别」。→ Task 3 `test_non_japanese_input`
2. 多句输入，句子之间用换行分隔、句尾没有句号 → 按换行和 。！？!? 都能分句。→ Task 2 `test_split_newlines_and_marks`
3. 辞书里没有的词 → 显示「（辞书里没有）」，绝不触发网络翻译。→ Task 4 `test_analyze_never_translates`
4. `user/progress.json` 损坏 → 解析照常进行，只是「学过」为空。→ Task 4 `test_analyze_with_corrupt_progress`
5. 空输入、超过 200 字 → 接口返回 400 和中文提示，页面显示这条提示。→ Task 4 `test_analyze_rejects_empty_and_long`

---

### Task 1: 分词补强（`fetch_news.tokenize`）

**Files:**
- Modify: `scripts/fetch_news.py`（`tokenize`，约在 413 行；`TOKENS_VERSION` 在 369 行）
- Test: `tests/test_tokenize.py`（新建，开头 `sys.path.insert(0, scripts)` 后 `import fetch_news`）

**Interfaces:**
- Produces:
  - `tokenize(title: str, headline: bool = True) -> list[dict]`：每个 token 新增字段 `j`，值为 janome 词性的前两段，比如 `"名詞,副詞可能"`、`"助詞,接続助詞"`、`"動詞,非自立"`。合并出来的 token（数字＋量词、名词＋后缀）取第一个原始 token 的 `j`。
  - `headline=False` 时，不加 `note`（结尾か、结尾へ、名词结尾省略する 这三类提示都不加）。
  - `FORMAL_NOUN_ZH: dict[str, str]`：形式名词的中文和用法提示。词性为 `名詞,非自立` 且辞书没有中文时，用它填 `m`。至少包含 こと、もの、の、とき、ところ、ため、つもり、はず、よう。
  - 词性为 `名詞,形容動詞語幹` 的 token，`p` 改为 `"形容动词（な形容词）"`，`k` 仍为 `"n"`。
  - `TOKENS_VERSION = 4`。

- [ ] **Step 1: 写失败的测试**

```python
def test_formal_noun_has_meaning(self):       # tokenize("見ることが好き") 里 こと 的 m 非空、包含 "事"
def test_na_adjective_display(self):          # 好き: p == "形容动词（な形容词）", k == "n"
def test_raw_pos_field(self):                 # 昨日 的 j == "名詞,副詞可能"；から（雨だから）的 j == "助詞,接続助詞"
def test_headline_notes_off(self):            # tokenize("行きますか", headline=False) 没有任何 note；headline=True 时 か 有 note
def test_tokens_version_bumped(self):         # fetch_news.TOKENS_VERSION == 4
```

- [ ] **Step 2: 运行测试，确认失败**
- [ ] **Step 3: 实现**（`tokenize` 签名、`j` 字段、`FORMAL_NOUN_ZH`、形容动词词性、关掉标题提示、版本号）
- [ ] **Step 4: 运行全部测试**；再跑 `--page-only`，确认已有新闻重新分词后页面正常（`grep -c '"tokens_v": 4' news/2026-11-13/tokyo_news_2026-11-13.json` 大于 0）
- [ ] **Step 5: Commit**：`feat: tokenize headline switch, raw pos, formal nouns`

---

### Task 2: 分句、切意群、标作用（`scripts/analyze.py`）

**Files:**
- Create: `scripts/analyze.py`
- Test: `tests/test_analyze.py`（真实分词：`tok = lambda s: fetch_news.tokenize(s, headline=False)`）

**Interfaces:**
- Consumes: Task 1 的 token 字段 `s, b, k, p, j, m, r, lv`
- Produces:
  - `split_sentences(text: str) -> list[str]`：在 `。！？!?` 后面和换行处切开，保留标点，去掉首尾空白和空句。
  - `chunk_tokens(tokens: list[dict]) -> list[list[dict]]`
  - `label_roles(chunks: list[list[dict]]) -> list[str]`：返回与 chunks 等长的作用 id 列表。
  - `ROLE_ZH: dict[str, str]`：作用 id 到中文说明。id 包括 `topic` 话题、`subject` 主语、`liked` 对象（喜欢、会、想要的是什么）、`object` 宾语、`ni` 时间/地点/对象、`de` 地点/手段、`with` 和……一起、`quote` 引用、`dir` 方向、`from` 起点、`until` 终点、`of` 修饰后面的名词（……的）、`also` 也、`modifier` 修饰后面的名词、`nominal` 动作→名词、`te` ……然后／……着、`adverb` 修饰后面的动作或形容词、`time` 时间、`reason` 原因、`contrast` 转折（但是）、`predicate` 谓语、`unknown` 未识别。
- 切块规则（规则以下面的测试为准）：
  - **独立词**：`k` 属于 n/pn/v/a/num，且 `j` 不是 `動詞,非自立`，也不是 `名詞,接尾`。
  - **前一个是名词、当前是 する 的动词**（`b == "する"`）→ 并进前一块，比如「勉強しています」。
  - **从当前独立词开始新的一块**，满足下面任一条：
    - 当前块已经有助词（`k == "pt"`），而且最后一个 token 是助词或符号
    - 当前块最后一个 token 是动词、形容词或助动词（活用词结尾）
    - 当前块是 `副詞` 或 `名詞,副詞可能`（时间、副词单独成块）
    - 当前 token 是 `副詞`
  - 符号并进前一块。
- 作用判断（看块里最后一个非符号 token）：
  - **助词结尾，按助词判断：**
    - は → `topic`
    - が：如果是 `助詞,接続助詞` → `contrast`；如果谓语块含 好き／嫌い／上手／下手／分かる／わかる／できる／ほしい → `liked`；否则 → `subject`
    - を → `object`；に → `ni`；で → `de`
    - と：前一块以「」」结尾 → `quote`，否则 → `with`
    - へ → `dir`
    - から：`接続助詞` → `reason`，否则 → `from`；ので → `reason`；けど／けれど → `contrast`
    - まで → `until`；の → `of`；も → `also`
    - て／で（`接続助詞`）→ `te`
  - **句子最后一块** → `predicate`
  - **动词或助动词た结尾、没有助词**：下一块以 こと／の（`名詞,非自立`）开头 → `nominal`；下一块以名词开头 → `modifier`
  - **い形容词，或者以 な（助动词）结尾**，下一块是名词 → `modifier`
  - 块是 `副詞` → `adverb`；块是 `名詞,副詞可能`、没有助词 → `time`
  - 其余 → `unknown`

- [ ] **Step 1: 写失败的测试**（期望值就是规则的定义）

```python
CASES = [
 ("私は映画を見ることが好きです。", ["私は", "映画を", "見る", "ことが", "好きです。"], ["topic", "object", "nominal", "liked", "predicate"]),
 ("昨日見た映画は面白かった。", ["昨日", "見た", "映画は", "面白かった。"], ["time", "modifier", "topic", "predicate"]),
 ("ご飯を食べて、寝ます。", ["ご飯を", "食べて、", "寝ます。"], ["object", "te", "predicate"]),
 ("とても静かな町です。", ["とても", "静かな", "町です。"], ["adverb", "modifier", "predicate"]),
 ("毎日日本語を勉強しています。", ["毎日", "日本語を", "勉強しています。"], ["time", "object", "predicate"]),
 ("雨だから、家で本を読みたい。", ["雨だから、", "家で", "本を", "読みたい。"], ["reason", "de", "object", "predicate"]),
 ("行きますか？", ["行きますか？"], ["predicate"]),
 ("新宿から東京まで歩きます。", ["新宿から", "東京まで", "歩きます。"], ["from", "until", "predicate"]),
]
def test_chunks_and_roles(self):              # 逐条 subTest：拼接每块的 s == 期望切块；label_roles == 期望作用
def test_split_newlines_and_marks(self):      # split_sentences("私は学生です。\n映画が好き！本も読む") == ["私は学生です。", "映画が好き！", "本も読む"]
def test_role_zh_covers_all(self):            # CASES 里出现的每个作用 id 都在 ROLE_ZH 里，且 "unknown" 对应 "未识别"
```

- [ ] **Step 2: 运行测试，确认失败**
- [ ] **Step 3: 在 `scripts/analyze.py` 实现**
- [ ] **Step 4: 运行全部测试**
- [ ] **Step 5: Commit**：`feat: sentence chunking and roles`

---

### Task 3: 句型表与结果组装

**Files:**
- Create: `data/sentence_patterns.json`
- Modify: `scripts/analyze.py`
- Test: `tests/test_analyze.py`

**Interfaces:**
- Consumes: Task 2；`lesson.load_syllabus(path)`、`lesson.match_point(point, item)`
- Produces:
  - `PATTERNS_PATH = ROOT / "data" / "sentence_patterns.json"`：40–60 项，格式同语法大纲，外加可选字段 `syllabus`（对应的大纲 id）。至少覆盖设计文档第 4 节列出的那些句型。
  - `analyze_text(text: str, tokenize_fn, ruby_fn, patterns: list[dict], syllabus: list[dict], learned_ids: set[str]) -> dict`：
    - 先 `strip`，空串或超过 200 字抛 `ValueError`，消息为中文。
    - 返回 `{"sentences": [...]}`，每句包含：
      - `text`
      - `ruby`：`ruby_fn(text)`
      - `chunks`：每块 `{"text", "role", "role_zh", "zh"}`，其中 `zh` 是块内实词的中文用「、」连起来，没有就是空串
      - `patterns`：`[{"id", "title", "brief", "hits", "learned"}]`，`learned` 表示 `syllabus` 字段指向的点在 `learned_ids` 里
      - `learned`：命中且已学过的大纲语法点 `[{"id", "title", "hits"}]`，不含 `level == "headline"` 的点
      - `words`：独立词去重后的列表 `[{"word", "base", "kana", "zh", "pos", "jlpt"}]`；`zh` 为空时填 `"（辞书里没有）"`；`word` 是词面，`base` 是原形
    - `match_point` 的 item 用 `{"title": 句子, "tokens": 分词结果}`。

- [ ] **Step 1: 写失败的测试**

```python
def test_patterns_file_valid(self):           # load_syllabus(PATTERNS_PATH) 不报错；40 <= 条数 <= 60；带 syllabus 字段的都指向真实存在的大纲 id
def test_example_sentence_patterns(self):     # 对 CASES 句子：句1 命中 id 包含 "v-koto" 和 "ga-suki"；句5 包含 "te-iru"；句6 包含 "tai" 和 "kara-reason"；句7 包含 "masu-ka"
def test_analyze_text_shape(self):            # 句1：chunks[1] == {"text": "映画を", "role": "object", "role_zh": ROLE_ZH["object"], "zh": "电影"}；words 里有 word "映画"、kana "えいが"、jlpt "N5"
def test_learned_marks(self):                 # learned_ids={"wo-object"} → 句1 的 learned 只含 wo-object；某个 syllabus 字段为 "tai-want" 的句型，在 learned_ids={"tai-want"} 时 learned 为 True
def test_unknown_word_placeholder(self):      # tokenize_fn 返回一个 m=None 的名词 → words[0]["zh"] == "（辞书里没有）"
def test_non_japanese_input(self):            # analyze_text("hello 123", ...) 不抛错，sentences 长度 1，所有作用都在 ROLE_ZH 里
def test_rejects_empty_and_too_long(self):    # "   " 和 "あ"*201 都抛 ValueError；"あ"*200 不抛
```

- [ ] **Step 2: 运行测试，确认失败**
- [ ] **Step 3: 写 `data/sentence_patterns.json`，实现 `analyze_text`**；跑一遍 CASES 和 5 句自编句子，人工检查有没有明显误配，比如 から 被当成原因
- [ ] **Step 4: 运行全部测试**
- [ ] **Step 5: Commit**：`feat: sentence patterns and analysis result`

---

### Task 4: 接口、页面和导航

**Files:**
- Modify: `scripts/analyze.py`（加 `ANALYZE_TEMPLATE`）、`scripts/fetch_news.py`（`build` 写 `analyze.html`；`make_server` 加 `POST /api/analyze`；新闻页、课程页、单词本三个模板的导航加「句子解析」）
- Modify: `scripts/lesson.py`（`LESSON_TEMPLATE` 的导航）
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `analyze_text`、`PATTERNS_PATH`、`lesson.load_syllabus`、`lesson.load_progress`
- Produces:
  - `POST /api/analyze {"text": str}`：
    - 成功：返回 `analyze_text(text, lambda s: tokenize(s, headline=False), furigana, load_syllabus(PATTERNS_PATH), load_syllabus(), learned_ids)` 的结果
    - `learned_ids` 取进度文件里 grammar 的键；进度文件损坏时用空集合，并在终端打印一行警告
    - `ValueError` → 400 `{"error": 中文消息}`
  - `ANALYZE_TEMPLATE`：
    - 页面结构：输入框（textarea）、「解析」按钮（回车提交，Shift+回车换行）
    - 每句依次显示：带注音的原句 → 意群卡片（原文、作用、中文）→ 句型（`learned` 为真的加「学过」标签）→ 学过的语法点 → 词表（每个词有「加入单词本」按钮，调用 `/api/vocab`，带上 `word, kana, zh, sentence`）
    - 最近 10 句存在 `localStorage`，读写都要包在 try/catch 里
    - 直接打开文件（不经过服务）时显示和其他页面一样的提示

- [ ] **Step 1: 写失败的测试**（用 `test_server.py` 里现有的 `make_server` 夹具）

```python
def test_analyze_endpoint(self):               # POST {"text": "私は映画を見ることが好きです。"} → 200，sentences[0]["chunks"][0]["role"] == "topic"
def test_analyze_rejects_empty_and_long(self): # "" → 400，"あ"*201 → 400，返回体都有 "error"
def test_analyze_never_translates(self):       # 把 fetch_news.translate 和 fetch_news._via_mymemory 换成会抛异常的函数，解析含生词的句子仍然返回 200
def test_analyze_with_corrupt_progress(self):  # 进度文件写入 "{" → 仍然返回 200，sentences[0]["learned"] == []
```

- [ ] **Step 2: 运行测试，确认失败**
- [ ] **Step 3: 实现接口、模板、`build` 写 `analyze.html`、四个页面的导航入口**
- [ ] **Step 4: 运行全部测试**；然后在浏览器里打开 `http://localhost:8766/analyze.html`（launch 配置 `news`），输入例句，检查：意群和作用显示正确；「加入单词本」能写进 `user/vocab.md`（测完删掉这个词）；刷新后最近 10 句还在；375px 宽度下没有横向滚动
- [ ] **Step 5: Commit**：`feat: sentence analyzer page and api`
