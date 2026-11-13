# Пояснение к файлу: Параллельный сбор с независимыми задержками по источникам.
# Как читать: Подготовка очереди → задания источников → сетевые задачи → применение результатов в БД. Ошибки и backoff учитываются по источникам.
# Команды запуска и структура проекта: README.md.

"""Parallel hosts, serialized queue/DB writes, and shared exact-content deduplication."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from collections import deque
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import signal
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
from lxml import etree

from tools.probe_candidates import CANDIDATES, article_url, extract
from tools.probe_sources import (Client, SOURCES, USER_AGENT, extract_article,
                                normalize_url, same_host, sitemap_entries)
from tools.corpus_catalog import canonical, configure, extract_publication


@contextmanager
def defer_cleanup_interrupts(on_interrupt):
    """Ctrl+C во время завершения запрашивает остановку, но не обрывает запись состояния."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, lambda signum, frame: on_interrupt())
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)

MAPS={'nkj':'https://www.nkj.ru/sitemap.xml','scfh':'https://scfh.ru/sitemap.xml',
      '3dnews':'https://3dnews.ru/export/sitemaps/sitemap.xml',
      'ixbt':'https://www.ixbt.com/export/sitemapindex.xml'}
DEFINITIONS={**SOURCES,**{name:dict(item,article_pattern=item['article'],map_pattern=item['maps'],
                                sitemap=MAPS.get(name)) for name,item in CANDIDATES.items()}}


def article_key(name,url):
    return canonical(name,url,DEFINITIONS[name])


def is_listing(name,url):
    if not same_host(url,DEFINITIONS[name]):return False
    parts=urlsplit(url)
    if name=='elementy':
        return parts.path.rstrip('/')=='/novosti_nauki' and set(parse_qs(parts.query))<={'page'}
    if name=='habr':
        return not parts.query and bool(re.fullmatch(r'/ru/(?:articles|hubs/[a-z0-9_-]+/articles)/(?:page\d+/)?',parts.path))
    if name=='securitylab' and parts.path in ('/analytics/','/analytics/list.php'):
        return all(re.fullmatch(r'PAGEN_\d+|page',key) and all(value.isdigit() for value in values)
                   for key,values in parse_qs(parts.query).items())
    return False


def enqueue_url(q,core,name,kind,url):
    if not same_host(url,DEFINITIONS[name]):return None
    key=article_key(name,url) if kind=='article' else url.split('#',1)[0]
    if key is None or (kind=='listing' and not is_listing(name,key)):return None
    core.enqueue(q,name,kind,key,request_url=key if name=='3dnews' and kind=='article' else url)
    return key


def publication_links(name,html,url):
    """Parse in the fetching worker; keep queue writes on the main thread."""
    tree=etree.HTML(html)
    if tree is None:return []
    found=set();links=[]
    for href in tree.xpath('//a[@href]/@href'):
        link=urljoin(url,href).split('#',1)[0]
        kind='article' if article_key(name,link) else 'listing' if is_listing(name,link) else None
        if kind and (kind,link) not in found:
            found.add((kind,link));links.append((kind,link))
    return links


def discover_links(q,core,name,html,url):
    for kind,link in publication_links(name,html,url):enqueue_url(q,core,name,kind,link)


def prepare_runtime(q):
    q.execute('''CREATE TABLE IF NOT EXISTS parallel_runtime (
        source TEXT PRIMARY KEY, effective_delay REAL NOT NULL, next_at REAL NOT NULL DEFAULT 0)''')
    q.execute('CREATE TABLE IF NOT EXISTS collector_meta (key TEXT PRIMARY KEY,value TEXT)')
    q.execute('''CREATE TABLE IF NOT EXISTS discovery_schedule (
        source TEXT PRIMARY KEY,articles_since_discovery INTEGER NOT NULL DEFAULT 0)''')
    q.commit()


