import json, sys, tempfile, unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import lesson  # noqa: E402


class MatchTest(unittest.TestCase):
    ITEM = {"title": "新宿から通報", "tokens": [
        {"s": "新宿", "k": "pn"}, {"s": "から", "k": "pt"},
        {"s": "通報", "k": "n", "p": "名词", "note": "标题省略了「する／した」：表示“进行了／将进行 通報”"}]}

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
        pt = {"id": "a", "level": "N5", "title": "t", "brief": "b", "match": [{"regex": "x"}]}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text(json.dumps([pt, pt]))
            with self.assertRaises(ValueError):
                lesson.load_syllabus(p)

    def test_load_syllabus_rejects_missing_field(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text(json.dumps([{"id": "a", "level": "N5", "title": "t", "brief": "b"}]))
            with self.assertRaises(ValueError):
                lesson.load_syllabus(p)

    def test_real_syllabus_is_valid(self):
        pts = lesson.load_syllabus()
        self.assertGreaterEqual(len(pts), 60)
        self.assertLessEqual(len(pts), 80)


class MigrateTest(unittest.TestCase):
    def test_migrate_moves_vocab(self):
        with tempfile.TemporaryDirectory() as d:
            news, user = Path(d) / "news", Path(d) / "user"
            news.mkdir()
            (news / "vocab.md").write_text("# 单词本\n## 東京\n")
            self.assertTrue(lesson.migrate_user_data(news, user))
            self.assertFalse((news / "vocab.md").exists())
            self.assertEqual((user / "vocab.md").read_text(), "# 单词本\n## 東京\n")
            self.assertTrue((user / "lessons").is_dir())

    def test_migrate_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            news, user = Path(d) / "news", Path(d) / "user"
            news.mkdir(); user.mkdir()
            (news / "vocab.md").write_text("old")
            (user / "vocab.md").write_text("mine")
            self.assertFalse(lesson.migrate_user_data(news, user))
            self.assertEqual((news / "vocab.md").read_text(), "old")
            self.assertEqual((user / "vocab.md").read_text(), "mine")


TODAY = date(2026, 11, 14)


def pt(id, word):
    return {"id": id, "level": "N5", "title": id, "brief": "b", "match": [{"regex": word}]}


def it(title, published="2026-11-14 08:00"):
    return {"title": title, "published": published, "tokens": []}


def empty():
    return {"grammar": {}, "lessons": {}}


class PlanTest(unittest.TestCase):
    SYL = [pt("p1", "AAA"), pt("p2", "BBB")]

    def test_plan_continue_blocks_new(self):
        prog = empty()
        prog["lessons"]["2026-11-12"] = {"kind": "new", "point": "p1", "points": [], "done": False}
        plan = lesson.plan_today(self.SYL, prog, [it("BBB"), it("BBB2")], TODAY)
        self.assertEqual(plan, {"action": "continue", "date": "2026-11-12"})

    def test_plan_new_picks_first_unlearned_with_two_hits(self):
        items = [it("AAA x"), it("BBB y", "2026-11-14 01:00"), it("BBB z", "2026-11-14 09:00")]
        plan = lesson.plan_today(self.SYL, empty(), items, TODAY)
        self.assertEqual(plan["action"], "new")
        self.assertEqual(plan["point"], "p2")
        self.assertEqual([x["title"] for x in plan["examples"]], ["BBB z", "BBB y"])

    def test_plan_skips_learned(self):
        prog = empty()
        prog["grammar"]["p1"] = {"learned": "2026-11-10", "level": 1, "due": "2026-11-20"}
        plan = lesson.plan_today(self.SYL, prog, [it("AAA 1"), it("AAA 2"), it("BBB 1"), it("BBB 2")], TODAY)
        self.assertEqual(plan["point"], "p2")

    def test_plan_lookahead_limit_is_10(self):
        syl = [pt(f"p{i}", f"W{i:02d}") for i in range(13)]
        items = [it("W12 a"), it("W12 b")]  # 最後の点だけ当たる → 最初の未習得点から 10 個先まで見ても見つからない
        self.assertEqual(lesson.plan_today(syl, empty(), items, TODAY), {"action": "none"})
        prog = empty()
        prog["grammar"]["p0"] = {"learned": "2026-11-01", "level": 3, "due": "2026-11-30"}
        plan = lesson.plan_today(syl, prog, items, TODAY)
        self.assertEqual(plan, {"action": "review", "points": ["p0"]})

    def test_plan_review_when_more_than_5_due(self):
        syl = [pt(f"p{i}", f"W{i}") for i in range(8)]
        prog = empty()
        for i in range(6):
            prog["grammar"][f"p{i}"] = {"learned": "2026-11-01", "level": 1, "due": "2026-11-13"}
        plan = lesson.plan_today(syl, prog, [it("W7 a"), it("W7 b")], TODAY)
        self.assertEqual(plan["action"], "review")
        self.assertEqual(plan["points"], ["p0", "p1", "p2", "p3", "p4"])

    def test_unknown_point_in_progress_ignored(self):
        prog = empty()
        prog["grammar"]["gone"] = {"learned": "2026-11-01", "level": 1, "due": "2026-11-01"}
        self.assertEqual(lesson.due_points(prog, self.SYL, TODAY), [])
        self.assertEqual(lesson.plan_today(self.SYL, prog, [it("AAA"), it("AAA2")], TODAY)["point"], "p1")


class ProgressTest(unittest.TestCase):
    def test_mark_done_sets_review(self):
        prog = empty()
        prog["lessons"]["2026-11-14"] = {"kind": "new", "point": "p1", "points": [], "done": False}
        lesson.mark_done(prog, "2026-11-14", TODAY)
        self.assertTrue(prog["lessons"]["2026-11-14"]["done"])
        self.assertEqual(prog["grammar"]["p1"], {"learned": "2026-11-14", "level": 1, "due": "2026-11-15"})

    def test_mark_done_idempotent(self):
        prog = empty()
        prog["lessons"]["2026-11-14"] = {"kind": "new", "point": "p1", "points": [], "done": False}
        lesson.mark_done(prog, "2026-11-14", TODAY)
        before = json.dumps(prog, sort_keys=True)
        lesson.mark_done(prog, "2026-11-14", TODAY + timedelta(days=3))
        self.assertEqual(json.dumps(prog, sort_keys=True), before)

    def test_corrupt_progress_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "progress.json"
            p.write_text("{")
            with self.assertRaises(ValueError):
                lesson.load_progress(p)

    def test_missing_progress_is_empty(self):
        self.assertEqual(lesson.load_progress(Path("/nonexistent/progress.json")), empty())

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "progress.json"
            prog = empty()
            prog["grammar"]["の"] = {"learned": "2026-11-14", "level": 1, "due": "2026-11-15"}
            lesson.save_progress(p, prog)
            self.assertEqual(lesson.load_progress(p), prog)

    def test_next_review_matches_vocab_rule(self):
        self.assertEqual(lesson.next_review(0, 2, TODAY), (1, "2026-11-15"))
        self.assertEqual(lesson.next_review(3, 1, TODAY), (3, "2026-11-15"))
        self.assertEqual(lesson.next_review(3, 0, TODAY), (0, "2026-11-14"))
        self.assertEqual(lesson.next_review(7, 2, TODAY), (7, "2027-01-13"))

    def test_review_point_updates(self):
        prog = empty()
        prog["grammar"]["p1"] = {"learned": "2026-11-10", "level": 1, "due": "2026-11-14"}
        lesson.review_point(prog, "p1", 2, TODAY)
        self.assertEqual(prog["grammar"]["p1"]["level"], 2)
        self.assertEqual(prog["grammar"]["p1"]["due"], "2026-11-16")


class WordsTest(unittest.TestCase):
    def test_learned_words(self):
        words = [{"word": "東京", "level": 1}, {"word": "外国", "level": 0}]
        self.assertEqual(lesson.learned_words(words), {"東京"})

    def test_pick_new_words_skips_learned(self):
        ex = [{"tokens": [{"s": "東京", "lv": "N5"}, {"s": "開か", "b": "開く", "lv": "N4"},
                          {"s": "外国", "lv": "N5"}, {"s": "難民", "lv": "N1"}, {"s": "外国", "lv": "N5"}]}]
        got = lesson.pick_new_words(ex, {"東京"}, n=5)
        self.assertEqual([t["s"] for t in got], ["開か", "外国"])


if __name__ == "__main__":
    unittest.main()
