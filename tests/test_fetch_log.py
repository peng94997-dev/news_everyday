import contextlib, io, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_news  # noqa: E402


class StartupLogTest(unittest.TestCase):
    """起動時の翻訳・講解補完は数分かかるので、始める前に何をしているか端末に出す。"""

    def run_quiet(self, fn):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            fn()
        return err.getvalue()

    def test_explain_announces_before_calling_claude(self):
        seen = []
        orig = fetch_news._claude_json
        fetch_news._claude_json = lambda prompt, timeout=600: seen.append(1) or [{"gist": "g", "chunks": [["a", "b", "c"]]}]
        fetch_news._cli_ok = True
        try:
            with tempfile.TemporaryDirectory() as d:
                out = self.run_quiet(lambda: fetch_news.explain_items([{"title": "東京の祭り"}], Path(d) / "e.json"))
        finally:
            fetch_news._claude_json = orig
        self.assertIn("正在生成句子讲解 1 条", out)
        self.assertIn("1/1", out)

    def test_translate_announces_before_calling(self):
        orig = fetch_news._via_claude_cli
        fetch_news._via_claude_cli = lambda titles: {t: "译" for t in titles}
        fetch_news._cli_ok = True
        try:
            with tempfile.TemporaryDirectory() as d:
                out = self.run_quiet(lambda: fetch_news.translate(["東京"], Path(d) / "t.json"))
        finally:
            fetch_news._via_claude_cli = orig
        self.assertIn("正在翻译 1 条", out)


class ClaudeTranslateTest(unittest.TestCase):
    def test_translation_with_unescaped_quotes(self):
        class R:
            returncode, stderr = 0, ""
            stdout = '["比"横滨"高的城市", "东京"]'  # 中文の引用に未エスケープの " を使った実出力の形
        orig = fetch_news.subprocess.run
        fetch_news.subprocess.run = lambda *a, **k: R()
        try:
            out = fetch_news._via_claude_cli(["横浜より高い街", "東京"])
        finally:
            fetch_news.subprocess.run = orig
        self.assertEqual(out, {"横浜より高い街": "比“横滨”高的城市", "東京": "东京"})


if __name__ == "__main__":
    unittest.main()