def seed_sources(q,cfg,core):
    refresh=not q.execute("SELECT 1 FROM collector_meta WHERE key='parallel_maps_v1'").fetchone()
    for name in cfg['source_order']:
        if name=='wikipedia':continue
        definition=DEFINITIONS[name]
        if not q.execute('SELECT 1 FROM jobs WHERE source=? LIMIT 1',(name,)).fetchone():
            if name in CANDIDATES:
                cached=[core.ROOT/cfg['candidate_seed_archive']/name/'urls.txt']
            else:cached=sorted((core.ROOT/'data/source_probes').glob(f'*/{name}/urls.txt'))
            if cached and cached[-1].exists():
                for url in cached[-1].read_text(encoding='utf-8').splitlines():enqueue_url(q,core,name,'article',url)
        if definition.get('sitemap'):enqueue_url(q,core,name,'sitemap',definition['sitemap'])
        if refresh and name in SOURCES:
            q.execute("UPDATE jobs SET status='pending',attempts=0,retry_at=0,error=NULL WHERE source=? AND kind='sitemap' AND status='done'",(name,))
        if name=='elementy':enqueue_url(q,core,name,'listing','https://elementy.ru/novosti_nauki')
        if name=='habr':
            for hub in ('popular_science','physics','biology','astronomy','engineering_systems','artificial_intelligence'):
                enqueue_url(q,core,name,'listing',f'https://habr.com/ru/hubs/{hub}/articles/')
        if name=='securitylab':
            enqueue_url(q,core,name,'listing','https://www.securitylab.ru/analytics/')
    for title in cfg['wiki_categories']:core.enqueue(q,'wikipedia','category','Категория:'+title,depth=0)
    q.execute("INSERT OR IGNORE INTO collector_meta VALUES('parallel_maps_v1',?)",(str(time.time()),))
    q.commit()


def pilot_directory(cfg,core):
    folder=(core.ROOT/cfg['pilot_import_directory']).resolve()
    if not folder.is_relative_to((core.ROOT/'data/source_candidates').resolve()):
        raise ValueError('Pilot folder must be inside data/source_candidates')
    return folder


def seed_pilot(q,cfg,core):
    folder=pilot_directory(cfg,core)
    if not (folder/'queue.sqlite').exists():return
    if q.execute("SELECT 1 FROM collector_meta WHERE key='pilot_seed_v1'").fetchone():return
    pilot=sqlite3.connect('file:'+str(folder/'queue.sqlite')+'?mode=ro',uri=True);pilot.row_factory=sqlite3.Row
    try:
        for row in pilot.execute("SELECT * FROM jobs WHERE kind!='robots'"):
            if row['source'] not in cfg['source_order']:continue
            key=enqueue_url(q,core,row['source'],row['kind'],row['request_url'])
            if key and row['status'] in ('rejected','robots_denied','done'):
                q.execute("UPDATE jobs SET status=?,error=? WHERE source=? AND kind=? AND key=? AND status='pending'",
                          (row['status'],row['error'],row['source'],row['kind'],key))
        for row in pilot.execute("SELECT name FROM hosts WHERE state='blocked'"):
            name=row['name']
            if name not in cfg['source_order']:continue
            core.note_response(q,name,True,cfg)
            q.execute('UPDATE source_health SET cooldown_until=MAX(cooldown_until,?) WHERE source=?',
                      (time.time()+cfg.get('source_403_cooldown_seconds',3600),name))
            print(f'PAUSE {name}: prior pilot access errors; queue retained, retry in one hour',flush=True)
        q.execute("INSERT INTO collector_meta VALUES('pilot_seed_v1',?)",(str(time.time()),));q.commit()
    finally:pilot.close()


