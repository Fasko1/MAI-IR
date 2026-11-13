# Пояснение к файлу: Автопроверки: обход широких архивов и фильтрация ссылок.
# Как читать: Подготовка фикстур/подмен зависимостей → вызов рабочей функции → assert ожидаемого поведения. Группа входит в project.ps1 tests.
# Команды запуска и структура проекта: README.md.

from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

from tools import collect_corpus as core, collect_parallel as parallel
from tools.corpus_catalog import configure, canonical, extract_publication
from tools.seed_wide_archives import seed_wide


class WideArchiveTests(unittest.TestCase):
    def setUp(self):
        self.definitions=deepcopy(parallel.DEFINITIONS);configure(self.definitions)

    def test_news_and_all_publication_sections_are_in_scope(self):
        urls={'ixbt':['https://www.ixbt.com/news/2026/11/08/science.html','https://www.ixbt.com/live/science/article.html'],
              '3dnews':['https://3dnews.ru/1123456/scientific-discovery'],
              'nkj':['https://www.nkj.ru/news/12345/'],'scfh':['https://scfh.ru/news/discovery/'],
              'ichip':['https://ichip.ru/podborki/interesting-123456']}
        urls['techinsider']=['https://www.techinsider.ru/science/news-434752-indostan-geology/']
        urls['biomolecula']=['https://biomolecula.ru/notices/discovery']
        for name,values in urls.items():
            for url in values:self.assertIsNotNone(canonical(name,url,self.definitions[name]),url)
        self.assertIsNone(canonical('ixbt','https://www.ixbt.com.evil.org/news/article.html',self.definitions['ixbt']))

    def test_accept_all_overrides_all_old_keyword_exclusions(self):
        cfg={'accept_all_publications':True,'source_topic_filters':{'naked-science':True},'wiki_topic_filter':True}
        for name in core.NAMES:self.assertTrue(core.accept_source_topic(name,'Гороскоп и политика','Короткая новость',cfg))

    def test_short_publications_are_kept(self):
        article=extract_publication('scfh','<h1>Brief</h1><div class="article-content"><p>A short fact.</p></div>',
                                    'https://scfh.ru/news/fact/',self.definitions['scfh'],1)
        self.assertEqual(article['clean_text'],'A short fact.')
        bare=extract_publication('scfh','<h1>Brief</h1><div class="article-content">Bare text fact.</div>',
                                 'https://scfh.ru/news/fact/',self.definitions['scfh'],1)
        self.assertEqual(bare['clean_text'],'Bare text fact.')

    def test_original_source_aliases_use_one_key(self):
        d=self.definitions['techinsider']
        self.assertEqual(canonical('techinsider','https://techinsider.ru/science/123-fact/',d),
                         canonical('techinsider','https://www.techinsider.ru/science/123-fact/',d))

    def test_new_sources_require_article_body_and_exclude_related_promotions(self):
        cases=[('hightech','<div itemprop="articleBody"><div class="page-content__body"><p>Full science text.</p></div><p>Advertisement</p></div>'),
               ('securitylab','<div class="articl-text"><div itemprop="articleBody"><p>Full science text.</p></div><p>Advertisement</p></div>'),
               ('vokrugsveta','<article><section class="ds-article-content"><p>Full science text.</p></section><p>Advertisement</p></article>')]
        for name,body in cases:
            definition=self.definitions[name]
            item=extract_publication(name,'<h1>Science</h1>'+body,definition['root'],definition,1)
            self.assertEqual(item['clean_text'],'Full science text.')
            with self.assertRaises(ValueError):
                extract_publication(name,'<h1>Science</h1><article><p>Advertisement</p></article>',definition['root'],definition,1)

    def test_recovery_never_crosses_site_minimum(self):
        client=SimpleNamespace(delay=30,minimum_delay=2);entry={'success_streak':0}
        for _ in range(300):parallel.recover_rate(client,entry,{'rate_recovery_successes':5})
        self.assertEqual(client.delay,2)
        client.minimum_delay=60;client.delay=90
        for _ in range(30):parallel.recover_rate(client,entry,{'rate_recovery_successes':5})
        self.assertEqual(client.delay,60)

    def test_access_challenge_is_deferred_instead_of_saved_as_article(self):
        client=parallel.HostClient(self.definitions['ferra'],1)
        response=requests.Response();response.status_code=200;response.headers['Content-Type']='text/html'
        response._content=b'<title>Auth-controller</title>'
        with patch.object(parallel.Client,'get',return_value=response):
            with self.assertRaises(requests.RequestException) as caught:client.get('https://www.ferra.ru/')
        self.assertEqual(caught.exception.retry_after,3600)
        client.session.close()

    def test_500_page_does_not_permanently_slow_the_entire_host(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(core,'STATE',Path(tmp)),redirect_stdout(io.StringIO()):
            q=core.queue_open();core.enqueue(q,'naked-science','article','server')
            response=requests.Response();response.status_code=500
            client=SimpleNamespace(delay=2)
            parallel.handle_error(q,'naked-science',core.next_job(q,'naked-science','article'),
                                  requests.HTTPError('500',response=response),{},core,client)
            self.assertEqual(client.delay,2);q.close()

    def test_expansion_is_idempotent_and_preserves_cooldowns(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(core,'ROOT',Path(tmp)),patch.object(core,'STATE',Path(tmp)/'state'),redirect_stdout(io.StringIO()):
            core.STATE.mkdir();q=core.queue_open();parallel.prepare_runtime(q)
            folder=Path(tmp)/'data/source_candidates/census/ixbt-news';folder.mkdir(parents=True)
            (folder/'urls.txt').write_text('https://www.ixbt.com/news/2026/11/08/fact.html\n',encoding='utf-8')
            core.enqueue(q,'naked-science','article','old');q.execute("UPDATE jobs SET status='off_topic'")
            core.note_response(q,'habr',True,{})
            q.execute("UPDATE source_health SET cooldown_until=9999999999 WHERE source='habr'")
            cfg={'source_order':['ixbt','naked-science','habr'],'wide_census_directory':'data/source_candidates/census',
                 'candidate_seed_archive':'missing','delay_seconds':2}
            seed_wide(q,Mock(),cfg,core,self.definitions)
            self.assertEqual(q.execute("SELECT status FROM jobs WHERE key='old'").fetchone()[0],'pending')
            self.assertEqual(core.source_health(q,'habr')['cooldown_until'],9999999999)
            q.execute("UPDATE jobs SET status='saved' WHERE key='old'");q.commit()
            seed_wide(q,Mock(),cfg,core,self.definitions)
            self.assertEqual(q.execute("SELECT status FROM jobs WHERE key='old'").fetchone()[0],'saved')
            self.assertEqual(q.execute("SELECT COUNT(*) FROM jobs WHERE key LIKE '%fact.html'").fetchone()[0],1)
            q.close()


if __name__=='__main__':unittest.main()
