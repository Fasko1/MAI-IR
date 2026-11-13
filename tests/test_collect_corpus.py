# Пояснение к файлу: Автопроверки: очередь расширенного сборщика, лимиты, ошибки и сохранение.
# Как читать: Подготовка фикстур/подмен зависимостей → вызов рабочей функции → assert ожидаемого поведения. Группа входит в project.ps1 tests.
# Команды запуска и структура проекта: README.md.

"""Offline checks of content deduplication, category traversal and Wikipedia cleanup."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from contextlib import redirect_stdout
import io
import requests

from tools import collect_corpus as collector


class CollectorTests(unittest.TestCase):
    def test_wikipedia_uses_known_articles_before_discovering_more_categories(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(collector,"STATE",Path(tmp)):
            q=collector.queue_open()
            collector.enqueue(q,'wikipedia','category','Science',depth=0)
            for i in range(3):collector.enqueue(q,'wikipedia','article',i)
            cfg={'wiki_prefetch_articles':3}
            self.assertEqual(collector.scheduled_jobs(q,'wikipedia',cfg),[('wikipedia','article')])
            q.execute("UPDATE jobs SET status='saved' WHERE kind='article' AND key='0'")
            self.assertEqual(collector.scheduled_jobs(q,'wikipedia',cfg),[('wikipedia','category'),('wikipedia','article')])
            # A delayed retry does not count as immediately downloadable stock.
            q.execute("UPDATE jobs SET retry_at=9999999999 WHERE kind='article'")
            self.assertEqual(collector.scheduled_jobs(q,'wikipedia',{'wiki_prefetch_articles':1})[0],('wikipedia','category'))
            self.assertEqual(collector.scheduled_jobs(q,'hi-news',cfg),[('hi-news','sitemap'),('hi-news','article')])
            q.close()

    def test_relaxed_sources_keep_indirect_topics_and_shorter_text(self):
        cfg={"source_topic_filters":{"hi-news":False},"wiki_topic_filter":False,
             "wiki_exclude_titles":"цивилизаци","min_text_bytes":2000,
             "source_min_text_bytes":{"hi-news":300},"wiki_min_text_bytes":100}
        self.assertTrue(collector.accept_source_topic("hi-news","Новости устройств","Краткий текст",cfg))
        self.assertTrue(collector.accept_source_topic("wikipedia","История цивилизации","Описание",cfg))
        self.assertFalse(collector.accept_source_topic("naked-science","История цивилизации","Описание",cfg))
        self.assertEqual(collector.minimum_text_bytes("hi-news",cfg),300)
        self.assertEqual(collector.minimum_text_bytes("wikipedia",cfg),100)
        self.assertEqual(collector.minimum_text_bytes("naked-science",cfg),2000)

    def test_new_order_finishes_hi_news_then_wikipedia_before_naked_science(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(collector,"STATE",Path(tmp)):
            q=collector.queue_open()
            cfg={"source_order":["hi-news","wikipedia","naked-science"]}
            for source in cfg["source_order"]:collector.enqueue(q,source,"article",source)
            self.assertEqual(collector.active_source(q,cfg),"hi-news")
            q.execute("UPDATE jobs SET status='saved' WHERE source='hi-news'")
            self.assertEqual(collector.active_source(q,cfg),"wikipedia")
            q.execute("UPDATE jobs SET status='saved' WHERE source='wikipedia'")
            self.assertEqual(collector.active_source(q,cfg),"naked-science")
            q.close()

    def test_wiki_long_retry_after_is_preserved_in_queue_and_source_pause(self):
        r=requests.Response();r.status_code=429;r.headers['Retry-After']='3600'
        wiki=collector.WikiClient(0.1)
        with patch.object(wiki.session,'get',return_value=r),patch.object(collector.time,'sleep'):
            with self.assertRaises(collector.WikiBackoff) as caught:wiki.query(action='query')
        self.assertEqual(caught.exception.retry_after,3600)
        self.assertGreaterEqual(wiki.delay,0.5)
        wiki.session.close()
        with tempfile.TemporaryDirectory() as tmp,patch.object(collector,'STATE',Path(tmp)), \
                patch.object(collector.time,'time',return_value=1000),redirect_stdout(io.StringIO()):
            q=collector.queue_open()
            collector.enqueue(q,'wikipedia','article','page')
            collector.log_error(q,collector.next_job(q,'wikipedia','article'),caught.exception,{})
            self.assertEqual(q.execute("SELECT retry_at FROM jobs").fetchone()[0],4600)
            self.assertEqual(collector.source_health(q,'wikipedia')['cooldown_until'],4600)
            q.close()

    def test_wiki_retry_after_supports_http_date_and_invalid_values(self):
        r=requests.Response()
        r.headers['Retry-After']='Thu, 01 Jan 1970 01:00:00 GMT'
        with patch.object(collector.time,'time',return_value=1000):
            self.assertEqual(collector.retry_after_seconds(r,10),2600)
        r.headers['Retry-After']='invalid'
        self.assertEqual(collector.retry_after_seconds(r,10),10)

    def test_sequential_source_waits_for_delayed_retry_then_advances(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(collector,"STATE",Path(tmp)):
            q=collector.queue_open()
            cfg={"source_order":["hi-news","naked-science","wikipedia"]}
            collector.enqueue(q,"hi-news","article","first")
            collector.enqueue(q,"naked-science","article","second")
            q.execute("UPDATE jobs SET retry_at=9999999999 WHERE source='hi-news'")
            self.assertIsNone(collector.next_job(q,"hi-news","article"))
            self.assertEqual(collector.active_source(q,cfg),"hi-news")
            q.execute("UPDATE jobs SET status='saved' WHERE source='hi-news'")
            self.assertEqual(collector.active_source(q,cfg),"naked-science")
            q.execute("UPDATE jobs SET status='failed'")
            self.assertIsNone(collector.active_source(q,cfg))
            q.close()

    def test_server_pause_persists_escalates_and_resets_on_success(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(collector,"STATE",Path(tmp)), \
                patch.object(collector.time,"time",return_value=1000), redirect_stdout(io.StringIO()):
            q=collector.queue_open()
            cfg={"source_error_threshold":2,"source_cooldown_seconds":300,"source_max_cooldown_seconds":600}
            collector.note_response(q,"naked-science",True,cfg)
            self.assertEqual(collector.source_health(q,"naked-science")["cooldown_until"],0)
            collector.note_response(q,"naked-science",True,cfg)
            q.close();q=collector.queue_open()
            self.assertEqual(collector.source_health(q,"naked-science")["cooldown_until"],1300)
            for _ in range(2):collector.note_response(q,"naked-science",True,cfg)
            self.assertEqual(collector.source_health(q,"naked-science")["cooldown_until"],1600)
            collector.note_response(q,"naked-science",False,cfg)
            self.assertEqual(collector.source_health(q,"naked-science")["cooldown_round"],0)
            self.assertEqual(collector.source_health(q,"naked-science")["cooldown_until"],0)
            q.close()

    def test_error_classification_retries_500_but_skips_disambiguation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(collector,"STATE",Path(tmp)):
            q=collector.queue_open()
            collector.enqueue(q,"naked-science","article","server")
            collector.enqueue(q,"wikipedia","article","ambiguous")
            r=requests.Response();r.status_code=500
            error=requests.HTTPError("500 Server Error",response=r)
            with redirect_stdout(io.StringIO()) as output:
                collector.log_error(q,collector.next_job(q,"naked-science","article"),error,{})
                collector.log_error(q,collector.next_job(q,"wikipedia","article"),ValueError("Disambiguation page"),{})
            self.assertIn("RETRY naked-science",output.getvalue())
            self.assertIn("SKIP wikipedia",output.getvalue())
            self.assertEqual(q.execute("SELECT status FROM jobs WHERE key='server'").fetchone()[0],"pending")
            self.assertEqual(q.execute("SELECT status FROM jobs WHERE key='ambiguous'").fetchone()[0],"rejected")
            r.status_code=404
            self.assertFalse(collector.retryable_error(requests.HTTPError(response=r)))
            q.close()

    def test_document_cap_stops_before_any_network_request(self):
        cfg={"source_order":list(collector.NAMES),"max_documents":1000000,"delay_seconds":0.5,
             "wiki_categories":[],"min_free_disk_bytes":0,"target_bytes":4100000000,"stop_at_target":False}
        def progress(conn,q,config,state):
            config["_document_count"]=1000000
            return 0
        with tempfile.TemporaryDirectory() as tmp, patch.object(collector,"STATE",Path(tmp)), \
                patch.object(collector,"load_config",return_value={"db":{}, "logic":{"max_documents":1000000,"delay_seconds":0.5}, "collection":cfg}), \
                patch.object(collector,"connect",return_value=Mock()), \
                patch.object(collector,"init_hashes"), \
                patch.object(collector,"status",side_effect=progress) as status, \
                patch.object(collector.Client,"get") as network, \
                patch.object(collector.WikiClient,"query") as wiki_network:
            collector.run(Mock(status=False,max_new=0))
            self.assertEqual(status.call_args.args[-1],"document_limit_reached")
            network.assert_not_called();wiki_network.assert_not_called()

    def test_indirect_wiki_topics_are_accepted_when_filter_disabled(self):
        config={"wiki_topic_filter":False,"wiki_exclude_titles":"цивилизаци"}
        self.assertTrue(collector.accept_wiki_topic("История цивилизации","Историческое описание",config))
        self.assertTrue(collector.accept_wiki_topic("Биография инженера","Краткое описание",config))
        config["wiki_topic_filter"]=True
        self.assertFalse(collector.accept_wiki_topic("История цивилизации","Описание",config))

    def test_topic_filter_rejects_general_history(self):
        config={"wiki_exclude_titles":"(?i)цивилизаци|религи"}
        self.assertFalse(collector.wiki_on_topic("История цивилизации","Физика и наука",config))
        self.assertFalse(collector.wiki_on_topic("Город","История поселения и его правителей",config))
        self.assertTrue(collector.wiki_on_topic("Атом","Квантовая физика изучает атомы.",config))
        self.assertTrue(collector.wiki_on_topic("Масштабный коэффициент (космология)","Определение коэффициента.",config))

    def test_whitespace_changes_do_not_create_new_content(self):
        self.assertEqual(collector.digest_text(" alpha\n\nbeta "),collector.digest_text("alpha beta"))
        self.assertNotEqual(collector.digest_text("alpha beta"),collector.digest_text("alpha gamma"))

    def test_wiki_cleanup_keeps_scientific_body_and_removes_references(self):
        html = '''<div class="mw-parser-output"><table><tr><td>Infobox</td></tr></table>
        <p>First fact<sup class="reference">[1]</sup>.</p><h2>Mechanism</h2>
        <ul><li>Step one</li><li><p>Step two</p></li></ul><h2>Примечания</h2>
        <ol><li>Bibliographic reference</li></ol><h2>Further research</h2><p>Another fact.</p></div>'''
        text = collector.wiki_text(html)
        self.assertIn("First fact",text)
        self.assertIn("Another fact",text)
        self.assertEqual(text.count("Step two"),1)
        for noise in ("Infobox","[1]","Bibliographic reference"):
            self.assertNotIn(noise,text)

    def test_queue_survives_reopen_and_deduplicates_jobs(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(collector,"STATE",Path(tmp)):
            q=collector.queue_open()
            collector.enqueue(q,"wikipedia","article",123,title="Physics")
            collector.enqueue(q,"wikipedia","article",123,title="Physics again")
            q.commit();q.close()
            q=collector.queue_open()
            self.assertEqual(q.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],1)
            self.assertEqual(collector.next_job(q,"wikipedia","article")["key"],"123")
            q.close()

    def test_category_cycles_depth_and_continuation(self):
        class Wiki:
            def query(self,**params):
                return {"query":{"categorymembers":[{"ns":0,"pageid":1,"title":"Physics"},
                    {"ns":14,"pageid":2,"title":"Категория:Физика"},
                    {"ns":14,"pageid":3,"title":"Категория:Персоналии"}]},
                    "continue":{"cmcontinue":"next"}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(collector,"STATE",Path(tmp)):
            q=collector.queue_open()
            collector.enqueue(q,"wikipedia","category","Категория:Физика",depth=0)
            q.commit()
            config={"wiki_depth":1,"wiki_exclude_categories":"Персоналии"}
            job=collector.next_job(q,"wikipedia","category")
            collector.category_step(q,Wiki(),job,config)
            self.assertEqual(q.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],2)
            self.assertIn('next',collector.next_job(q,"wikipedia","category")["payload"])
            self.assertIsNotNone(collector.next_job(q,"wikipedia","article"))
            q.close()

    def test_new_root_promotes_existing_category_and_is_processed_first(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(collector,"STATE",Path(tmp)):
            q=collector.queue_open()
            collector.enqueue(q,"wikipedia","category","Earlier branch",depth=2)
            collector.enqueue(q,"wikipedia","category","New root",depth=3)
            job=q.execute("SELECT * FROM jobs WHERE key='New root'").fetchone()
            collector.finish(q,job,"done")
            collector.enqueue(q,"wikipedia","category","New root",depth=0)
            root=collector.next_job(q,"wikipedia","category")
            self.assertEqual(root["key"],"New root")
            self.assertEqual(root["status"],"pending")
            self.assertIn('"depth": 0',root["payload"])
            self.assertEqual(q.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],2)
            q.close()


if __name__ == "__main__":
    unittest.main()