def import_pilot(q,conn,cfg,core,max_new=0):
    folder=pilot_directory(cfg,core)
    if not (folder/'queue.sqlite').exists():return 0
    pilot=sqlite3.connect('file:'+str(folder/'queue.sqlite')+'?mode=ro',uri=True);pilot.row_factory=sqlite3.Row
    imported=0
    try:
        for row in pilot.execute('SELECT * FROM articles ORDER BY saved_at'):
            if max_new and imported>=max_new:break
            name=row['source']
            if name not in cfg['source_order']:continue
            if (core.STATE/'STOP').exists() or cfg['_document_count']>=cfg['max_documents']:break
            if cfg.get('stop_at_target',True) and cfg['_unique_text_bytes']>=cfg['target_bytes']:break
            key=enqueue_url(q,core,name,'article',row['url'])
            job=q.execute("SELECT * FROM jobs WHERE source=? AND kind='article' AND key=?",(name,key)).fetchone()
            if job['status']!='pending':continue
            stem=row['stem']
            if not re.fullmatch(r'[0-9a-f]{24}',stem):raise ValueError('Invalid cached article filename')
            text=(folder/name/(stem+'.txt')).read_text(encoding='utf-8')
            if core.digest_text(text)!=row['sha256'] or len(text.encode('utf-8'))!=row['text_bytes']:
                raise ValueError('Cached article content/hash mismatch')
            if not core.accept_source_topic(name,row['title'],text,cfg):outcome='off_topic'
            else:
                html=(folder/name/(stem+'.html')).read_text(encoding='utf-8')
                outcome=core.save_article(conn,key,html,row['title'],text,name,
                         dict(pilot_import=True,original_url=row['url'],quality_flags=json.loads(row['flags'])),
                         core.minimum_text_bytes(name,cfg))
            core.finish(q,job,outcome);q.commit()
            if outcome=='saved':
                imported+=1;cfg['_document_count']+=1;cfg['_unique_text_bytes']+=row['text_bytes']
            print(f'{name} {outcome} (pilot cache): {key}',flush=True)
    finally:pilot.close()
    print(f'PILOT_IMPORT: {imported} new unique documents added to main corpus',flush=True)
    return imported


class MeasuredSession(requests.Session):
    def __init__(self):
        super().__init__();self.trust_env=False;self.headers['User-Agent']=USER_AGENT
        self.requests_count=0;self.http_seconds=0.;self.last_start_epoch=0.

    def request(self,*args,**kwargs):
        start=time.perf_counter();self.last_start_epoch=time.time()
        try:return super().request(*args,**kwargs)
        finally:self.requests_count+=1;self.http_seconds+=time.perf_counter()-start


class HostClient(Client):
    def __init__(self,definition,delay):
        super().__init__(definition,delay);self.session.close();self.session=MeasuredSession();self.minimum_delay=delay

    def get(self,url,**kwargs):
        response=super().get(url,**kwargs)
        if 'html' in response.headers.get('Content-Type','').lower() and 'auth-controller' in response.text[:4000].lower():
            error=requests.RequestException('Access challenge returned instead of publication')
            error.retry_after=3600;raise error
        return response

    def load_robots(self,directory):
        try:text=self.get(self.source['root']+'/robots.txt',robots_request=True).text
        except requests.HTTPError as error:
            if error.response.status_code not in (404,410):raise
            text=''
        (directory/'robots.txt').write_text(text,encoding='utf-8')
        self.robots_text=text;self.robots=RobotFileParser();self.robots.parse(text.splitlines())
        self.delay=max(self.delay,float(self.robots.crawl_delay(USER_AGENT) or 0))
        rate=self.robots.request_rate(USER_AGENT)
        if rate and rate.requests:self.delay=max(self.delay,rate.seconds/rate.requests)
        self.minimum_delay=max(self.minimum_delay,float(self.robots.crawl_delay(USER_AGENT) or 0),rate.seconds/rate.requests if rate and rate.requests else 0)


def select_job(q,name,cfg,core):
    if name=='wikipedia':
        for _,kind in core.scheduled_jobs(q,name,cfg):
            job=core.next_job(q,name,kind)
            if job is not None:return job
        return None
    row=q.execute('SELECT articles_since_discovery FROM discovery_schedule WHERE source=?',(name,)).fetchone()
    count=row[0] if row else 0
    kinds=('sitemap','listing','article') if count>=cfg.get('discovery_batch_articles',40) else ('article','sitemap','listing')
    for kind in kinds:
        job=core.next_job(q,name,kind)
        if job is not None:
            q.execute('''INSERT INTO discovery_schedule VALUES(?,?) ON CONFLICT(source) DO UPDATE SET
                articles_since_discovery=excluded.articles_since_discovery''',(name,count+1 if kind=='article' else 0))
            return job
    return None


