# Пояснение к файлу: Пробный сбор небольшого числа публикаций до массового запуска.
# Как читать: Профиль источников → выбор URL → скачивание и извлечение → сохранение пилотных примеров/сводки. Проверяет пригодность источника.
# Команды запуска и структура проекта: README.md.

"""Resumable round-robin pilot of six sources, isolated from the main corpus."""
from __future__ import annotations

from html import escape
import hashlib
import json
from pathlib import Path
import random
import re
import shutil
import sqlite3
import statistics
import sys
import time
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import requests
import yaml
from bs4 import BeautifulSoup
from lxml.etree import XMLSyntaxError
from tools.probe_candidates import CANDIDATES, article_url, extract
from tools.probe_sources import USER_AGENT, robots_allowed, same_host, sitemap_entries
from tools.collect_corpus import STATE as MAIN_STATE, process_lock, retry_after_seconds

STATE=ROOT/'data/source_candidates/pilot-100'
MAPS={'nkj':'https://www.nkj.ru/sitemap.xml','scfh':'https://scfh.ru/sitemap.xml',
      '3dnews':'https://3dnews.ru/export/sitemaps/sitemap.xml',
      'ixbt':'https://www.ixbt.com/export/sitemapindex.xml'}


def open_queue(path):
    q=sqlite3.connect(path,timeout=30);q.row_factory=sqlite3.Row
    q.execute('PRAGMA journal_mode=WAL')
    q.executescript('''CREATE TABLE IF NOT EXISTS hosts (
        name TEXT PRIMARY KEY, delay REAL NOT NULL, next_at REAL NOT NULL DEFAULT 0,
        robots TEXT, state TEXT NOT NULL DEFAULT 'active', errors INTEGER NOT NULL DEFAULT 0,
        requests INTEGER NOT NULL DEFAULT 0, http_seconds REAL NOT NULL DEFAULT 0,
        service_seconds REAL NOT NULL DEFAULT 0, first_at REAL, finished_at REAL,
        article_attempts INTEGER NOT NULL DEFAULT 0, listing_requests INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS jobs (
        source TEXT,kind TEXT,url TEXT,request_url TEXT,status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,retry_at REAL NOT NULL DEFAULT 0,
        redirects INTEGER NOT NULL DEFAULT 0,error TEXT,PRIMARY KEY(source,kind,url));
    CREATE INDEX IF NOT EXISTS pilot_pending ON jobs(source,status,kind,retry_at);
    CREATE TABLE IF NOT EXISTS articles (
        source TEXT,url TEXT,title TEXT,sha256 TEXT,text_bytes INTEGER,words INTEGER,
        flags TEXT,stem TEXT,saved_at REAL,PRIMARY KEY(source,url),UNIQUE(source,sha256));
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT);''')
    q.commit();return q


def enqueue(q,name,kind,url):
    if not same_host(url,CANDIDATES[name]):return
    key=url
    if kind=='article':
        key=article_url(url,CANDIDATES[name])
        if not key:return
        if name=='habr':
            number=re.search(r'/articles/(\d+)',key).group(1)
            key=f'https://habr.com/ru/articles/{number}/'
    q.execute('INSERT OR IGNORE INTO jobs(source,kind,url,request_url) VALUES(?,?,?,?)',(name,kind,key,url))


def listing_url(name,url):
    if not same_host(url,CANDIDATES[name]):return False
    parts=urlsplit(url)
    if name=='elementy':
        params=parse_qs(parts.query)
        return parts.path.rstrip('/')=='/novosti_nauki' and set(params)<= {'page'}
    if name=='habr':
        return not parts.query and bool(re.fullmatch(r'/ru/(?:articles|hubs/[a-z0-9_-]+/articles)/(?:page\d+/)?',parts.path))
    return False


def seed_jobs(q,cfg):
    signature=json.dumps({'target':cfg['target_per_source'],'order':cfg['order']},sort_keys=True)
    old=q.execute("SELECT value FROM meta WHERE key='signature'").fetchone()
    if old and old[0]!=signature:raise ValueError('Pilot target/order changed: use a new pilot state directory')
    if old:return
    q.execute("INSERT INTO meta VALUES('signature',?)",(signature,))
    for name in cfg['order']:
        q.execute('INSERT INTO hosts(name,delay) VALUES(?,?)',(name,cfg['base_delay_seconds']))
        enqueue(q,name,'robots',CANDIDATES[name]['root']+'/robots.txt')
        urls_file=ROOT/cfg['seed_archive']/name/'urls.txt'
        if urls_file.exists():
            urls=list(dict.fromkeys(urls_file.read_text(encoding='utf-8').splitlines()))
            random.Random(f'{cfg["seed"]}:{name}').shuffle(urls)
            for url in urls:enqueue(q,name,'article',url)
        elif name in MAPS:enqueue(q,name,'sitemap',MAPS[name])
        if name=='elementy':enqueue(q,name,'listing','https://elementy.ru/novosti_nauki')
        if name=='habr':
            for hub in ('popular_science','physics','biology','astronomy','engineering_systems','artificial_intelligence'):
                enqueue(q,name,'listing',f'https://habr.com/ru/hubs/{hub}/articles/')
    q.commit()


