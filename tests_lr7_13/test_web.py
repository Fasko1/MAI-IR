# Пояснение к файлу: HTTP-проверки локального поискового интерфейса.
# Как читать: Временный сервер/индекс → запросы → HTML, ошибки и навигация. Сервер закрывается после теста, внешний интернет не нужен.
# Команды запуска и структура проекта: README.md.

"""Проверка настоящего HTTP-сервиса с C++-индексом на изолированном корпусе."""
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.project.build import EXE, compiler
from lab08.web import create_server, safe_link


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler()
        cls.temp = tempfile.TemporaryDirectory(prefix="mai_ir_web_")
        folder = Path(cls.temp.name)
        lines = []
        for i in range(1, 57):
            source = folder / f"{i}.stems"
            source.write_text("agent\n007\n", encoding="utf-8")
            lines.append(f'{i}\t{source.as_posix()}\t<script>bad</script> {i}\thttps://example.test/{i}')
        manifest = folder / "manifest.tsv"
        manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        cls.index = folder / "test.ir"
        subprocess.run([str(ROOT / ("build/ir_engine" + EXE)), "build", "--manifest", str(manifest),
                        "--index", str(cls.index)], check=True, capture_output=True)
        cls.server = create_server(cls.index, port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temp.cleanup()

    def get(self, path):
        try:
            response = urlopen(self.base + path, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.read().decode("utf-8")

    def test_form_and_health(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn('name="q"', body)
        self.assertIn("TF-IDF", body)
        status, body = self.get("/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ready"])

    def test_pagination_and_html_escaping(self):
        status, body = self.get("/?" + urlencode({"q": "agent"}))
        self.assertEqual(status, 200)
        self.assertEqual(body.count("<li>"), 50)
        self.assertIn("Следующие 50", body)
        self.assertIn("&lt;script&gt;bad&lt;/script&gt;", body)
        self.assertNotIn("<script>", body)
        status, body = self.get("/?" + urlencode({"q": "agent", "offset": 50}))
        self.assertEqual(status, 200)
        self.assertEqual(body.count("<li>"), 6)
        self.assertIn("Предыдущие 50", body)

    def test_ranked_preserves_numeric_identifier(self):
        status, body = self.get("/?" + urlencode({"q": "007", "mode": "ranked"}))
        self.assertEqual(status, 200)
        self.assertIn("TF-IDF:", body)
        self.assertEqual(body.count("<li>"), 50)

    def test_invalid_query_and_route(self):
        status, body = self.get("/?" + urlencode({"q": "agent && ("}))
        self.assertEqual(status, 422)
        self.assertIn('role="alert"', body)
        self.assertEqual(self.get("/?q=agent&offset=oops")[0], 400)
        self.assertEqual(self.get("/missing")[0], 404)

    def test_links(self):
        self.assertEqual(safe_link("javascript:alert(1)"), "")
        self.assertEqual(safe_link("https://example.test/a"), "https://example.test/a")

    def test_multiline_query_is_one_query(self):
        status, body = self.get("/?" + urlencode({"q": "agent\nunknownterm"}))
        self.assertEqual(status, 200)
        self.assertEqual(body.count("<li>"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
