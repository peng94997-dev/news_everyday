# 每日一课 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每天从当天新闻标题里生成一节以一个新语法点为主角的课，按固定 N5→N4 大纲推进，记录进度并复习，在新闻列表里标出学过的语法点。

**Architecture:** 新模块 `scripts/lesson.py` 负责大纲匹配、进度、选课、调用 Claude 生成课程和课程页模板，全部是纯函数加文件读写；Claude 调用以参数注入，便于测试。`scripts/fetch_news.py` 只做接线：`run_fetch` 里调用生成，`serve` 里挂接口，`build` 里写 `lesson.html` 并把「学过」标记嵌入 `index.html`。

**Tech Stack:** Python 3（`/opt/miniconda3/bin/python3`），janome 分词结果（已存于每条新闻的 `tokens` 字段），`claude -p --model sonnet`，标准库 `unittest`（环境里没有 pytest，不新装依赖），原生 HTML/JS。

**Spec:** `docs/superpowers/specs/2026-11-13-daily-lesson-design.md`

## Global Constraints

- 运行与测试一律用 `/opt/miniconda3/bin/python3`；测试命令：`cd /Users/cs-pc-027/Documents/projects/news_everyday && /opt/miniconda3/bin/python3 -m unittest discover -s tests -v`
- 不新增第三方依赖。
- 界面文字和讲解用简体中文；数据文件 JSON 用 `ensure_ascii=False, indent=1`（与现有文件一致）。
- 课程生成模型：`sonnet`；现有逐条讲解保持 `haiku` 不动。
- 复习间隔沿用 `REVIEW_DAYS = [0, 1, 2, 4, 7, 15, 30, 60]`，评分 0=不认识 1=模糊 2=认识，算法与现有单词本相同。
- 选点：最少命中 2 条标题，沿大纲最多往后看 10 个点；到期复习超过 5 个时上复习课。
- 「学过的单词」= 单词本里 `level >= 1` 的词。
- 同一时间只有一节「当前课」：最近一节课未「学完了」时不推进、不为新点调用 Claude。
- 「学过」标记和选课绝不调用 Claude。
- `--page-only` 不生成课程；课程生成失败只记日志，不影响新闻产出。
- 个人数据一律在 `USER_DIR = ROOT / "user"`（gitignored）：`progress.json`、`vocab.md`、`lessons/<date>.json`。不得写进 `news/`。

## Review Focus

1. 当天没有新闻文件（抓取失败、0 条）→ 不生成课程、不改进度，课程页仍显示当前课或「今天还没有新闻」，不报错。→ Task 4 `test_generate_without_items_is_noop`
2. Claude 返回非 JSON 或缺字段 → 不写课程文件、进度不变，之后可以重新生成。→ Task 4 `test_generate_bad_output_leaves_no_trace`
3. `progress.json` 损坏 → 报错并拒绝写入，绝不悄悄重置成空（会丢掉全部学习记录）；文件不存在则视为空。→ Task 3 `test_corrupt_progress_raises`
4. 同一天重复抓取或重复点「重新生成」→ 已有当天课程文件时直接返回，不再调用 Claude；重复点「学完了」不改变已排的复习日期。→ Task 3 `test_mark_done_idempotent`、Task 4 `test_generate_idempotent`
5. 使用者手动编辑大纲，删掉了进度里已有的点 → 该点被忽略，不崩溃。→ Task 3 `test_unknown_point_in_progress_ignored`

---

### Task 1: 语法大纲草稿与匹配器（完成后交使用者过目，确认后才开始 Task 2）

**Files:**
- Create: `scripts/lesson.py`
- Create: `data/grammar_syllabus.json`
- Create: `scripts/syllabus_report.py`
- Test: `tests/test_lesson.py`（文件开头 `sys.path.insert(0, str(ROOT / "scripts"))`）

**Interfaces:**
- Produces:
  - `SYLLABUS_PATH: Path`（`ROOT / "data" / "grammar_syllabus.json"`）
  - `load_syllabus(path: Path = SYLLABUS_PATH) -> list[dict]`：读入并校验，每项必须有 `id, level, title, brief, match`，`id` 唯一，否则 `ValueError`（消息里带出错的 id 或序号）。
  - `match_point(point: dict, item: dict) -> list[str]`：返回该点在这条标题里命中的原文片段，空列表表示没命中。`item` 是新闻 JSON 的一项，含 `title` 和 `tokens`。