def choose_job(q,host,cfg,now):
    if host['state']!='active' or host['next_at']>now:return None
    name=host['name']
    robots=q.execute("SELECT * FROM jobs WHERE source=? AND kind='robots' AND status='pending' AND retry_at<=?",(name,now)).fetchone()
    if robots:return robots
    if host['robots'] is None:return None
    remaining=cfg['target_per_source']-q.execute('SELECT COUNT(*) FROM articles WHERE source=?',(name,)).fetchone()[0]
    if remaining<=0:return None
    count=q.execute("SELECT COUNT(*) FROM jobs WHERE source=? AND kind='article' AND status='pending' AND retry_at<=?",(name,now)).fetchone()[0]
    if count<min(20,remaining) and host['listing_requests']<cfg['max_listing_requests_per_source']:
        job=q.execute("SELECT * FROM jobs WHERE source=? AND kind IN ('listing','sitemap') AND status='pending' AND retry_at<=? ORDER BY rowid LIMIT 1",(name,now)).fetchone()
        if job:return job
    if host['article_attempts']<cfg['max_article_attempts_per_source']:
        job=q.execute("SELECT * FROM jobs WHERE source=? AND kind='article' AND status='pending' AND retry_at<=? ORDER BY rowid LIMIT 1",(name,now)).fetchone()
        if job:return job
    if host['listing_requests']<cfg['max_listing_requests_per_source']:
        return q.execute("SELECT * FROM jobs WHERE source=? AND kind IN ('listing','sitemap') AND status='pending' AND retry_at<=? ORDER BY rowid LIMIT 1",(name,now)).fetchone()
    return None


def has_work(q,host,cfg):
    """Include delayed retries, but exclude jobs beyond exhausted request budgets."""
    kinds=['robots'] if host['robots'] is None else []
    if host['robots'] is not None:
        if host['article_attempts']<cfg['max_article_attempts_per_source']:kinds.append('article')
        if host['listing_requests']<cfg['max_listing_requests_per_source']:kinds.extend(('listing','sitemap'))
    return any(q.execute("SELECT 1 FROM jobs WHERE source=? AND kind=? AND status='pending' LIMIT 1",
                         (host['name'],kind)).fetchone() for kind in kinds)


def finish(q,job,state,error=None):
    q.execute('UPDATE jobs SET status=?,error=? WHERE source=? AND kind=? AND url=?',
              (state,error,job['source'],job['kind'],job['url']))


def discover_links(q,name,html,url):
    soup=BeautifulSoup(html,'html.parser')
    for a in soup.select('a[href]'):
        link=urljoin(url,a['href']).split('#',1)[0]
        if article_url(link,CANDIDATES[name]):enqueue(q,name,'article',link)
        elif listing_url(name,link):enqueue(q,name,'listing',link)


def save_result(q,name,url,html,folder):
    article=extract(html,CANDIDATES[name]);text=article.pop('clean_text');raw=article.pop('raw_text')
    if any(flag in article['flags'] for flag in ('possible_paywall_or_teaser','unicode_replacement')):
        raise ValueError('Quality flags: '+','.join(article['flags']))
    sha=hashlib.sha256(re.sub(r'\s+',' ',text).strip().encode('utf-8')).hexdigest()
    if q.execute('SELECT 1 FROM articles WHERE source=? AND (sha256=? OR url=?)',(name,sha,url)).fetchone():return False
    stem=sha[:24];(folder/f'{stem}.html').write_text(html,encoding='utf-8')
    (folder/f'{stem}.txt').write_text(text,encoding='utf-8')
    (folder/f'{stem}.source.txt').write_text(raw,encoding='utf-8')
    review=f'''<!doctype html><meta charset="utf-8"><title>{escape(article['title'])}</title>
    <style>body{{font:16px system-ui;margin:24px}}.columns{{display:grid;grid-template-columns:1fr 1fr;gap:24px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;min-width:0;font:15px system-ui}}</style>
    <h1>{escape(article['title'])}</h1><a href="{escape(url,quote=True)}">Оригинал</a>
    <p>Слева исходный контейнер, справа очищенный текст. Исходные скрипты и изображения не исполняются.</p>
    <div class="columns"><pre>{escape(raw)}</pre><pre>{escape(text)}</pre></div>'''
    (folder/f'{stem}.review.html').write_text(review,encoding='utf-8')
    q.execute('INSERT INTO articles VALUES(?,?,?,?,?,?,?,?,?)',
              (name,url,article['title'],sha,len(text.encode('utf-8')),len(text.split()),json.dumps(article['flags']),stem,time.time()))
    return True