# Сетевая часть задания выполняется отдельно от применения результата; для источника сохраняются его задержки и правила.
def fetch_job(name,job,client,cfg,core):
    """Worker performs network/HTML work only; SQLite and PostgreSQL stay on the caller thread."""
    kind=job['kind']
    if kind=='robots':
        directory=core.STATE/name;directory.mkdir(exist_ok=True)
        client.load_robots(directory)
        return dict(type='robots',sitemaps=re.findall(r'^Sitemap:\s*(\S+)',getattr(client,'robots_text',''),re.I|re.M))
    if name=='wikipedia':
        if kind=='category':
            payload=json.loads(job['payload'])
            params=dict(action='query',list='categorymembers',cmtitle=job['key'],cmtype='page|subcat',cmnamespace='0|14',cmlimit=500)
            if payload.get('continuation'):params['cmcontinue']=payload['continuation']
            return dict(type='category',data=client.query(**params))
        parsed=client.query(action='parse',pageid=job['key'],prop='text|revid|properties',redirects=1,disableeditsection=1)['parse']
        if re.match(r'^(Категория|Шаблон|Википедия|Портал|Обсуждение|Файл|Участник):',parsed['title'],re.I):
            raise ValueError('Not an encyclopedia article')
        if 'disambiguation' in parsed.get('properties',{}):raise ValueError('Disambiguation page')
        html=parsed['text'];title=parsed['title'];text=core.wiki_text(html)
        url='https://ru.wikipedia.org/wiki/'+quote(title.replace(' ','_'),safe='()_')
        meta=dict(page_id=parsed['pageid'],revision_id=parsed['revid'],
                  revision_url=f'https://ru.wikipedia.org/w/index.php?oldid={parsed["revid"]}',
                  history_url=f'https://ru.wikipedia.org/w/index.php?curid={parsed["pageid"]}&action=history',
                  category=json.loads(job['payload'])['category'],
                  license='CC BY-SA; see article footer and history',author='Wikipedia contributors')
    else:
        url=json.loads(job['payload']).get('request_url',job['key'])
        response=client.get(url)
        if kind=='sitemap':
            map_kind,locations=sitemap_entries(response.content)
            return dict(type='sitemap',map_kind=map_kind,locations=locations)
        if 'html' not in response.headers.get('Content-Type','').lower():raise ValueError('Not HTML')
        if response.encoding is None or response.encoding.lower() in ('iso-8859-1','ascii'):
            response.encoding=response.apparent_encoding
        html=response.text;url=response.url
        links=publication_links(name,html,url) if name not in SOURCES else []
        if kind=='listing':return dict(type='listing',html=html,url=url,links=links)
        if name not in SOURCES:
            article=extract_publication(name,html,url,DEFINITIONS[name],cfg.get('publication_min_characters',300))
            if any(flag in article['flags'] for flag in ('possible_paywall_or_teaser','unicode_replacement')):
                raise ValueError('Quality flags: '+','.join(article['flags']))
            meta=dict(quality_flags=article['flags'],original_url=url,author=article.get('author',''),published=article.get('published'))
        else:
            article=extract_article(html,SOURCES[name],min_characters=cfg.get('source_min_text_characters',{}).get(name,300))
            meta={k:v for k,v in article.items() if k not in ('title','clean_text')}
        title,text=article['title'],article['clean_text']
        url=article_key(name,url) or job['key']
    return dict(type='article',url=url,html=html,title=title,text=text,meta=meta,
                links=links if name!='wikipedia' else [])