- `match` 格式（本计划规定）：规则列表，任一条命中即算命中。规则三种：
  - `{"token": {<字段>: <值>, ...}}`：某个 token 的这些字段全部相等即命中，命中片段为该 token 的 `s`。可用字段：`s` 词面、`b` 原形、`k` 种类（`n/pn/v/a/pt/ax/sym/num`）、`p` 中文词性。
  - `{"note": "<子串>"}`：某个 token 的 `note` 含该子串即命中（复用 `tokenize` 已有的标题结尾判断：省略「する」、结尾「へ」「か」），命中片段为该 token 的 `s`。
  - `{"regex": "<模式>"}`：对 `title` 做 `re.finditer`，命中片段为 `group(0)`。

- [ ] **Step 1: 写失败的测试**

```python
class MatchTest(unittest.TestCase):
    ITEM = {"title": "新宿から通報", "tokens": [
        {"s": "新宿", "k": "pn"}, {"s": "から", "k": "pt"},
        {"s": "通報", "k": "n", "p": "名词", "note": "标题省略了「する／した」：..."}]}

    def test_token_rule(self):
        p = {"match": [{"token": {"s": "から", "k": "pt"}}]}
        self.assertEqual(lesson.match_point(p, self.ITEM), ["から"])

    def test_note_rule(self):
        p = {"match": [{"note": "省略了「する"}]}
        self.assertEqual(lesson.match_point(p, self.ITEM), ["通報"])

    def test_regex_rule_and_no_match(self):
        self.assertEqual(lesson.match_point({"match": [{"regex": "新宿"}]}, self.ITEM), ["新宿"])
        self.assertEqual(lesson.match_point({"match": [{"token": {"s": "を"}}]}, self.ITEM), [])

    def test_load_syllabus_rejects_duplicate_id(self):
        # 写两个 id 相同的项到临时文件 → assertRaises(ValueError)

    def test_real_syllabus_is_valid(self):
        pts = lesson.load_syllabus()
        self.assertGreaterEqual(len(pts), 60)
        self.assertLessEqual(len(pts), 80)
```

- [ ] **Step 2: 运行测试，确认失败**（`ModuleNotFoundError: lesson`）

- [ ] **Step 3: 在 `scripts/lesson.py` 实现 `load_syllabus` 和 `match_point`**

- [ ] **Step 4: 写 `data/grammar_syllabus.json` 草稿**：60–80 项，按学习顺序排列。`level` 取 `N5 / N4 / headline`。先排 N5 里标题中最常见的点（の、に、で、を、が、と、から、も、名词结尾省略する），再排 headline 写法（结尾へ、结尾か、「」、、代替助词、＝／），最后排 N4。只收录 `match` 能用规则可靠判断的点；`brief` 写给只有 N5 水平的人看。

- [ ] **Step 5: 实现 `scripts/syllabus_report.py`**：读 `news/*/tokyo_news_*.json` 的全部标题，按大纲顺序输出每个点的命中标题数和 1 条示例，并列出命中 0 条的点。运行：`/opt/miniconda3/bin/python3 scripts/syllabus_report.py`。修改规则，直到大纲前 15 个点每个至少命中 3 条，同时抽查示例没有误判。

- [ ] **Step 6: 运行全部测试，确认通过**

- [ ] **Step 7: 停下，把大纲交给使用者过目**：给出每个点的 `title`、`level`、命中数，按顺序排成一张简表。按使用者的意见改顺序或增删后，再进入 Task 2。

- [ ] **Step 8: Commit**：`feat: add grammar syllabus and matcher`

---

### Task 2: 个人数据目录 `user/`

**Files:**
- Modify: `scripts/lesson.py`、`scripts/fetch_news.py`（所有用到 `news_dir / "vocab.md"` 的地方）、`.gitignore`、`readme.md` 不在此任务改
- Test: `tests/test_lesson.py`

