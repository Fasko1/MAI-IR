# Пояснение к файлу: Автопроверки: общие HTTP/robots/sitemap и извлечение дополнительных источников.
# Как читать: Подготовка фикстур/подмен зависимостей → вызов рабочей функции → assert ожидаемого поведения. Группа входит в project.ps1 tests.
# Команды запуска и структура проекта: README.md.

"""Offline checks for source extraction and sitemap interpretation."""
import unittest

from tools.probe_sources import SOURCES, extract_article, robots_allowed, sample_urls, same_host, sitemap_entries


class SourceProbeTests(unittest.TestCase):
    def test_short_article_can_be_admitted_without_accepting_page_navigation(self):
        text="A short report about a new device. "*5
        html=f'<h1>Device</h1><main><div class="item"><div class="text"><p>{text}</p></div></div></main>'
        with self.assertRaises(ValueError):extract_article(html,SOURCES["hi-news"])
        self.assertEqual(extract_article(html,SOURCES["hi-news"],min_characters=100)["clean_text"],text.strip())
        with self.assertRaises(ValueError):
            extract_article('<h1>Navigation</h1><nav>'+text+'</nav>',SOURCES["hi-news"],min_characters=100)

    def test_robots_query_rule_does_not_block_the_archive(self):
        robots = "User-agent: *\nDisallow: /?\nDisallow: /*?page\nDisallow: /wp-\n"
        self.assertTrue(robots_allowed(robots, "https://naked-science.ru/sitemap.xml"))
        self.assertFalse(robots_allowed(robots, "https://naked-science.ru/?s=query"))
        self.assertFalse(robots_allowed(robots, "https://naked-science.ru/article/foo?page=2"))
        self.assertFalse(robots_allowed(robots, "https://naked-science.ru/wp-admin/"))

    def test_robots_specific_agent_and_longest_rule_win(self):
        robots = "User-agent: *\nDisallow: /\nUser-agent: MAI-InfoSearch\nDisallow: /private\nAllow: /private/open$\n"
        self.assertTrue(robots_allowed(robots, "https://hi-news.ru/article"))
        self.assertTrue(robots_allowed(robots, "https://hi-news.ru/private/open"))
        self.assertFalse(robots_allowed(robots, "https://hi-news.ru/private/open/extra"))
        self.assertFalse(robots_allowed(robots, "https://hi-news.ru/article", "AnotherBot"))

    def test_sitemap_ignores_image_locations(self):
        xml = b'''<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
          xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
          <url><loc>https://hi-news.ru/science/article.html</loc>
          <image:image><image:loc>https://example.org/a.jpg</image:loc></image:image>
          </url></urlset>'''
        self.assertEqual(sitemap_entries(xml), ("urlset", ["https://hi-news.ru/science/article.html"]))

    def test_hi_news_keeps_lists_without_navigation_or_promotion(self):
        paragraph = "Scientific explanation with details. " * 12
        html = f'''<nav><p>Navigation</p></nav><h1>Title</h1><main><div class="item">
          <div class="text"><p>{paragraph}</p><h2>Steps</h2><ul><li><p>First step</p></li>
          <li>Second step</li></ul><p>Читайте также: unrelated article</p>
          <aside><p>Advertisement</p></aside><p class="wp-caption-text">Image caption</p>
          <p>Join our <a href="https://t.me/hi_news_chat">Telegram</a>!</p>
          </div></div></main><footer>Footer</footer>'''
        text = extract_article(html, SOURCES["hi-news"])["clean_text"]
        self.assertIn("Steps", text)
        self.assertEqual(text.count("First step"), 1)
        self.assertIn("Second step", text)
        for unwanted in ("Navigation", "unrelated article", "Advertisement", "Footer", "Image caption", "Telegram"):
            self.assertNotIn(unwanted, text)

    def test_naked_science_uses_body_and_keeps_quotation(self):
        paragraph = "Description of a research finding. " * 12
        html = f'''<h1>Finding</h1><article class="ns-editorial-article">
          <div class="post-lead">Introductory summary</div>
          <div class="body"><p>{paragraph}</p><blockquote><p>Researcher's quote</p></blockquote></div>
          <div class="comments"><p>Reader comment</p></div></article>'''
        text = extract_article(html, SOURCES["naked-science"])["clean_text"]
        self.assertIn("Researcher's quote", text)
        self.assertTrue(text.startswith("Introductory summary"))
        self.assertNotIn("Reader comment", text)

    def test_missing_article_is_not_replaced_with_page_text(self):
        with self.assertRaises(ValueError):
            extract_article("<h1>Error</h1><nav>" + "Menu " * 100 + "</nav>", SOURCES["hi-news"])

    def test_archive_sample_includes_both_ends_without_duplicates(self):
        urls = [str(i) for i in range(100)]
        sampled = sample_urls(urls, 20)
        self.assertEqual((sampled[0], sampled[-1]), ("0", "99"))
        self.assertEqual(len(set(sampled)), 20)
        self.assertEqual(sample_urls([], 20), [])
        self.assertEqual(sample_urls(urls, 1), ["50"])

    def test_foreign_hosts_are_rejected(self):
        for url in ("https://hi-news.ru.example.org/article", "file:///hi-news.ru"):
            self.assertFalse(same_host(url, SOURCES["hi-news"]))


if __name__ == "__main__":
    unittest.main()