# Результат сетевой задачи переносится в очередь и PostgreSQL; только успешно сохранённая статья увеличивает фактический корпус.
def apply_result(q,conn,name,job,result,cfg,core):
    kind=result['type']
    if kind=='robots':
        for url in result.get('sitemaps',[]):
            if re.search(DEFINITIONS[name].get('map_pattern') or r'\.xml(?:\.gz)?$',urlsplit(url).path):
                enqueue_url(q,core,name,'sitemap',url)
        q.commit();return 'robots'
    if kind=='category':
        core.category_step(q,SimpleNamespace(query=lambda **_:result['data']),job,cfg)
        return 'category'
    if kind=='sitemap':
        definition=DEFINITIONS[name]
        for url in result['locations']:
            if result['map_kind']=='sitemapindex':
                if definition.get('map_pattern') and re.search(definition['map_pattern'],urlsplit(url).path):
                    enqueue_url(q,core,name,'sitemap',url)
            else:enqueue_url(q,core,name,'article',url)
        core.finish(q,job,'done');q.commit();return 'sitemap'
    if name not in SOURCES and name!='wikipedia':
        for link_kind,link in result.get('links',[]):enqueue_url(q,core,name,link_kind,link)
    if kind=='listing':core.finish(q,job,'done');q.commit();return 'listing'
    if not core.accept_source_topic(name,result['title'],result['text'],cfg):outcome='off_topic'
    else:
        outcome=core.save_article(conn,result['url'],result['html'],result['title'],result['text'],name,
                                  result['meta'],core.minimum_text_bytes(name,cfg))
    core.finish(q,job,outcome);q.commit()
    if outcome=='saved':cfg['_document_count']+=1;cfg['_unique_text_bytes']+=len(result['text'].encode('utf-8'))
    print(f'{name} {outcome}: {result["url"]}',flush=True)
    return outcome


def handle_error(q,name,job,error,cfg,core,client):
    response=getattr(error,'response',None);code=response.status_code if response is not None else None
    server_wait=core.retry_after_seconds(response,0) if response is not None else 0
    error.retry_after=max(server_wait,getattr(error,'retry_after',0))
    if job['kind']=='robots':
        if not core.is_dns_error(error):core.note_response(q,name,True,cfg)
        error.retry_after=max(30,error.retry_after)
        print(f'RETRY {name} robots.txt: {error}',flush=True)
    else:core.log_error(q,job,error,cfg)
    if code==403:
        error.retry_after=max(error.retry_after,cfg.get('source_403_cooldown_seconds',3600))
        core.note_response(q,name,True,cfg)
        if job['kind']!='robots' and job['attempts']+1<4:
            q.execute("UPDATE jobs SET status='pending',retry_at=? WHERE source=? AND kind=? AND key=?",
                      (time.time()+error.retry_after,name,job['kind'],job['key']))
    if code==429 or code==503:
        client.delay=max(client.delay,min(30,max(.5,client.delay*2)))
        error.retry_after=max(error.retry_after,30)
    if error.retry_after:
        if not q.execute('SELECT 1 FROM source_health WHERE source=?',(name,)).fetchone():
            core.note_response(q,name,True,cfg)
        q.execute('UPDATE source_health SET cooldown_until=MAX(cooldown_until,?) WHERE source=?',
                  (time.time()+error.retry_after,name))
        print(f'PAUSE {name}: {error.retry_after:g}s; other hosts continue',flush=True)
    q.commit()


def limit_state(cfg,args,saved):
    if cfg['_document_count']>=cfg['max_documents']:return 'document_limit_reached'
    if cfg.get('stop_at_target',True) and cfg['_unique_text_bytes']>=cfg['target_bytes']:return 'target_reached'
    if args.max_new and saved>=args.max_new:return 'batch_complete'
    return None


def recover_rate(client,entry,cfg):
    entry['success_streak']+=1
    if entry['success_streak']>=cfg.get('rate_recovery_successes',30):
        client.delay=max(client.minimum_delay,client.delay*.8);entry['success_streak']=0


def rolling_rate(history,now,text_bytes):
    history.append((now,text_bytes))
    while len(history)>1 and history[1][0]<=now-300:history.popleft()
    return round((text_bytes-history[0][1])/1e6/max(1,now-history[0][0])*3600,2)


