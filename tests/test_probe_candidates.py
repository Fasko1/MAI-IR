# Пояснение к файлу: Автопроверки: канонизация URL и извлечение текста сайтов-кандидатов.
# Как читать: Подготовка фикстур/подмен зависимостей → вызов рабочей функции → assert ожидаемого поведения. Группа входит в project.ps1 tests.
# Команды запуска и структура проекта: README.md.

import unittest

from tools.probe_candidates import CANDIDATES, extract, article_url


class CandidateProbeTests(unittest.TestCase):
    def test_review_excludes_other_articles_and_keeps_body(self):
        prose='Engineering explanation with measurements. '*12
        html=f'''<h1>Review</h1><div class="entry-body" itemprop="articleBody">
          <div class="js-mediator-article"><p>{prose}</p></div>
          <div class="slider-slide-content"><h3>Unrelated recommendation</h3></div></div>'''
        text=extract(html,CANDIDATES['3dnews'])['clean_text']
        self.assertIn(prose.strip(),text)
        self.assertNotIn('Unrelated recommendation',text)

    def test_gallery_does_not_replace_scientific_text(self):
        prose='Research explanation with supporting evidence. '*12
        html=f'''<h1>Science</h1><div itemprop="articleBody"><div class="image-slider">
          <p>Gallery caption</p></div><p>{prose}</p></div>'''
        text=extract(html,CANDIDATES['nkj'])['clean_text']
        self.assertNotIn('Gallery caption',text)
        self.assertIn(prose.strip(),text)

    def test_habr_prose_is_not_inflated_with_code_and_promotions(self):
        prose='Scientific and technical discussion. '*12
        html=f'''<h1>Article</h1><div id="post-content-body"><p>{prose}</p>
          <pre>large code listing</pre><details><summary>Может быть интересно:</summary>
          <p>Other article recommendation</p></details><p>Новости — в нашем Telegram-канале!</p></div>'''
        text=extract(html,CANDIDATES['habr'])['clean_text']
        for extra in ('large code listing','Other article recommendation','Telegram'):
            self.assertNotIn(extra,text)

    def test_foreign_hosts_and_missing_body_are_rejected(self):
        self.assertIsNone(article_url('https://www.nkj.ru.example.org/archive/articles/123/',CANDIDATES['nkj']))
        with self.assertRaises(ValueError):
            extract('<h1>Menu</h1><nav>'+'menu '*100+'</nav>',CANDIDATES['nkj'])


if __name__=='__main__':unittest.main()