**Interfaces:**
- Produces:
  - `USER_DIR: Path = ROOT / "user"`
  - `migrate_user_data(news_dir: Path, user_dir: Path) -> bool`：创建 `user_dir` 和 `user_dir/lessons`；`news_dir/vocab.md` 存在且 `user_dir/vocab.md` 不存在时把它移过去，返回 True；否则什么都不动，返回 False。
  - `fetch_news.py` 里新增 `VOCAB_PATH = USER_DIR / "vocab.md"`，替换全部 `news_dir / "vocab.md"`；`main()` 一开始调用 `migrate_user_data`。
- `.gitignore` 加 `user/` 和 `news/`；然后 `git rm -r --cached news` 让 git 不再跟踪（本地文件保留）。

- [ ] **Step 1: 写失败的测试**：`test_migrate_moves_vocab`（旧文件消失、新文件内容相同、返回 True）、`test_migrate_never_overwrites`（两边都有时都不变、返回 False）
- [ ] **Step 2: 运行测试，确认失败**
- [ ] **Step 3: 实现 `migrate_user_data`，修改 `fetch_news.py`、`.gitignore`，执行 `git rm -r --cached news`**
- [ ] **Step 4: 运行全部测试**；运行 `--page-only`，确认 `user/vocab.md` 里有原来的 118 个词，`news/vocab.md` 不存在；`git status` 不再显示 `news/` 和 `user/`
- [ ] **Step 5: Commit**：`feat: per-user data directory`

---

### Task 3: 进度与选课

**Files:**
- Modify: `scripts/lesson.py`
- Modify: `scripts/fetch_news.py`：把 `REVIEW_DAYS` 改为从 `lesson` 导入，`/api/review` 的计算改用 `next_review`
- Test: `tests/test_lesson.py`

**Interfaces:**
- Consumes: `load_syllabus`、`match_point`（Task 1）、`USER_DIR`（Task 2）
- Produces:
  - `REVIEW_DAYS: list[int]`；`PROGRESS_NAME = "progress.json"`
  - `next_review(level: int, grade: int, today: date) -> tuple[int, str]`：返回新的 `(level, due_iso)`，规则与现有 `/api/review` 完全一致。
  - `load_progress(path: Path) -> dict`：文件不存在时返回 `{"grammar": {}, "lessons": {}}`；JSON 损坏时抛 `ValueError`。
  - `save_progress(path: Path, prog: dict) -> None`
  - `current_lesson(prog: dict) -> str | None`：返回日期最晚、且 `done` 为假的课的日期，否则 `None`。
  - `due_points(prog: dict, syllabus: list[dict], today: date) -> list[str]`：已学且 `due <= today` 的点，只返回大纲里还存在的 id，按大纲顺序。
  - `plan_today(syllabus, prog, items, today) -> dict`，返回以下三种之一：
    - `{"action": "continue", "date": str}`：有未完成的课。
    - `{"action": "new", "point": id, "examples": [item, ...]}`：`examples` 是命中该点的标题，按发布时间倒序，最多 3 条。
    - `{"action": "review", "points": [id, ...]}`：到期复习超过 5 个，或者沿大纲往后 10 个点内都找不到命中 ≥ 2 条的点。`points` 取到期的点（最多 5 个）；如果一个都没有到期，就从已学的点里取 `due` 最早的 3 个；如果还一个都没学过，返回 `{"action": "none"}`。
  - `learned_words(vocab_words: list[dict]) -> set[str]`：`level >= 1` 的词。
  - `pick_new_words(examples: list[dict], learned: set[str], n: int = 5) -> list[dict]`：从例句 tokens 里挑 `lv` 为 N5/N4、原形（`b` 或 `s`）不在 `learned` 里的词，去重，保持出现顺序，最多 n 个；返回 token 字典。
  - `mark_done(prog: dict, date: str, today: date) -> None`：把该课 `done` 设为真；如果是新课，把它的点记为 `{"learned": today, "level": 1, "due": today + REVIEW_DAYS[1]}`。已经 done 的课再调用时不做任何改动。
  - `review_point(prog: dict, point_id: str, grade: int, today: date) -> None`：用 `next_review` 更新该点。
- `progress.json` 结构：`{"grammar": {id: {"learned", "level", "due"}}, "lessons": {date: {"kind": "new"|"review", "point": id|null, "points": [id], "done": bool}}}`

- [ ] **Step 1: 写失败的测试**

