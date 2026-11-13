import contextlib, io, json, socket, sys, tempfile, threading, time, unittest, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_news  # noqa: E402
import lesson  # noqa: E402

LESSON = {"main": {"title": "AのB", "gist": "g", "reading": [{"text": "AのB", "zh": "z", "why": "w", "is_point": True}]},
          "point": {"explain": "e", "examples": []}, "words": [], "date": "2026-11-13", "kind": "new",
          "point_id": "no-link", "point_ids": [], "ruby": [[]]}


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.news, self.user = d / "news", d / "user"
        self.news.mkdir()
        (self.user / "lessons").mkdir(parents=True)
        (self.user / "lessons" / "2026-11-13.json").write_text(json.dumps(LESSON))
        lesson.save_progress(self.user / "progress.json", {"grammar": {}, "lessons": {
            "2026-11-13": {"kind": "new", "point": "no-link", "points": [], "done": False}}})
        self.orig = fetch_news.USER_DIR, fetch_news.build
        fetch_news.USER_DIR = self.user
        self.httpd = fetch_news.make_server(self.news, 0)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        fetch_news.USER_DIR, fetch_news.build = self.orig
        self.tmp.cleanup()

    def post(self, path, body, timeout):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST")
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

    def test_lesson_page_not_blocked_while_rebuilding(self):
        # build() が Claude で講解を補完して数分かかる状況を再現
        fetch_news.build = lambda news_dir: time.sleep(3)
        t0 = time.time()
        done = self.post("/api/lesson/done", {"date": "2026-11-13"}, timeout=10)
        self.assertTrue(done["done"])
        self.assertLess(time.time() - t0, 1.5, "「学完了」は再生成を待たずに返す")
        t0 = time.time()
        view = json.loads(urllib.request.urlopen(self.base + "/api/lesson", timeout=10).read())
        self.assertLess(time.time() - t0, 1.5, "再生成中でも /api/lesson はすぐ返る")
        self.assertTrue(view["done"])


    def test_grammar_review_saves(self):
        prog = lesson.load_progress(self.user / "progress.json")
        prog["grammar"]["no-link"] = {"learned": "2026-11-12", "level": 1, "due": "2026-11-13"}
        lesson.save_progress(self.user / "progress.json", prog)
        self.assertEqual(self.post("/api/grammar/review", {"id": "no-link", "grade": 2}, timeout=5), {"ok": True})
        self.assertEqual(lesson.load_progress(self.user / "progress.json")["grammar"]["no-link"]["level"], 2)

    def test_client_leaving_during_generate_is_one_line(self):
        orig = fetch_news.generate_today
        fetch_news.generate_today = lambda *a, **k: time.sleep(0.5)  # 生成中にページを閉じた
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                s = socket.create_connection(self.httpd.server_address)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
                s.sendall(b"POST /api/lesson/generate HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{}")
                time.sleep(0.1)
                s.close()  # RST で切断
                time.sleep(1)
        finally:
            fetch_news.generate_today = orig
        out = err.getvalue()
        self.assertNotIn("Traceback", out)
        self.assertIn("连接已断开", out)


if __name__ == "__main__":
    unittest.main()
