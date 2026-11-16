import sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import analyze  # noqa: E402
import fetch_news  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