```python
def test_plan_continue_blocks_new(self):         # lessons 里有 done=False 的课 → action == "continue"，date 等于那节课
def test_plan_new_picks_first_unlearned_with_two_hits(self):  # 第一个点只命中 1 条、第二个命中 2 条 → point == 第二个
def test_plan_lookahead_limit_is_10(self):        # 只有第 11 个点能命中 → action == "review"（或者 "none"）
def test_plan_review_when_more_than_5_due(self):  # 6 个点到期 → action == "review"，len(points) == 5
def test_mark_done_sets_review(self):             # due == today + 1 天，level == 1
def test_mark_done_idempotent(self):              # 调用两次，第二次传更晚的 today → grammar 记录不变
def test_corrupt_progress_raises(self):           # 文件内容 "{" → assertRaises(ValueError)
def test_missing_progress_is_empty(self):
def test_unknown_point_in_progress_ignored(self): # grammar 里有大纲中不存在的 id → due_points 不含它，plan_today 不崩溃
def test_next_review_matches_vocab_rule(self):    # (0,2)->(1,+1天) (3,1)->(3,+1天) (3,0)->(0,+0天) (7,2)->(7,+60天)
def test_pick_new_words_skips_learned(self):
```

- [ ] **Step 2: 运行测试，确认失败**

- [ ] **Step 3: 在 `scripts/lesson.py` 实现上述函数**

- [ ] **Step 4: 修改 `scripts/fetch_news.py`**：删掉本文件里的 `REVIEW_DAYS`，改为 `from lesson import REVIEW_DAYS, next_review`；`/api/review` 的分支改为 `cur["level"], cur["due"] = next_review(cur["level"], grade, today_jst())`。

- [ ] **Step 5: 运行全部测试，确认通过**；再跑 `/opt/miniconda3/bin/python3 scripts/fetch_news.py --page-only`，确认能正常生成页面。

- [ ] **Step 6: Commit**：`feat: lesson progress and point selection`

---

### Task 4: 生成课程

**Files:**
- Modify: `scripts/lesson.py`
- Modify: `scripts/fetch_news.py`：在 `run_fetch` 里 `auto_add_vocab` 之后、`build` 之前调用 `generate_today`
- Test: `tests/test_lesson.py`

**Interfaces:**
- Consumes: Task 1、Task 2 的全部函数
- Produces:
  - `call_claude(prompt: str, model: str = "sonnet", timeout: int = 900) -> dict`：执行 `claude -p --model <model> <prompt>`，从输出里取最外层的 `{...}` 解析成 dict；返回码非 0 或解析失败时抛 `RuntimeError`。
  - `build_prompt(plan: dict, syllabus: list[dict], prog: dict, learned: set[str], new_words: list[dict]) -> str`
  - `validate_lesson(obj: dict, kind: str) -> bool`
  - `today_items(news_dir: Path, today: date) -> list[dict]`：读 `news/<today>/tokyo_news_<today>.json`，不存在时返回 `[]`。
  - `generate_today(news_dir: Path, user_dir: Path, today: date, vocab_words: list[dict], ask=call_claude, ruby_fn=lambda s: [], log=print) -> dict | None`：按 `plan_today` 执行。`continue` / `none` / 无新闻时不调用 Claude，返回 `None`。`user/lessons/<today>.json` 已存在时直接读出返回，不调用 Claude。成功时写 `user/lessons/<today>.json`，在 `progress["lessons"][today]` 记一条 `done: false` 的课，然后返回课程。Claude 调用失败或校验不通过时 `log` 一行，什么都不写，返回 `None`。
  - `review_examples(syllabus, prog, items, today) -> list[dict]`：对每个到期的点，从 `items` 里找一条命中的标题，返回 `{"id", "title", "brief", "example": item | None, "hits": [str]}`。查看课程时实时计算，不调用 Claude。
- Claude 输出格式（写进提示词，`validate_lesson` 据此校验）：