# Планировщик распределяет работу по источникам, соблюдает лимиты и обрабатывает остановку/ошибки без потери очереди.
def run_parallel(args,cfg,core):
    names=cfg['source_order']
    if (len(names)!=len(set(names)) or set(names)-set(core.NAMES) or not names
            or cfg['max_documents']<1 or any(core.source_delay(cfg,n)<0 for n in names)
            or cfg.get('parallel_workers',len(names))<1):raise ValueError('Invalid parallel collection settings')
    if cfg.get('wide_archives'):configure(DEFINITIONS)
    hosts=[urlsplit(core.API if n=='wikipedia' else DEFINITIONS[n]['root']).hostname for n in names]
    if len(hosts)!=len(set(hosts)):raise ValueError('Configure only one source worker per host')
    conn=core.connect(core.load_config(str(core.ROOT/'config.yaml')));q=core.queue_open()
    clients={};runtime={};busy={};state='running';saved=0;started=time.monotonic();pool=None
    try:
        prepare_runtime(q);core.init_hashes(conn)
        cfg['_unique_text_bytes']=core.status(conn,q,cfg,'initializing')
        if args.status:state='status_only';return
        if limit_state(cfg,args,0):state=limit_state(cfg,args,0);return
        if (core.STATE/'STOP').exists():state='stopped_by_file';return
        if not core.has_storage_space(cfg):state='low_disk_space';return
        seed_sources(q,cfg,core);seed_pilot(q,cfg,core)
        if cfg.get('wide_archives'):
            from tools.seed_wide_archives import seed_wide, seed_original_existing
            core.status(conn,q,cfg,'preparing_queues')
            seed_wide(q,conn,cfg,core,DEFINITIONS)
            seed_original_existing(q,conn,cfg,core,DEFINITIONS)
            from tools.optimize_collection import migrate_queue
            migrate_queue(q,cfg,core,DEFINITIONS)
        saved=import_pilot(q,conn,cfg,core,args.max_new)
        for name in names:
            row=q.execute('SELECT * FROM parallel_runtime WHERE source=?',(name,)).fetchone()
            delay=max(core.source_delay(cfg,name),row['effective_delay'] if row else 0)
            if name=='wikipedia':
                client=core.WikiClient(delay);client.session.close();client.session=MeasuredSession();client.max_attempts=1
                cfg['_wiki_metrics']=client.metrics
            else:client=HostClient(DEFINITIONS[name],delay)
            client.minimum_delay=core.source_delay(cfg,name)
            clients[name]=client
            runtime[name]=dict(ready=name=='wikipedia',next_at=row['next_at'] if row else 0,saved=0,text_bytes=0,errors=0,success_streak=0,last_error='',history=deque([(time.monotonic(),0)]))
        pool=ThreadPoolExecutor(max_workers=min(len(names),cfg.get('parallel_workers',len(names))))
        last_status=0
        while True:
            if (core.STATE/'STOP').exists():state='stopped_by_file';break
            state=limit_state(cfg,args,saved) or 'running'
            if state!='running':break
            if not core.has_storage_space(cfg):state='low_disk_space';break
            # A source has at most one submitted future. All DB mutations below stay serialized.
            done=[name for name,future_job in busy.items() if future_job[0].done()]
            for name in done:
                future,job=busy.pop(name);client=clients[name];entry=runtime[name]
                try:
                    result=future.result()
                except (requests.RequestException,ValueError,KeyError,etree.XMLSyntaxError) as error:
                    entry['errors']+=1;entry['success_streak']=0;entry['last_error']=str(error);handle_error(q,name,job,error,cfg,core,client)
                else:
                    # Already fetched work remains pending when a limit/stop is reached.
                    if limit_state(cfg,args,saved) or (core.STATE/'STOP').exists():continue
                    outcome=apply_result(q,conn,name,job,result,cfg,core)
                    entry['last_error']=''
                    core.note_response(q,name,False,cfg)
                    recover_rate(client,entry,cfg)
                    if outcome=='robots':
                        entry['ready']=True
                        print(f'ROBOTS {name}: effective interval {client.delay:g}s',flush=True)
                    if outcome=='saved':
                        saved+=1;entry['saved']+=1;entry['text_bytes']+=len(result['text'].encode('utf-8'))
                entry['next_at']=max(time.time(),client.session.last_start_epoch+client.delay)
                q.execute('''INSERT INTO parallel_runtime VALUES(?,?,?) ON CONFLICT(source) DO UPDATE SET
                    effective_delay=excluded.effective_delay,next_at=excluded.next_at''',(name,client.delay,entry['next_at']))
                q.commit()
            if limit_state(cfg,args,saved):continue
            if (core.STATE/'STOP').exists():continue
            pending=[]
            for name in names:
                if name in busy:pending.append(name);continue
                if not q.execute("SELECT 1 FROM jobs WHERE source=? AND status='pending' LIMIT 1",(name,)).fetchone():continue
                pending.append(name);entry=runtime[name]
                if entry['next_at']>time.time() or core.source_health(q,name)['cooldown_until']>time.time():continue
                if not entry['ready']:
                    job=dict(source=name,kind='robots',key=DEFINITIONS[name]['root']+'/robots.txt',payload='{}',attempts=0)
                else:job=select_job(q,name,cfg,core)
                if job is not None:
                    entry['next_at']=time.time()+clients[name].delay
                    q.execute('''INSERT INTO parallel_runtime VALUES(?,?,?) ON CONFLICT(source) DO UPDATE SET
                        effective_delay=excluded.effective_delay,next_at=excluded.next_at''',(name,clients[name].delay,entry['next_at']))
                    q.commit()
                    busy[name]=(pool.submit(fetch_job,name,dict(job),clients[name],cfg,core),dict(job))
            cfg['_active_sources']=pending
            if time.monotonic()-last_status>=30:
                elapsed=max(1,time.monotonic()-started)
                cfg['_source_metrics']=[dict(source=name,in_flight=name in busy,robots_ready=runtime[name]['ready'],
                    access_state=('cooldown' if core.source_health(q,name)['cooldown_until']>time.time() else
                                  'exhausted' if name not in pending else 'fetching' if name in busy else 'waiting'),
                    last_error=runtime[name]['last_error'],minimum_delay=clients[name].minimum_delay,
                    effective_delay=clients[name].delay,next_request_at=runtime[name]['next_at'],
                    http_requests=clients[name].session.requests_count,http_seconds=round(clients[name].session.http_seconds,3),
                    saved_this_run=runtime[name]['saved'],text_bytes_this_run=runtime[name]['text_bytes'],
                    observed_mb_per_hour=round(runtime[name]['text_bytes']/1e6/elapsed*3600,2),
                    recent_mb_per_hour=rolling_rate(runtime[name]['history'],time.monotonic(),runtime[name]['text_bytes']),
                    errors_this_run=runtime[name]['errors']) for name in names]
                cfg['_unique_text_bytes']=core.status(conn,q,cfg,'running');last_status=time.monotonic()
            if not pending and not busy:
                failures=q.execute("SELECT COUNT(*) FROM jobs WHERE status='failed'").fetchone()[0]
                state='sources_finished_with_errors' if failures else 'sources_exhausted';break
            if busy:wait([item[0] for item in busy.values()],timeout=1,return_when=FIRST_COMPLETED)
            else:time.sleep(1)
    except KeyboardInterrupt:conn.rollback();state='interrupted'
    except Exception:conn.rollback();state='failed';raise
    finally:
        def request_stop():
            nonlocal state
            if state != 'interrupted':
                print('Остановка: ожидаем завершения текущих запросов и сохраняем очередь...',flush=True)
            state='interrupted'
        # Обработчик действует до закрытия БД, включая ожидание сетевых потоков.
        # Выполняемые HTTP-запросы заканчиваются по своим таймаутам; новые не запускаются.
        with defer_cleanup_interrupts(request_stop):
            if state=='interrupted':
                print('Остановка: ожидаем завершения текущих запросов и сохраняем очередь...',flush=True)
            if pool:
                for future,_ in busy.values():future.cancel()
                pool.shutdown(wait=True,cancel_futures=True)
            for name,client in clients.items():
                q.execute('''INSERT INTO parallel_runtime VALUES(?,?,?) ON CONFLICT(source) DO UPDATE SET
                    effective_delay=excluded.effective_delay,next_at=excluded.next_at''',
                          (name,client.delay,max(runtime[name]['next_at'],client.session.last_start_epoch+client.delay)))
            q.commit()
            try:core.status(conn,q,cfg,state)
            finally:
                for client in clients.values():client.session.close()
                conn.close();q.close()
