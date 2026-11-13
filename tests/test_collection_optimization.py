# Пояснение к файлу: Автопроверки: настройки ускорения, ограничения и сохранность качества извлечения.
# Как читать: Подготовка фикстур/подмен зависимостей → вызов рабочей функции → assert ожидаемого поведения. Группа входит в project.ps1 tests.
# Команды запуска и структура проекта: README.md.

"""Regression cases for queue migration, delayed retries, discovery and real article bodies."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import requests
from tools import collect_corpus as core,collect_parallel as parallel
from tools.corpus_catalog import canonical,configure,extract_publication
from tools.optimize_collection import migrate_queue


class OptimizationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.state_patch=patch.object(core,'STATE',self.root);self.state_patch.start()
        self.q=core.queue_open();parallel.prepare_runtime(self.q)
        self.definitions=deepcopy(parallel.DEFINITIONS);configure(self.definitions)

    def tearDown(self):
        self.q.close();self.state_patch.stop();self.temp.cleanup()

    def test_index_preserves_delayed_jobs_and_reverse_order(self):
        for key in ('first','later','last'):core.enqueue(self.q,'hi-news','article',key)
        self.q.execute("UPDATE jobs SET retry_at=9999999999 WHERE key='last'")
        self.assertEqual(core.next_job(self.q,'hi-news','article')['key'],'later')
        core.enqueue(self.q,'ixbt','article','first');core.enqueue(self.q,'ixbt','article','last')
        self.assertEqual(core.next_job(self.q,'ixbt','article')['key'],'first')
        plan=self.q.execute("EXPLAIN QUERY PLAN SELECT * FROM jobs INDEXED BY jobs_pick WHERE source='ixbt' AND kind='article' AND status='pending' AND retry_at<=1 ORDER BY rowid LIMIT 1").fetchall()
        self.assertFalse(any('TEMP B-TREE' in row[3] for row in plan))

    def test_all_storage_disks_are_checked_and_unavailable_storage_stops_collection(self):
        cfg={'min_free_disk_bytes':8,'storage_paths':[str(self.root/'database')]}
        with patch.object(core.shutil,'disk_usage',side_effect=[SimpleNamespace(free=100),SimpleNamespace(free=7)]):
            self.assertFalse(core.has_storage_space(cfg))
        with patch.object(core.shutil,'disk_usage',side_effect=[SimpleNamespace(free=100),OSError('Drive unavailable')]):
            self.assertFalse(core.has_storage_space(cfg))
        with patch.object(core.shutil,'disk_usage',return_value=SimpleNamespace(free=100)):
            self.assertTrue(core.has_storage_space(cfg))

    def test_sleep_prevention_is_released_even_when_collection_fails(self):
        from unittest.mock import Mock
        import ctypes
        api=Mock(return_value=0x80000000)
        fake=SimpleNamespace(kernel32=SimpleNamespace(SetThreadExecutionState=api))
        with patch.object(core.os,'name','nt'),patch.object(ctypes,'windll',fake,create=True),redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                with core.prevent_idle_sleep():raise RuntimeError('Collection ended')
        self.assertEqual([call.args[0] for call in api.call_args_list],[0x80000001,0x80000000])

    def test_maps_are_serviced_with_a_large_article_backlog_and_deferred_map(self):
        for key in range(12):core.enqueue(self.q,'ixbt','article',str(key))
        core.enqueue(self.q,'ixbt','sitemap','map');core.enqueue(self.q,'ixbt','listing','list')
        cfg={'discovery_batch_articles':3}
        kinds=[]
        for _ in range(8):
            job=parallel.select_job(self.q,'ixbt',cfg,core);kinds.append(job['kind'])
            core.finish(self.q,job,'done')
        self.assertEqual(kinds,['article']*3+['sitemap']+['article']*3+['listing'])

    def test_dns_failure_keeps_attempts_and_preserves_server_pause(self):
        core.enqueue(self.q,'ixbt','article','page')
        self.q.execute("UPDATE jobs SET attempts=3")
        self.q.execute("INSERT INTO source_health VALUES('ixbt',0,5000,0)")
        error=requests.ConnectionError('NameResolutionError: getaddrinfo failed')
        with patch.object(core.time,'time',return_value=1000),redirect_stdout(io.StringIO()):
            core.log_error(self.q,core.next_job(self.q,'ixbt','article'),error,{'dns_retry_seconds':30})
        row=self.q.execute('SELECT * FROM jobs').fetchone()
        self.assertEqual((row['status'],row['attempts'],row['retry_at']),('pending',3,1030))
        self.assertEqual(core.source_health(self.q,'ixbt')['cooldown_until'],5000)

    def test_identity_migration_keeps_saved_alias_and_continuation_and_is_repeatable(self):
        entries=[('http://3dnews.ru/123/title','saved'),('https://3dnews.ru/123/','pending'),
                 ('https://3dnews.ru/123/page-2.html','pending')]
        for key,status in entries:
            core.enqueue(self.q,'3dnews','article',key,request_url=key)
            self.q.execute('UPDATE jobs SET status=? WHERE key=?',(status,key))
        cfg={'source_order':['3dnews']}
        with redirect_stdout(io.StringIO()):
            result=migrate_queue(self.q,cfg,core,self.definitions)
            self.assertEqual(migrate_queue(self.q,cfg,core,self.definitions),{})
        self.assertEqual(result['3dnews_id_page_groups'],2)
        self.assertEqual(self.q.execute("SELECT status FROM jobs WHERE key='https://3dnews.ru/123'").fetchone()[0],'saved')
        self.assertEqual(self.q.execute("SELECT status FROM jobs WHERE key='https://3dnews.ru/123/page-2.html'").fetchone()[0],'pending')
        snapshot=json.loads(self.q.execute("SELECT snapshot FROM job_aliases WHERE original_key=?",(entries[0][0],)).fetchone()[0])
        self.assertEqual(snapshot['status'],'saved')

    def test_continuations_with_all_known_page_spellings_keep_their_identity(self):
        for suffix in ('page-2.html','page_2.html','page2.html','page-3'):
            url='http://3dnews.ru/123/'+suffix
            self.assertEqual(canonical('3dnews',url,self.definitions['3dnews']),'https://3dnews.ru/123/'+suffix)

    def test_nkj_requeues_verified_sections_and_not_catalogs(self):
        for path in ('news/123','facts/456','archive/2004/9','shop/789'):
            core.enqueue(self.q,'nkj','article','https://www.nkj.ru/'+path+'/')
        self.q.execute("UPDATE jobs SET status='rejected',error='Missing title or article container'")
        with redirect_stdout(io.StringIO()):migrate_queue(self.q,{'source_order':['nkj']},core,self.definitions)
        self.assertEqual(self.q.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0],2)

    def test_nkj_extracts_body_without_related_articles(self):
        html='<h1>Research</h1><article class="news-detail"><p>Full research text.</p><section class="rel-articles"><p>Other article.</p></section></article>'
        result=extract_publication('nkj',html,'https://www.nkj.ru/news/123/',self.definitions['nkj'],1)
        self.assertEqual(result['clean_text'],'Full research text.')

    def test_4pda_comment_fallback_is_rejected_but_real_short_article_is_kept(self):
        definition=self.definitions['4pda']
        with self.assertRaisesRegex(ValueError,'verified 4PDA'):
            extract_publication('4pda','<h1>Article</h1><div class="content">A reader comment.</div>',definition['root'],definition,1)
        html='<h1>Article</h1><div class="content-box"><p>A short real publication.</p></div><div class="content">Comment.</div>'
        self.assertEqual(extract_publication('4pda',html,definition['root'],definition,1)['clean_text'],'A short real publication.')

    def test_declared_maps_and_listing_pagination_are_scoped_to_the_host(self):
        with patch.dict(parallel.DEFINITIONS,self.definitions):
            self.assertTrue(parallel.is_listing('securitylab','https://www.securitylab.ru/analytics/?PAGEN_1=2'))
            self.assertFalse(parallel.is_listing('securitylab','https://www.securitylab.ru.evil.org/analytics/?PAGEN_1=2'))
            parallel.apply_result(self.q,None,'securitylab',{},dict(type='robots',sitemaps=[
                'https://www.securitylab.ru/upload/sitemaps/blog/sitemap_blog_ru.xml','https://evil.org/map.xml']),{},core)
        self.assertEqual(self.q.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)


if __name__=='__main__':unittest.main()