```json
{"main": {"title": "…", "gist": "…",
          "reading": [{"text": "日语片段", "zh": "中文", "why": "为什么这样用", "is_point": true}]},
 "point": {"explain": "…", "examples": [{"title": "…", "where": "命中的片段", "zh": "…"}]},
 "words": [{"word": "…", "kana": "…", "zh": "…", "note": "…"}],
 "quiz": {"title": "…", "question": "…", "answer": "…"}}
```

  - `quiz` 可选。复习课时 `point` 换成 `points: [{"id", "explain", "examples": [...]}]`。`main.reading` 的 `text` 依次拼起来必须等于 `main.title`，否则校验不通过。
  - 写进课程文件时再加上：`date`、`kind`、`point` 或 `points`、`model`，以及 `ruby`（对 `reading` 每一段的 `text` 调用 `furigana` 得到）。`furigana` 由 `fetch_news` 作为参数 `ruby_fn` 传给 `generate_today`，默认 `lambda s: []`。这样 `lesson.py` 不需要导入 `fetch_news`。
- 提示词要求：讲解只能用使用者已学过的语法点（列出它们的 `title`）和最基础的术语；`why` 要讲「为什么这样用」，不能只翻译；新词从传入的 `new_words` 里选。

- [ ] **Step 1: 写失败的测试**（`ask` 用桩函数，临时目录放 fixture）

```python
def test_generate_writes_lesson_and_progress(self):  # 桩返回合法课程 → 课程文件存在，lessons[today].done is False
def test_generate_idempotent(self):                  # 连续调用两次 → 桩只被调用 1 次
def test_generate_bad_output_leaves_no_trace(self):  # 桩抛 RuntimeError；另一次返回缺 main 的 dict → 都没有课程文件，progress 与调用前相同
def test_generate_without_items_is_noop(self):       # 当天没有新闻文件 → 返回 None，桩未被调用
def test_generate_continue_does_not_call_claude(self):
def test_validate_rejects_reading_not_matching_title(self):
def test_review_examples_uses_todays_titles(self):
```

- [ ] **Step 2: 运行测试，确认失败**

- [ ] **Step 3: 实现上述函数**

- [ ] **Step 4: 接到 `run_fetch`**：用 `try` 包住 `generate_today(news_dir, USER_DIR, now.date(), vocab_load(VOCAB_PATH)[0], ruby_fn=furigana, log=log)`，任何异常都只记一条 `log`；成功时记录「今日一课：<点的 title>」。

- [ ] **Step 5: 运行全部测试，确认通过**

- [ ] **Step 6: 真实冒烟测试**：在临时目录里复制 `news/` 的内容，以临时目录作为 `user_dir`，在 Python 里对 2026-11-13 调用一次 `generate_today`（真实的 `claude`），打开生成的课程文件 人工读一遍：讲解是否只用了已学内容、`why` 是否讲清了原因。

- [ ] **Step 7: Commit**：`feat: generate daily lesson with claude`

---

### Task 5: 课程页与接口

**Files:**
- Modify: `scripts/lesson.py`（加入 `LESSON_TEMPLATE`）
- Modify: `scripts/fetch_news.py`：`build` 写出 `lesson.html`；`serve` 加接口
- Test: `tests/test_lesson.py`（测接口用的纯函数），外加浏览器手动验证

**Interfaces:**
- Consumes: Task 2、Task 3
- Produces:
  - `LESSON_TEMPLATE: str`：含 `/*__BASE_CSS__*/` 占位，与现有模板相同。
  - `lesson_view(news_dir: Path, user_dir: Path, date: str | None, today: date) -> dict`：`date` 为空时取「当前课」，没有当前课就取最近一节。返回 `{"lesson": dict | None, "date": str | None, "done": bool, "review": review_examples(...)（用今天的新闻）, "history": [{"date", "title", "done"}]（按日期倒序）, "status": {"current": str | None, "due": int}}`。
  - 接口（都返回 JSON）：
    - `GET /api/lesson?date=` → `lesson_view`
    - `POST /api/lesson/done {date}` → `mark_done` 后保存，返回 `lesson_view`
    - `POST /api/grammar/review {id, grade}` → `review_point` 后保存，返回 `{"ok": true}`
    - `POST /api/lesson/generate` → `generate_today`，返回 `lesson_view`；生成失败时返回 `{"error": "…"}`，状态码 500
  - 进度文件的读写要加 `_vocab_lock`（复用现有的锁）。
