# Пояснение к файлу: Автопроверки: базовый робот, извлечение, обновление и восстановление состояния.
# Как читать: Подготовка фикстур/подмен зависимостей → вызов рабочей функции → assert ожидаемого поведения. Группа входит в project.ps1 tests.
# Команды запуска и структура проекта: README.md.

"""Проверки фильтрации sitemap, ответов сервера и сохранения очереди.

Запуск: python -m unittest discover -s tests -p test_crawler.py
"""
import tempfile
import unittest
from contextlib import ExitStack
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import requests
from bs4.exceptions import ParserRejectedMarkup
from lab02 import crawler


class CrawlerTests(unittest.TestCase):
    def exercise_update(self, status_code):
        """Проверяем настоящую фазу обновления без сети и рабочей БД."""
        conn=MagicMock()
        cursor=conn.cursor.return_value.__enter__.return_value
        url='https://biomolecula.ru/articles/test'
        cursor.fetchall.return_value=[(url,url,'article text','title','author',None,'yesterday','etag')]
        response=SimpleNamespace(status_code=status_code,url=url,headers={},text='html')
        config={'logic':{'delay_seconds':0,'update_interval_seconds':0}}
        with patch.object(crawler,'request_with_retries',return_value=response) as request, \
             patch.object(crawler,'parse_article_response',return_value=('article text','title','author',None)), \
             patch.object(crawler.time,'time',return_value=1000), \
             patch.object(crawler.time,'sleep'):
            crawler.run_update_phase(conn,config,Mock(),document_limit=1)
        self.assertEqual(cursor.execute.call_args_list[0].args[1],(1000,1))
        self.assertIn('LIMIT %s',cursor.execute.call_args_list[0].args[0])
        self.assertEqual(request.call_args.kwargs['headers'],{'If-Modified-Since':'yesterday','If-None-Match':'etag'})
        conn.commit.assert_called_once()
        self.assertNotIn('clean_text =',cursor.execute.call_args.args[0])

    def test_update_304_checks_headers_and_preserves_article(self):
        self.exercise_update(304)

    def test_update_200_compares_content_and_preserves_unchanged_article(self):
        self.exercise_update(200)

    def test_article_urls(self):
        for url in (
            "https://www.techinsider.ru/technologies/449352-rocket/",
            "https://biomolecula.ru/articles/kletka",
        ):
            self.assertTrue(crawler.qualifies_as_article(url), url)

    def test_media_and_foreign_hosts(self):
        for url in (
            "https://images.techinsider.ru/upload/img_cache/75d/file.jpg",
            "https://www.techinsider.ru/upload/123.JPG?size=740",
            "https://www.techinsider.ru/upload/123.pdf",
            "https://biomolecula.ru/upload/file.png",
            "https://techinsider.ru.example.org/123-article/",
            "ftp://www.techinsider.ru/123-article/",
        ):
            self.assertFalse(crawler.qualifies_as_article(url), url)

    def test_sitemap_excludes_nested_image_locations(self):
        response = Mock()
        response.status_code = 200
        response.content = b'''<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
          xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
          <url><loc>https://www.techinsider.ru/123-article/</loc>
          <image:image><image:loc>https://images.techinsider.ru/123.jpg</image:loc>
          </image:image></url></urlset>'''
        session = Mock()
        session.get.return_value = response
        self.assertEqual(crawler.fetch_sitemap_urls("https://example.org/sitemap.xml", session),
                         ["https://www.techinsider.ru/123-article/"])
        response.content = b'''<sitemapindex><sitemap>
          <loc>https://example.org/sitemap1.xml</loc></sitemap></sitemapindex>'''
        self.assertEqual(crawler.fetch_sitemap_urls("https://example.org/sitemap.xml", session),
                         ["https://example.org/sitemap1.xml"])

    def test_non_html_never_reaches_parser(self):
        for content_type in ("image/jpeg", "application/pdf", ""):
            response = SimpleNamespace(headers={"Content-Type": content_type})
            with patch.object(crawler, "extract_clean_text") as parser:
                self.assertIsNone(crawler.parse_article_response(response, "https://example.org/123"))
                parser.assert_not_called()

    def test_rejected_markup_does_not_abort_crawl(self):
        response = SimpleNamespace(headers={"Content-Type": "text/html"}, text="broken")
        with patch.object(crawler, "extract_clean_text", side_effect=ParserRejectedMarkup("bad HTML")):
            self.assertIsNone(crawler.parse_article_response(response, "https://example.org/123"))

    def test_valid_html_still_extracts_text_and_metadata(self):
        response = SimpleNamespace(headers={"Content-Type": "text/html; charset=utf-8"}, text="html")
        with patch.object(crawler, "extract_clean_text", return_value="article text"), \
             patch.object(crawler, "extract_metadata", return_value=("title", "author", None)):
            self.assertEqual(crawler.parse_article_response(response, "https://example.org/123"),
                             ("article text", "title", "author", None))

    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "crawler_state.pkl"
            with patch.object(crawler, "STATE_FILE", path):
                crawler.save_state(deque([("https://biomolecula.ru/articles/test", "source")]), 105162)
                state = crawler.load_state()
                self.assertEqual(state["saved_count"], 105162)
                self.assertEqual(len(state["queue"]), 1)
                self.assertFalse(path.with_suffix(".pkl.tmp").exists())

    def test_html_fallback_without_jsonld(self):
        html = '''<nav><p>menu</p></nav><article><h1>Title</h1>
          <p>First paragraph.</p><aside><p>advertisement</p></aside>
          <p>Second paragraph.</p></article><footer><p>footer</p></footer>'''
        self.assertEqual(crawler.extract_clean_text(html, "https://www.techinsider.ru/science/123/"),
                         "First paragraph. Second paragraph.")

    def test_missing_article_is_not_replaced_with_navigation(self):
        self.assertEqual(crawler.extract_clean_text("<nav><p>menu</p></nav>",
                         "https://www.techinsider.ru/science/123/"), "")

    def test_dns_failure_then_recovery(self):
        session = Mock()
        response = SimpleNamespace(status_code=200)
        session.get.side_effect = [requests.ConnectionError("DNS failure"), response]
        with patch.object(crawler.time, "sleep") as sleep:
            self.assertIs(crawler.request_with_retries(session, "https://example.org", timeout=30), response)
            sleep.assert_called_once_with(5)

    def test_persistent_dns_failure_is_reported_after_three_attempts(self):
        session = Mock()
        session.get.side_effect = requests.ConnectionError("DNS failure")
        with patch.object(crawler.time, "sleep"), self.assertRaises(requests.ConnectionError):
            crawler.request_with_retries(session, "https://example.org", timeout=30)
        self.assertEqual(session.get.call_count, 3)

    def test_server_failure_then_recovery(self):
        session = Mock()
        response = requests.Response()
        response.status_code = 503
        recovered = SimpleNamespace(status_code=200)
        session.get.side_effect = [response, recovered]
        with patch.object(crawler.time, "sleep"):
            self.assertIs(crawler.request_with_retries(session, "https://example.org"), recovered)

    def test_network_outage_preserves_current_and_remaining_urls(self):
        queue = [("https://www.techinsider.ru/science/123/", "source"),
                 ("https://www.techinsider.ru/science/456/", "source")]
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(crawler, "STATE_FILE", Path(directory) / "state.pkl"))
            crawler.save_state(deque(queue), 100)
            stack.enter_context(patch.object(crawler, "load_config", return_value={
                "logic": {"max_documents": 200000, "respect_robots_txt": False}}))
            connection = stack.enter_context(patch.object(crawler, "connect", return_value=Mock()))
            stack.enter_context(patch.object(crawler, "create_tables"))
            stack.enter_context(patch.object(crawler, "count_documents", return_value=100))
            stack.enter_context(patch.object(crawler, "document_exists", return_value=False))
            stack.enter_context(patch.object(crawler.signal, "signal"))
            stack.enter_context(patch.object(crawler, "request_with_retries",
                                           side_effect=requests.ConnectionError("DNS failure")))
            update = stack.enter_context(patch.object(crawler, "run_update_phase"))
            crawler.main("unused.yaml")
            self.assertEqual(crawler.load_state(), {"queue": queue, "saved_count": 100})
            connection.return_value.close.assert_called_once()
            update.assert_not_called()

    def test_rescan_merges_old_queue_and_new_articles_with_backup(self):
        old_url = "https://www.techinsider.ru/science/123/"
        new_url = "https://www.techinsider.ru/science/456/"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "crawler_state.pkl"
            with patch.object(crawler, "STATE_FILE", path):
                crawler.save_state(deque([(old_url, "source")]), 100)
                original = path.read_bytes()
                with patch.object(crawler, "collect_urls", return_value=deque([
                    (old_url, "source"), (new_url, "source"),
                    ("https://images.techinsider.ru/123.jpg", "source")])):
                    queue = crawler.refresh_queue({}, Mock(), crawler.load_state())
                self.assertEqual(list(queue), [(old_url, "source"), (new_url, "source")])
                backups = list(Path(directory).glob("crawler_state.backup-*.pkl"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_bytes(), original)
                self.assertEqual(path.read_bytes(), original)

    def test_rescan_keeps_pending_articles_if_sitemaps_are_unavailable(self):
        url = "https://www.techinsider.ru/science/123/"
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(crawler, "STATE_FILE", Path(directory) / "state.pkl"), \
             patch.object(crawler, "collect_urls", return_value=deque()):
            queue = crawler.refresh_queue({}, Mock(), {"queue": [(url, "source")]})
            self.assertEqual(list(queue), [(url, "source")])


if __name__ == "__main__":
    unittest.main()