def process_job(q,sessions,host,job,cfg):
    name=host['name'];definition=CANDIDATES[name];folder=STATE/name;folder.mkdir(exist_ok=True)
    url=job['request_url'];now=time.time()
    if not same_host(url,definition) or (job['kind']!='robots' and not robots_allowed(host['robots'],url,USER_AGENT)):
        finish(q,job,'robots_denied');q.commit();return
    q.execute('UPDATE hosts SET next_at=?,first_at=COALESCE(first_at,?) WHERE name=?',(now+host['delay'],now,name))
    q.execute('UPDATE jobs SET attempts=attempts+1 WHERE source=? AND kind=? AND url=?',(name,job['kind'],job['url']))
    counter='article_attempts' if job['kind']=='article' else 'listing_requests'
    if job['kind']!='robots':q.execute(f'UPDATE hosts SET {counter}={counter}+1 WHERE name=?',(name,))
    q.commit();started=time.perf_counter()
    try:
        response=sessions[name].get(url,timeout=30,allow_redirects=False)
    except requests.RequestException as error:
        response=None;network_error=error
    else:network_error=None
    elapsed=time.perf_counter()-started
    q.execute('UPDATE hosts SET requests=requests+1,http_seconds=http_seconds+?,service_seconds=service_seconds+? WHERE name=?',
              (elapsed,max(elapsed,host['delay']),name))
    code=response.status_code if response is not None else 0
    if network_error or code in (403,408,425,429) or code>=500:
        errors=host['errors']+1
        delay=max(host['delay'],min(30,cfg['base_delay_seconds']*2**min(errors,7)))
        wait=max(delay,30*2**min(job['attempts'],4))
        if response is not None:wait=retry_after_seconds(response,wait)
        if errors>=5:wait=max(wait,300)
        retry=job['attempts']<3
        message=str(network_error) if network_error else f'HTTP {code}'
        finish(q,job,'pending' if retry else 'failed',message)
        q.execute('UPDATE jobs SET retry_at=? WHERE source=? AND kind=? AND url=?',(time.time()+wait,name,job['kind'],job['url']))
        q.execute('UPDATE hosts SET delay=?,next_at=?,errors=? WHERE name=?',(delay,time.time()+wait,errors,name))
        if job['kind']=='robots' and not retry:q.execute("UPDATE hosts SET state='blocked' WHERE name=?",(name,))
        if code==403 and errors>=3:q.execute("UPDATE hosts SET state='blocked' WHERE name=?",(name,))
        q.commit();print(f'RETRY {name} {message}; pause {wait:.0f}s',flush=True);return
    try:
        if code in (301,302,303,307,308):
            destination=urljoin(url,response.headers['Location'])
            if not same_host(destination,definition) or job['redirects']>=5:raise ValueError('Unsafe/excessive redirect')
            q.execute('UPDATE jobs SET request_url=?,redirects=redirects+1 WHERE source=? AND kind=? AND url=?',
                      (destination,name,job['kind'],job['url']));q.commit();return
        if job['kind']=='robots':
            if code in (404,410):text=''
            else:response.raise_for_status();text=response.text
            parser=RobotFileParser();parser.parse(text.splitlines())
            delay=max(cfg['base_delay_seconds'],float(parser.crawl_delay(USER_AGENT) or 0))
            rate=parser.request_rate(USER_AGENT)
            if rate and rate.requests:delay=max(delay,rate.seconds/rate.requests)
            q.execute('UPDATE hosts SET robots=?,delay=?,next_at=? WHERE name=?',(text,delay,time.time()+delay,name))
            (folder/'robots.txt').write_text(text,encoding='utf-8');finish(q,job,'done')
            print(f'ROBOTS {name}: interval {delay:g}s',flush=True)
        else:
            response.raise_for_status()
            if job['kind']=='sitemap':
                kind,locations=sitemap_entries(response.content)
                random.Random(f'{cfg["seed"]}:{url}').shuffle(locations)
                for loc in locations:
                    if kind=='sitemapindex':
                        if re.search(definition['maps'],urlsplit(loc).path):enqueue(q,name,'sitemap',loc)
                    elif article_url(loc,definition):enqueue(q,name,'article',loc)
                finish(q,job,'done')
            else:
                if 'html' not in response.headers.get('Content-Type','').lower():raise ValueError('Not HTML')
                if response.encoding is None or response.encoding.lower() in ('iso-8859-1','ascii'):
                    response.encoding=response.apparent_encoding
                html=response.text;discover_links(q,name,html,url)
                if job['kind']=='listing':finish(q,job,'done')
                else:
                    saved=save_result(q,name,url,html,folder)
                    finish(q,job,'saved' if saved else 'duplicate')
                    count=q.execute('SELECT COUNT(*) FROM articles WHERE source=?',(name,)).fetchone()[0]
                    print(f'{name} {"saved" if saved else "duplicate"}: {count}/{cfg["target_per_source"]} {url}',flush=True)
        q.execute('UPDATE hosts SET errors=0 WHERE name=?',(name,))
    except (ValueError,KeyError,XMLSyntaxError,requests.RequestException) as error:
        finish(q,job,'rejected',str(error));print(f'SKIP {name}: {error} {url}',flush=True)
        if job['kind']=='robots':q.execute("UPDATE hosts SET state='blocked' WHERE name=?",(name,))
    q.commit()