- 页面结构（按设计文档第 4 节的顺序）：今日标题（注音 + 大意）→ 逐词精读（`is_point` 的段落高亮，每段显示中文和「为什么」）→ 今日语法点 → 今日新词（每个词有「加入单词本」按钮，调用 `/api/vocab`）→ 复习（每个点三个评分按钮，调用 `/api/grammar/review`）→ 小测验（点击显示答案）→ 「学完了」按钮 → 历史课程列表。复习课不显示「今日语法点」一节，改为逐个显示复习的点。没有课程时显示「还没有课程」和「生成今天的课」按钮。直接打开文件（不是通过 `--serve`）时，显示与 `vocab.html` 相同的提示。

- [ ] **Step 1: 写失败的测试**：`test_lesson_view_prefers_current`、`test_lesson_view_history_order`、`test_lesson_view_empty`（没有任何课程时 `lesson is None`）

- [ ] **Step 2: 运行测试，确认失败**

- [ ] **Step 3: 实现 `lesson_view`、`LESSON_TEMPLATE`，以及 `fetch_news.py` 里的四个接口和 `build` 里写 `lesson.html` 的部分**

- [ ] **Step 4: 运行全部测试，确认通过**

- [ ] **Step 5: 在浏览器里验证**：运行 `/opt/miniconda3/bin/python3 scripts/fetch_news.py --serve`，打开 `http://localhost:8765/lesson.html`。依次检查：五个部分都显示了；点「学完了」后 `progress.json` 记录了这个点，`due` 是明天；刷新后显示已完成；在复习里评分后，`due` 按规则变化；手机宽度（375px）下没有横向滚动。

- [ ] **Step 6: Commit**：`feat: lesson page and api`

---

### Task 6: 新闻页入口和「学过」标记

**Files:**
- Modify: `scripts/lesson.py`
- Modify: `scripts/fetch_news.py`：`build` 和 `TEMPLATE`
- Test: `tests/test_lesson.py`

**Interfaces:**
- Consumes: `match_point`、`load_progress`、`load_syllabus`
- Produces:
  - `grammar_marks(syllabus, prog, days: dict[str, list[dict]]) -> dict[str, list[dict]]`：键是标题，值是 `[{"id", "title", "learned": date_str, "hits": [str]}]`，只包含已学过的点。
  - `build` 把它加入嵌入数据：`{"days": ..., "sections": ..., "marks": ...}`。`progress.json` 损坏时 `marks` 为 `{}`，并在终端打印警告，页面照常生成。
- 页面：header 里「单词本」左边加 `<a class="navlink" href="lesson.html">今日一课 <span id="lstatus"></span></a>`。在 `--serve` 下，用 `/api/lesson` 的 `status` 填状态：有当前课时显示「未读」，到期复习时显示「复习 N」，都没有时显示「✓」。每条标题下方为每个学过的点显示一个小标签，比如「学过 · の：把两个名词连起来」，悬停时显示学过的日期；标题里命中的片段加下划线。
- 「学完了」或复习评分之后，课程页重新调用一次 `build`，让标记立即更新（在 `/api/lesson/done` 里调用 `build(news_dir)`）。

- [ ] **Step 1: 写失败的测试**：`test_marks_only_learned_points`、`test_marks_empty_without_progress`

- [ ] **Step 2: 运行测试，确认失败**

- [ ] **Step 3: 实现 `grammar_marks`，修改 `build`、`TEMPLATE` 和 `/api/lesson/done`**

- [ ] **Step 4: 运行全部测试，确认通过**；在浏览器里打开 `index.html`，确认入口状态正确，学过的点有标签，下划线位置正确。

- [ ] **Step 5: Commit**：`feat: lesson entry and learned-grammar marks on news page`

---

### Task 7: 端到端验证

- [ ] **Step 1**：备份 `user/vocab.md`，然后运行一次完整抓取 `/opt/miniconda3/bin/python3 scripts/fetch_news.py`。确认日志里有「今日一课：…」，`user/lessons/<今天>.json` 已生成。
- [ ] **Step 2**：断网（或把 `QUERIES` 临时指向不存在的域名）再跑一次，确认新闻部分照旧报错，课程部分没有崩溃，`progress.json` 没有变化。
- [ ] **Step 3**：在 `README` 里补充：每日一课的用法、`user/` 目录（个人数据，不提交）和 `grammar_syllabus.json` 的作用、多人各自克隆使用的方式、怎样手动修改大纲。
- [ ] **Step 4: Commit**：`docs: daily lesson usage`
