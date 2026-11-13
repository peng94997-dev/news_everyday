import json, sys, tempfile, unittest
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


if __name__ == "__main__":
    unittest.main()