def write_report(q,cfg,state):
    sources=[];sections=[];now=time.time()
    for name in cfg['order']:
        host=dict(q.execute('SELECT * FROM hosts WHERE name=?',(name,)).fetchone())
        rows=[dict(r) for r in q.execute('SELECT * FROM articles WHERE source=? ORDER BY saved_at',(name,))]
        sizes=[r['text_bytes'] for r in rows];total=sum(sizes)
        report=dict(source=name,label=CANDIDATES[name]['label'],state=host['state'],saved=len(rows),target=cfg['target_per_source'],
                    text_bytes=total,mean_bytes=round(statistics.mean(sizes)) if sizes else 0,
                    median_bytes=round(statistics.median(sizes)) if sizes else 0,
                    mean_words=round(statistics.mean(r['words'] for r in rows)) if rows else 0,
                    http_requests=host['requests'],http_seconds=round(host['http_seconds'],2),
                    estimated_dedicated_mb_per_hour=round(total/host['service_seconds']*3600/1e6,2) if host['service_seconds'] else 0,
                    delay_seconds=host['delay'],next_request_at=host['next_at'],article_attempts=host['article_attempts'],
                    queue=[dict(r) for r in q.execute('SELECT kind,status,COUNT(*) AS count FROM jobs WHERE source=? GROUP BY kind,status',(name,))])
        sources.append(report)
        links=''.join(f'<li><a href="{name}/{r["stem"]}.review.html">{escape(r["title"])}</a> — {r["text_bytes"]/1000:.1f} КБ</li>' for r in rows)
        sections.append(f'<h2>{escape(report["label"])}: {len(rows)}/{cfg["target_per_source"]}</h2><p>{total/1e6:.3f} МБ; среднее {report["mean_bytes"]/1000:.1f} КБ; медиана {report["median_bytes"]/1000:.1f} КБ; интервал {host["delay"]:g} с.</p><ul>{links}</ul>')
    global_bytes=q.execute('SELECT COALESCE(SUM(b),0) FROM (SELECT MAX(text_bytes) AS b FROM articles GROUP BY sha256)').fetchone()[0]
    report=dict(state=state,updated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),pid=__import__('os').getpid(),
                target_per_source=cfg['target_per_source'],total_saved=sum(s['saved'] for s in sources),
                total_text_bytes=sum(s['text_bytes'] for s in sources),unique_pilot_text_bytes=global_bytes,
                compared_with_main_corpus=False,sources=sources)
    temporary=STATE/'status.tmp';temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(STATE/'status.json')
    index='''<!doctype html><html lang="ru"><meta charset="utf-8"><title>Пилот шести источников</title><style>body{font:16px system-ui;max-width:1100px;margin:30px auto;padding:12px}li{margin:7px}</style><h1>Пилот дополнительных источников</h1><p>100 уникальных статей на источник. Сохранённые HTML и TXT находятся отдельно от основной базы. Сравнение с дублями основной базы ещё не выполнено. Кодовые листинги, иллюстрации не входят в текст; проверяйте полноту по оригиналу. Данные меняются по мере сбора.</p>'''
    (STATE/'index.html').write_text(index+''.join(sections)+'</html>',encoding='utf-8')
    lines=['# Пилот дополнительных источников','',f'Состояние: {state}. Обновлено: {report["updated_at"]}.','',
           '| Источник | Статей | Текст, МБ | Среднее, КБ | Медиана, КБ | Оценка МБ/ч при отдельном сборе |','|---|---:|---:|---:|---:|---:|']
    for s in sources:lines.append(f'| {s["label"]} | {s["saved"]}/{s["target"]} | {s["text_bytes"]/1e6:.3f} | {s["mean_bytes"]/1000:.1f} | {s["median_bytes"]/1000:.1f} | {s["estimated_dedicated_mb_per_hour"]} |')
    lines+=['','Оценка скорости = байты текста / сумма max(время HTTP-запроса, интервал источника). Это ориентир выделенной последовательной загрузки, не фактическая скорость совместного пилота и не гарантия. Сравнение с основной БД не выполнялось. Выборка из сохранённых списков перемешана с фиксированным seed; Хабр и Элементы дополняются через тематические списки и ссылки статей. Нельзя экстраполировать её на весь сайт без проверки.']
    (STATE/'summary.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('PROGRESS '+', '.join(f'{s["source"]}={s["saved"]}/{s["target"]}' for s in sources),flush=True)
    return report


def run(cfg):
    q=open_queue(STATE/'queue.sqlite');seed_jobs(q,cfg)
    sessions={name:requests.Session() for name in cfg['order']}
    for session in sessions.values():session.trust_env=False;session.headers['User-Agent']=USER_AGENT
    last_report=0;state='running'
    try:
        while True:
            if (STATE/'STOP').exists():state='stopped_by_file';break
            if shutil.disk_usage(STATE).free<cfg['min_free_disk_bytes']:state='low_disk_space';break
            worked=False
            for name in cfg['order']:
                if (STATE/'STOP').exists():break
                host=q.execute('SELECT * FROM hosts WHERE name=?',(name,)).fetchone()
                count=q.execute('SELECT COUNT(*) FROM articles WHERE source=?',(name,)).fetchone()[0]
                if count>=cfg['target_per_source']:
                    q.execute("UPDATE hosts SET state='complete',finished_at=COALESCE(finished_at,?) WHERE name=?",(time.time(),name));q.commit();continue
                job=choose_job(q,host,cfg,time.time())
                if job:
                    process_job(q,sessions,host,job,cfg);worked=True
                elif host['state']=='active' and host['next_at']<=time.time():
                    if not has_work(q,host,cfg):
                        q.execute("UPDATE hosts SET state='exhausted',finished_at=? WHERE name=?",(time.time(),name));q.commit()
            if time.monotonic()-last_report>=10:
                write_report(q,cfg,'running');last_report=time.monotonic()
            active=q.execute("SELECT COUNT(*) FROM hosts WHERE state='active'").fetchone()[0]
            if not active:
                completed=q.execute("SELECT COUNT(*) FROM hosts WHERE state='complete'").fetchone()[0]
                state='complete' if completed==len(cfg['order']) else 'finished_incomplete';break
            if not worked:time.sleep(1)
    except KeyboardInterrupt:state='interrupted'
    except Exception:
        state='failed';raise
    finally:
        write_report(q,cfg,state)
        for session in sessions.values():session.close()
        q.close()


def main():
    sys.stdout.reconfigure(encoding='utf-8');sys.stderr.reconfigure(encoding='utf-8')
    cfg={'target_per_source': 100, 'order': ['3dnews', 'ixbt', 'scfh', 'nkj', 'habr', 'elementy'], 'base_delay_seconds': 0.25, 'max_article_attempts_per_source': 500, 'max_listing_requests_per_source': 40, 'min_free_disk_bytes': 4000000000, 'seed': 20261108, 'seed_archive': 'data/source_candidates/trial-20261107T112642.787460Z'}
    if set(cfg['order'])!=set(CANDIDATES) or len(cfg['order'])!=len(CANDIDATES) or cfg['target_per_source']<1:
        raise ValueError('Invalid pilot configuration')
    STATE.mkdir(parents=True,exist_ok=True)
    # Hold the main collector lock throughout the pilot to prevent accidental overlap.
    (MAIN_STATE/'STOP').touch()
    for _ in range(90):
        try:
            lock=process_lock();lock.__enter__();break
        except (RuntimeError,OSError):time.sleep(1)
    else:raise RuntimeError('Main collector did not stop')
    try:run(cfg)
    finally:lock.__exit__(None,None,None)


if __name__=='__main__':main()
