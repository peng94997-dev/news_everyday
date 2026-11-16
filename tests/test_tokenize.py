import sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_news  # noqa: E402


def tok(s, **kw):
    return {t["s"]: t for t in fetch_news.tokenize(s, **kw)}


class TokenizeTest(unittest.TestCase):
    def test_formal_noun_has_meaning(self):
        self.assertIn("事", tok("見ることが好き", headline=False)["こと"]["m"])

    def test_na_adjective_display(self):
        t = tok("見ることが好き", headline=False)["好き"]
        self.assertEqual(t["p"], "形容动词（な形容词）")
        self.assertEqual(t["k"], "n")

    def test_raw_pos_field(self):
        self.assertEqual(tok("昨日見た映画", headline=False)["昨日"]["j"], "名詞,副詞可能")
        self.assertEqual(tok("雨だから行かない", headline=False)["から"]["j"], "助詞,接続助詞")

    def test_headline_notes_off(self):
        self.assertFalse(any(t.get("note") for t in fetch_news.tokenize("行きますか", headline=False)))
        self.assertTrue(tok("殺人関与か")["か"].get("note"))

    def test_tokens_version_bumped(self):
        self.assertEqual(fetch_news.TOKENS_VERSION, 4)


if __name__ == "__main__":
    unittest.main()
