# Пояснение к файлу: Автопроверки: пилотный сбор и отбор пробных публикаций.
# Как читать: Подготовка фикстур/подмен зависимостей → вызов рабочей функции → assert ожидаемого поведения. Группа входит в project.ps1 tests.
# Команды запуска и структура проекта: README.md.

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools import pilot_candidates as pilot


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.folder=Path(self.tmp.name)
        self.q=pilot.open_queue(self.folder/'queue.sqlite')
        self.cfg=dict(target_per_source=100,base_delay_seconds=.25,
                      max_article_attempts_per_source=500,max_listing_requests_per_source=40)
        for name in ('habr','elementy'):
            self.q.execute("INSERT INTO hosts(name,delay,robots) VALUES(?,.25,'User-agent: *\nAllow: /')",(name,))
        self.q.commit()

    def tearDown(self):
        self.q.close();self.tmp.cleanup()

    def host(self,name):
        return self.q.execute('SELECT * FROM hosts WHERE name=?',(name,)).fetchone()

    def test_cooling_host_does_not_block_other_host(self):
        pilot.enqueue(self.q,'elementy','article','https://elementy.ru/novosti_nauki/123/science')
        pilot.enqueue(self.q,'habr','article','https://habr.com/ru/articles/123/')
        self.q.execute("UPDATE hosts SET next_at=1000 WHERE name='elementy'")
        self.assertIsNone(pilot.choose_job(self.q,self.host('elementy'),self.cfg,100))
        self.assertEqual(pilot.choose_job(self.q,self.host('habr'),self.cfg,100)['kind'],'article')

    def test_company_and_direct_urls_share_one_article(self):
        pilot.enqueue(self.q,'habr','article','https://habr.com/ru/companies/company/articles/123/')
        pilot.enqueue(self.q,'habr','article','https://habr.com/ru/articles/123/')
        self.assertEqual(self.q.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)
        self.assertIn('/companies/',self.q.execute('SELECT request_url FROM jobs').fetchone()[0])

    def test_pagination_is_kept_but_foreign_hosts_excluded(self):
        self.assertTrue(pilot.listing_url('elementy','https://elementy.ru/novosti_nauki?page=2'))
        self.assertFalse(pilot.listing_url('elementy','https://other.ru/novosti_nauki?page=2'))
        self.assertTrue(pilot.listing_url('habr','https://habr.com/ru/hubs/physics/articles/page2/'))
        self.assertFalse(pilot.listing_url('habr','https://habr.com/ru/articles/?q=search'))

    def test_budgets_do_not_leave_host_idle_forever(self):
        pilot.enqueue(self.q,'habr','listing','https://habr.com/ru/hubs/physics/articles/')
        self.q.execute("UPDATE hosts SET listing_requests=40 WHERE name='habr'")
        self.assertFalse(pilot.has_work(self.q,self.host('habr'),self.cfg))
        pilot.enqueue(self.q,'habr','article','https://habr.com/ru/articles/123/')
        self.q.execute('UPDATE jobs SET retry_at=999999')
        self.assertTrue(pilot.has_work(self.q,self.host('habr'),self.cfg))

    def test_queue_persists_on_reopen(self):
        pilot.enqueue(self.q,'habr','article','https://habr.com/ru/articles/123/')
        self.q.execute('UPDATE jobs SET retry_at=1000,attempts=2');self.q.commit();self.q.close()
        self.q=pilot.open_queue(self.folder/'queue.sqlite')
        row=self.q.execute('SELECT * FROM jobs').fetchone()
        self.assertEqual((row['status'],row['attempts'],row['retry_at']),('pending',2,1000))

    def test_content_duplicates_are_not_counted(self):
        html='<h1>Science</h1><div id="post-content-body"><p>'+('Scientific explanation. '*40)+'</p></div>'
        self.assertTrue(pilot.save_result(self.q,'habr','https://habr.com/ru/articles/1/',html,self.folder))
        self.assertFalse(pilot.save_result(self.q,'habr','https://habr.com/ru/articles/2/',html,self.folder))
        self.assertEqual(self.q.execute('SELECT COUNT(*) FROM articles').fetchone()[0],1)
        self.assertEqual(len(list(self.folder.glob('*.review.html'))),1)

    def test_fresh_robots_imposes_host_interval(self):
        pilot.enqueue(self.q,'elementy','robots','https://elementy.ru/robots.txt')
        response=Mock(status_code=200,text='User-agent: *\nCrawl-delay: 60\nAllow: /',headers={})
        session=Mock();session.get.return_value=response
        job=self.q.execute("SELECT * FROM jobs WHERE kind='robots'").fetchone()
        with patch.object(pilot,'STATE',self.folder):
            pilot.process_job(self.q,{'elementy':session},self.host('elementy'),job,self.cfg)
        self.assertEqual(self.host('elementy')['delay'],60)

    def test_rate_limit_reschedules_and_honors_retry_after(self):
        pilot.enqueue(self.q,'habr','article','https://habr.com/ru/articles/123/')
        response=Mock(status_code=429,headers={'Retry-After':'120'})
        session=Mock();session.get.return_value=response
        job=self.q.execute('SELECT * FROM jobs').fetchone()
        with patch.object(pilot,'STATE',self.folder):
            pilot.process_job(self.q,{'habr':session},self.host('habr'),job,self.cfg)
        row=self.q.execute('SELECT * FROM jobs').fetchone()
        self.assertEqual(row['status'],'pending')
        self.assertGreaterEqual(row['retry_at']-pilot.time.time(),119)
        self.assertEqual(self.host('habr')['article_attempts'],1)


if __name__=='__main__':unittest.main()
