# Пояснение к файлу: Добавление начальных URL широких архивов в очередь.
# Как читать: Правила архивов → канонизация/фильтрация адресов → задания. Постановка URL в очередь ещё не загружает статью в корпус.
# Команды запуска и структура проекта: README.md.

"""One-time resumable expansion of scopes using already verified local URL lists."""
import json
import time
from pathlib import Path

from tools.corpus_catalog import canonical
from tools.probe_sources import sitemap_entries


def seed_original_existing(q,conn,cfg,core,definitions):
    if q.execute("SELECT 1 FROM collector_meta WHERE key='original_publications_v1'").fetchone():return
    labels={core.NAMES[name]:name for name in ('techinsider','biomolecula') if name in cfg['source_order']}
    if not labels:return
    with conn.cursor() as cursor:
        cursor.execute('SELECT normalized_url,source FROM documents WHERE source=ANY(%s)',(list(labels),))
        while True:
            rows=cursor.fetchmany(2000)
            if not rows:break
            batch=[]
            for url,label in rows:
                name=labels[label];key=canonical(name,url,definitions[name])
                if key:batch.append((name,'article',key,json.dumps(dict(request_url=key))))
            q.executemany("INSERT OR IGNORE INTO jobs(source,kind,key,payload,status) VALUES(?,?,?,?,'existing')",batch)
            q.commit()
    conn.commit()
    q.execute("UPDATE jobs SET status='pending',attempts=0,retry_at=0,error=NULL WHERE status='rejected' AND error LIKE '%Too little%'")
    for name in labels.values():q.execute("UPDATE jobs SET status='pending',attempts=0,retry_at=0,error=NULL WHERE source=? AND kind='sitemap' AND status='done'",(name,))
    q.execute("INSERT INTO collector_meta VALUES('original_publications_v1',?)",(str(time.time()),));q.commit()


def seed_wide(q,conn,cfg,core,definitions):
    if q.execute("SELECT 1 FROM collector_meta WHERE key='wide_archives_v1'").fetchone():return
    started=time.monotonic();inserted=0

    def add(name,urls):
        nonlocal inserted
        batch=[]
        for url in urls:
            if (core.STATE/'STOP').exists():return False
            key=canonical(name,url,definitions[name])
            if key:
                batch.append((name,'article',key,json.dumps(dict(request_url=url),ensure_ascii=False)))
            if len(batch)>=2000:
                q.executemany('INSERT OR IGNORE INTO jobs(source,kind,key,payload) VALUES(?,?,?,?)',batch)
                inserted+=len(batch);batch=[];q.commit()
        if batch:
            q.executemany('INSERT OR IGNORE INTO jobs(source,kind,key,payload) VALUES(?,?,?,?)',batch)
            inserted+=len(batch);q.commit()
        print(f'SEED {name}: {inserted} publication URLs considered in total',flush=True)
        return True

    census=(core.ROOT/cfg['wide_census_directory']).resolve()
    if not census.is_relative_to((core.ROOT/'data/source_candidates').resolve()):raise ValueError('Invalid census folder')
    for folder in census.iterdir() if census.exists() else []:
        name='ixbt' if folder.name=='ixbt-news' else folder.name
        if name in cfg['source_order'] and (folder/'urls.txt').exists():
            with (folder/'urls.txt').open(encoding='utf-8') as handle:
                if not add(name,(line.strip() for line in handle if line.strip())):return
    for path in (core.ROOT/cfg['candidate_seed_archive']/'3dnews').glob('map-*.xml'):
        if not add('3dnews',sitemap_entries(path.read_bytes())[1]):return
    # Both original sources already contain >100k documents. Seed them as existing,
    # avoiding a network re-download when their sitemaps are traversed again.
    seed_original_existing(q,conn,cfg,core,definitions)
    # Only semantic/length exclusions are reset; service pages and content duplicates remain classified.
    q.execute("UPDATE jobs SET status='pending',attempts=0,retry_at=0,error=NULL WHERE status IN ('off_topic','short')")
    q.execute("""UPDATE jobs SET status='pending',attempts=0,retry_at=0,error=NULL WHERE status='rejected'
        AND (error LIKE '%Too little%' OR error LIKE '%fewer than%')""")
    q.execute("""UPDATE jobs SET status='pending',attempts=0,retry_at=0,error=NULL WHERE status='failed'
        AND (error LIKE '%500 Server%' OR error LIKE '%503 Server%' OR error LIKE '%Connection%' OR error LIKE '%Timeout%')""")
    q.execute("UPDATE jobs SET status='pending',attempts=0,retry_at=0,error=NULL WHERE kind='sitemap' AND status='done'")
    # Old code increased a whole host's delay for isolated HTTP500 pages indefinitely.
    q.execute("UPDATE parallel_runtime SET effective_delay=? WHERE source='naked-science'",(core.source_delay(cfg,'naked-science'),))
    if 'ixbt' in cfg['source_order']:core.enqueue(q,'ixbt','sitemap','https://www.ixbt.com/live/sitemap.xml',request_url='https://www.ixbt.com/live/sitemap.xml')
    if 'habr' in cfg['source_order']:core.enqueue(q,'habr','listing','https://habr.com/ru/articles/',request_url='https://habr.com/ru/articles/')
    q.execute("INSERT INTO collector_meta VALUES('wide_archives_v1',?)",(str(time.time()),));q.commit()
    print(f'WIDE_SEED_COMPLETE: {inserted} URLs considered; {time.monotonic()-started:.1f}s; topic filters disabled',flush=True)
