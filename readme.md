## 抓取和过滤新闻
/opt/miniconda3/bin/python3 scripts/fetch_news.py --serve

不带参数运行 `scripts/fetch_news.py` 会抓取当天新闻，并生成「今日一课」。`--serve` 启动本地页面：http://localhost:8765

## 今日一课

每天以一个语法点为主角，按 `data/grammar_syllabus.json` 的顺序从 N5 学到 N4，例句全部来自当天的新闻标题。

- 打开 http://localhost:8765/lesson.html 。读完之后点「学完了」，这个语法点就会进入复习。
- 当前这节课没学完时，不会推进到新的语法点。
- 复习按 1、2、4、7……天的间隔出现，每次都用当天的新标题作例句。
- 新闻页里，标题中用到你学过的语法点时，会显示「学过 · …」标记。
- 课程用 `claude` CLI（sonnet）生成，需要先登录 `claude`。生成失败不影响新闻抓取，之后可以在课程页点「生成今天的课」重试。

## 个人数据 `user/`（不提交到 git）

每个人克隆仓库，在自己的电脑上运行。学习进度各自独立：

- `user/progress.json`：学过的语法点和复习进度
- `user/vocab.md`：单词本（可以直接编辑）
- `user/lessons/`：自己的每日课程

`news/` 下抓取的新闻和生成的页面也不提交，每个人在自己的电脑上抓取。

## 修改语法大纲

`data/grammar_syllabus.json` 是一个按学习顺序排列的数组，可以调整顺序、删除或新增语法点。每一项包含：`id`（不要修改已经学过的点的 id）、`level`、`title`、`brief`、`match`（判断标题是否用到这个点的规则：`token` 按分词结果匹配，`note` 按标题写法提示匹配，`regex` 用正则匹配标题）。

改完后运行下面的命令，可以看到每个点在已有标题里能命中多少条：

/opt/miniconda3/bin/python3 scripts/syllabus_report.py

## 测试

/opt/miniconda3/bin/python3 -m unittest discover -s tests -v
