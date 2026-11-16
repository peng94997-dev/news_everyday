import sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import analyze  # noqa: E402
import fetch_news  # noqa: E402
import lesson  # noqa: E402


def tok(s):
    return fetch_news.tokenize(s, headline=False)


CASES = [
    ("私は映画を見ることが好きです。", ["私は", "映画を", "見る", "ことが", "好きです。"],
     ["topic", "object", "nominal", "liked", "predicate"]),
    ("昨日見た映画は面白かった。", ["昨日", "見た", "映画は", "面白かった。"], ["time", "modifier", "topic", "predicate"]),
    ("ご飯を食べて、寝ます。", ["ご飯を", "食べて、", "寝ます。"], ["object", "te", "predicate"]),
    ("とても静かな町です。", ["とても", "静かな", "町です。"], ["adverb", "modifier", "predicate"]),
    ("毎日日本語を勉強しています。", ["毎日", "日本語を", "勉強しています。"], ["time", "object", "predicate"]),
    ("雨だから、家で本を読みたい。", ["雨だから、", "家で", "本を", "読みたい。"], ["reason", "de", "object", "predicate"]),
    ("行きますか？", ["行きますか？"], ["predicate"]),
    ("新宿から東京まで歩きます。", ["新宿から", "東京まで", "歩きます。"], ["from", "until", "predicate"]),
    ("この本はとても面白いと思います。", ["この本は", "とても", "面白いと", "思います。"],
     ["topic", "adverb", "quote", "predicate"]),
    ("冷たい水を飲んでもいいですか。", ["冷たい", "水を", "飲んでも", "いいですか。"],
     ["modifier", "object", "concession", "predicate"]),
    ("先生が「明日は休みです」と言いました。", ["先生が", "「明日は", "休みです」と", "言いました。"],
     ["subject", "topic", "quote", "predicate"]),
]


class ChunkTest(unittest.TestCase):
    def test_chunks_and_roles(self):
        for text, chunks, roles in CASES:
            with self.subTest(text=text):
                got = analyze.chunk_tokens(tok(text))
                self.assertEqual(["".join(t["s"] for t in c) for c in got], chunks)
                self.assertEqual(analyze.label_roles(got), roles)

    def test_split_newlines_and_marks(self):
        self.assertEqual(analyze.split_sentences("私は学生です。\n映画が好き！本も読む"),
                         ["私は学生です。", "映画が好き！", "本も読む"])

    def test_role_zh_covers_all(self):
        for _, _, roles in CASES:
            for r in roles:
                self.assertIn(r, analyze.ROLE_ZH)
        self.assertEqual(analyze.ROLE_ZH["unknown"], "未识别")


PATTERNS = lesson.load_syllabus(analyze.PATTERNS_PATH)
SYLLABUS = lesson.load_syllabus()


def run(text, learned=frozenset(), tokenize_fn=tok):
    return analyze.analyze_text(text, tokenize_fn, lambda s: [[s, ""]], PATTERNS, SYLLABUS, set(learned))


class ResultTest(unittest.TestCase):
    def test_patterns_file_valid(self):
        self.assertTrue(40 <= len(PATTERNS) <= 60)
        ids = {p["id"] for p in SYLLABUS}
        for p in PATTERNS:
            if "syllabus" in p:
                self.assertIn(p["syllabus"], ids, p["id"])

    def test_example_sentence_patterns(self):
        expect = {0: {"v-koto", "ga-suki"}, 4: {"te-iru"}, 5: {"tai", "kara-reason"}, 6: {"masu-ka"}}
        for i, ids in expect.items():
            with self.subTest(text=CASES[i][0]):
                got = {p["id"] for p in run(CASES[i][0])["sentences"][0]["patterns"]}
                self.assertTrue(ids <= got, got)

    def test_no_false_pattern_matches(self):
        ids = lambda t: {p["id"] for p in run(t)["sentences"][0]["patterns"]}
        self.assertNotIn("temo", ids("とても静かな町です。"))
        self.assertNotIn("you-da", ids("家で本を読みたい。"))
        self.assertIn("you-da", ids("雨みたいだ。"))
        self.assertIn("temo", ids("雨が降っても行きます。"))

    def test_analyze_text_shape(self):
        s = run(CASES[0][0])["sentences"][0]
        self.assertEqual(s["chunks"][1], {"text": "映画を", "role": "object", "role_zh": analyze.ROLE_ZH["object"],
                                          "zh": "电影"})
        w = next(w for w in s["words"] if w["word"] == "映画")
        self.assertEqual((w["kana"], w["jlpt"]), ("えいが", "N5"))

    def test_learned_marks(self):
        s = run(CASES[0][0], learned={"wo-object"})["sentences"][0]
        self.assertEqual([x["id"] for x in s["learned"]], ["wo-object"])
        tai = next(p for p in PATTERNS if p.get("syllabus") == "tai-want")
        s = run("映画を見たい。", learned={"tai-want"})["sentences"][0]
        self.assertTrue(next(p for p in s["patterns"] if p["id"] == tai["id"])["learned"])

    def test_unknown_word_placeholder(self):
        fake = lambda s: [{"s": "ほげ", "k": "n", "p": "名词", "j": "名詞,一般", "m": None}]
        self.assertEqual(run("ほげ", tokenize_fn=fake)["sentences"][0]["words"][0]["zh"], "（辞书里没有）")

    def test_non_japanese_input(self):
        out = run("hello 123")
        self.assertEqual(len(out["sentences"]), 1)
        for c in out["sentences"][0]["chunks"]:
            self.assertIn(c["role"], analyze.ROLE_ZH)

    def test_rejects_empty_and_too_long(self):
        for bad in ("   ", "あ" * 201):
            with self.assertRaises(ValueError):
                run(bad)
        run("あ" * 200)


if __name__ == "__main__":
    unittest.main()
