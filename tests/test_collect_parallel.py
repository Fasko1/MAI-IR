# Пояснение к файлу: Автопроверки: параллельный обход, планирование и изоляция задержек источников.
# Как читать: Подготовка фикстур/подмен зависимостей → вызов рабочей функции → assert ожидаемого поведения. Группа входит в project.ps1 tests.
# Команды запуска и структура проекта: README.md.

"""Contracts for simultaneous hosts, serialized writes, limits and cached imports."""
from contextlib import ExitStack, redirect_stdout
import io
import json
import signal
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

from tools import collect_corpus as core, collect_parallel as parallel, pilot_candidates as pilot


class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.state=self.root/'data/expansion';self.state.mkdir(parents=True)
        self.stack=ExitStack();self.stack.enter_context(patch.object(core,'STATE',self.state))
        self.q=core.queue_open();parallel.prepare_runtime(self.q)

    def tearDown(self):
        self.q.close();self.stack.close();self.tmp.cleanup()

    def test_candidate_urls_deduplicate_company_aliases_and_keep_pagination(self):
        urls=['https://habr.com/ru/articles/123/','https://habr.com/ru/companies/team/articles/123/']
        for url in urls:parallel.enqueue_url(self.q,core,'habr','article',url)
        self.assertEqual(self.q.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)
        self.assertTrue(parallel.is_listing('elementy','https://elementy.ru/novosti_nauki?page=3'))
        self.assertFalse(parallel.is_listing('elementy','https://elementy.ru.example.org/novosti_nauki?page=3'))

    def test_robots_preserve_host_minimum_delay(self):
        client=parallel.HostClient(parallel.DEFINITIONS['elementy'],.25)
        with patch.object(client,'get',return_value=SimpleNamespace(text='User-agent: *\nCrawl-delay: 60\nAllow: /')):
            client.load_robots(self.root)
        self.assertEqual(client.delay,60)
        self.assertFalse(client.session.trust_env)
        client.session.close()

    def test_worker_link_discovery_preserves_relative_links_and_host_boundaries(self):
        html='''<a href="/ru/articles/123/#part">Article</a>
            <a href="/ru/articles/123/">Duplicate</a>
            <a href="/ru/hubs/physics/articles/page2/">Next</a>
            <a href="https://habr.com.evil.org/ru/articles/456/">Other host</a>'''
        self.assertEqual(parallel.publication_links('habr',html,'https://habr.com/ru/articles/'),[
            ('article','https://habr.com/ru/articles/123/'),
            ('listing','https://habr.com/ru/hubs/physics/articles/page2/')])

    def test_403_retains_job_and_persists_source_pause(self):
        core.enqueue(self.q,'habr','article','private')
        job=core.next_job(self.q,'habr','article')
        response=requests.Response();response.status_code=403
        with patch.object(core.time,'time',return_value=1000),redirect_stdout(io.StringIO()):
            parallel.handle_error(self.q,'habr',job,requests.HTTPError('403',response=response),
                                  {'source_403_cooldown_seconds':3600},core,SimpleNamespace(delay=10))
        row=self.q.execute('SELECT * FROM jobs').fetchone()
        self.assertEqual((row['status'],row['retry_at']),('pending',4600))
        self.assertEqual(core.source_health(self.q,'habr')['cooldown_until'],4600)
        self.assertEqual(core.source_health(self.q,'scfh')['cooldown_until'],0)

    def test_429_uses_retry_after_and_slows_only_affected_host(self):
        core.enqueue(self.q,'ixbt','article','limited')
        response=requests.Response();response.status_code=429;response.headers['Retry-After']='7200'
        client=SimpleNamespace(delay=.25)
        with patch.object(core.time,'time',return_value=1000),redirect_stdout(io.StringIO()):
            parallel.handle_error(self.q,'ixbt',core.next_job(self.q,'ixbt','article'),
                                  requests.HTTPError('429',response=response),{},core,client)
        self.assertEqual(core.source_health(self.q,'ixbt')['cooldown_until'],8200)
        self.assertEqual(self.q.execute('SELECT retry_at FROM jobs').fetchone()[0],8200)
        self.assertGreaterEqual(client.delay,.5)

    def test_wiki_parallel_attempt_defers_pause_without_sleeping_worker(self):
        client=core.WikiClient(.1);client.max_attempts=1
        response=requests.Response();response.status_code=429;response.headers['Retry-After']='60'
        with patch.object(client.session,'get',return_value=response) as get,patch.object(core.time,'sleep') as sleep:
            with self.assertRaises(core.WikiBackoff):client.query(action='query')
        self.assertEqual(get.call_count,1)
        self.assertTrue(all(call.args[0]==0 for call in sleep.call_args_list))
        client.session.close()

    def test_cached_import_is_idempotent_and_counts_only_new_text(self):
        folder=self.root/'data/source_candidates/pilot-100';folder.mkdir(parents=True)
        pq=pilot.open_queue(folder/'queue.sqlite')
        (folder/'habr').mkdir()
        html='<h1>Article</h1><div id="post-content-body"><p>'+('Science and engineering. '*40)+'</p></div>'
        pilot.save_result(pq,'habr','https://habr.com/ru/articles/123/',html,folder/'habr');pq.commit();pq.close()
        cfg=dict(pilot_import_directory='data/source_candidates/pilot-100',source_order=['habr'],
                 _document_count=10,_unique_text_bytes=10000,max_documents=100,min_text_bytes=300,
                 source_topic_filters={'habr':False},stop_at_target=False)
        with patch.object(core,'ROOT',self.root),patch.object(core,'save_article',return_value='duplicate') as save,redirect_stdout(io.StringIO()):
            self.assertEqual(parallel.import_pilot(self.q,Mock(),cfg,core),0)
            self.assertEqual(parallel.import_pilot(self.q,Mock(),cfg,core),0)
        self.assertEqual(save.call_count,1)
        self.assertEqual(cfg['_document_count'],10)
        self.assertEqual(self.q.execute('SELECT status FROM jobs').fetchone()[0],'duplicate')

    def exercise_runner(self,document_limit,interrupt_shutdown=False):
        for name,url in [('3dnews','https://3dnews.ru/123/obzor-device'),('scfh','https://scfh.ru/papers/science/')]:
            parallel.enqueue_url(self.q,core,name,'article',url)
        self.q.commit()
        cfg=dict(source_order=['3dnews','scfh'],max_documents=document_limit,delay_seconds=0,
                 min_text_bytes=300,source_topic_filters={'3dnews':False,'scfh':False},
                 min_free_disk_bytes=0,target_bytes=4100000000,stop_at_target=False,
                 parallel_workers=2,pilot_import_directory='data/source_candidates/absent')
        barrier=threading.Barrier(2);main_thread=threading.get_ident();writes=[]
        class FakeClient:
            def __init__(self,definition,delay):
                self.delay=delay
                self.session=SimpleNamespace(last_start_epoch=0,requests_count=0,http_seconds=0,close=lambda:None)
            def load_robots(self,directory):pass
            def get(self,url):
                barrier.wait(timeout=5)  # Both hosts must enter before either can complete.
                response=requests.Response();response.status_code=200;response.url=url
                response.headers['Content-Type']='text/html';response.encoding='utf-8'
                response._content=('<h1>Science</h1><div class="entry-body"><div class="js-mediator-article">'
                    '<div class="article-content"><p>'+('Scientific explanation. '*40)+'</p></div></div></div>').encode()
                self.session.last_start_epoch=time.time();self.session.requests_count+=1
                return response
        self.runner_states=[]
        def progress(conn,q,cfg,state):
            self.runner_states.append(state)
            cfg.setdefault('_document_count',0)
            return 0
        class InterruptedPool(parallel.ThreadPoolExecutor):
            def shutdown(self,*args,**kwargs):
                signal.raise_signal(signal.SIGINT)
                signal.raise_signal(signal.SIGINT)
                return super().shutdown(*args,**kwargs)
        def save(*args):
            self.assertEqual(threading.get_ident(),main_thread)
            writes.append(args[6]);return 'saved'
        with ExitStack() as stack,redirect_stdout(io.StringIO()):
            if interrupt_shutdown:
                stack.enter_context(patch.object(parallel,'ThreadPoolExecutor',InterruptedPool))
            for target,name,value in [(core,'connect',Mock(return_value=Mock())),(core,'load_config',Mock(return_value={})),
                (core,'init_hashes',Mock()),(core,'status',Mock(side_effect=progress)),
                (core,'save_article',Mock(side_effect=save)),(parallel,'HostClient',FakeClient),
                (parallel,'seed_sources',Mock()),(parallel,'seed_pilot',Mock()),
                (parallel,'import_pilot',Mock(return_value=0))]:stack.enter_context(patch.object(target,name,value))
            parallel.run_parallel(SimpleNamespace(status=False,max_new=2),cfg,core)
        return writes

    def test_hosts_fetch_concurrently_but_writes_are_serialized(self):
        self.assertEqual(len(self.exercise_runner(10)),2)
        self.assertEqual(self.q.execute("SELECT COUNT(*) FROM jobs WHERE status='saved'").fetchone()[0],2)

    def test_document_limit_does_not_overshoot_with_two_completed_fetches(self):
        self.assertEqual(len(self.exercise_runner(1)),1)
        self.assertEqual(self.q.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0],1)

    def test_ctrl_c_during_shutdown_preserves_pending_jobs_and_final_status(self):
        previous=signal.getsignal(signal.SIGINT)
        self.assertEqual(len(self.exercise_runner(1,interrupt_shutdown=True)),1)
        self.assertEqual(self.runner_states[-1],'interrupted')
        self.assertEqual(self.q.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0],1)
        self.assertIs(signal.getsignal(signal.SIGINT),previous)

    def test_cleanup_restores_signal_handler_even_on_error(self):
        previous=signal.getsignal(signal.SIGINT)
        callback=Mock()
        with self.assertRaisesRegex(RuntimeError,'cleanup failure'):
            with parallel.defer_cleanup_interrupts(callback):
                signal.raise_signal(signal.SIGINT)
                callback.assert_called_once()
                raise RuntimeError('cleanup failure')
        self.assertIs(signal.getsignal(signal.SIGINT),previous)


if __name__=='__main__':unittest.main()
