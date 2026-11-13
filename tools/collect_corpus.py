# Пояснение к файлу: Общая очередь, хранение и последовательный режим расширенного сбора.
# Как читать: SQLite хранит задания; PostgreSQL хранит статьи; блокировки предотвращают двойной запуск; run выбирает последовательный или parallel режим.
# Команды запуска и структура проекта: README.md.

"""Resume collection of two magazines and topical Russian Wikipedia into MAI-IR PostgreSQL."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import requests
import yaml
from bs4 import BeautifulSoup
from lxml import etree
from tools.probe_sources import Client, SOURCES, extract_article, normalize_url, same_host, sitemap_entries
from src.config import load_config
from src.db import connect

STATE = ROOT / "data" / "expansion"
NAMES = {"hi-news": "Hi-News", "naked-science": "Naked Science", "wikipedia": "Википедия (ru)"}
NAMES.update({"3dnews":"3DNews", "ixbt":"iXBT", "scfh":"Наука из первых рук",
              "nkj":"Наука и жизнь", "habr":"Хабр", "elementy":"Элементы"})
NAMES.update({"nplus1":"N+1","scientificrussia":"Научная Россия","computerra":"Компьютерра",
              "ichip":"CHIP","trashbox":"Trashbox","overclockers":"Overclockers.ru",
              "techinsider":"Техинсайдер","biomolecula":"Биомолекула",
              "ferra":"Ferra","indicator":"Индикатор","4pda":"4PDA"})
API = "https://ru.wikipedia.org/w/api.php"
NAMES.update({"hightech":"Хайтек","securitylab":"SecurityLab","vokrugsveta":"Вокруг света"})


def digest_text(text):
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode("utf-8")).hexdigest()


TOPIC_STEMS = ("физик", "химич", "биолог", "астроном", "косм", "геолог", "техник", "технолог",
               "информат", "медицин", "нейро", "эколог", "авиац", "робот", "математ", "алгоритм",
               "атом", "молекул", "электр", "энерги", "квант", "генет", "клетк", "организм",
               "вычисл", "программ", "механик", "оптич", "излучен", "магнит", "спутник",
               "планет", "двигател", "научн", "наук", "инженер", "термодин", "давлен", "вирус",
               "телефон", "смартфон", "компьют", "процессор", "ноутбук", "телескоп", "бактери",
               "гриб", "токсин", "растени", "животн", "металл", "лазер", "нейтрон", "фотон",
               "геном", "ракет", "болезн", "иммун", "лечени", "спектр", "звёзд", "солнц",
               "здоров", "кров", "мозг", "лекар", "плесен")


def wiki_on_topic(title, text, config):
    if re.search(config["wiki_exclude_titles"], title):
        return False
    if config.get("topic_exclude_titles") and re.search(config["topic_exclude_titles"],title):
        return False
    if any(stem in title.lower() for stem in ("физик", "хим", "биолог", "астроном", "косм", "геолог",
                                               "нейро", "робот", "квант", "математ", "термодин")):
        return True
    intro = (title + " " + text[:3000]).lower()
    return sum(stem in intro for stem in TOPIC_STEMS) >= 2


def accept_wiki_topic(title, text, config):
    return not config.get("wiki_topic_filter", True) or wiki_on_topic(title, text, config)


def accept_source_topic(source, title, text, config):
    if config.get("accept_all_publications",False):return True
    if source == "wikipedia":
        return accept_wiki_topic(title,text,config)
    return not config.get("source_topic_filters",{}).get(source,True) or wiki_on_topic(title,text,config)


def minimum_text_bytes(source, config):
    if source == "wikipedia":
        return config.get("wiki_min_text_bytes",config["min_text_bytes"])
    return config.get("source_min_text_bytes",{}).get(source,config["min_text_bytes"])


def wiki_text(html):
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one(".mw-parser-output")
    if body is None:
        raise ValueError("No Wikipedia article container")
    if body.select_one("#disambigbox, .mw-disambig, .disambig"):
        raise ValueError("Disambiguation page")
    for tag in body.select("script, style, table, figure, .thumb, .navbox, .infobox, .metadata, .hatnote, .toc, .mw-editsection, .reference, .references, .reflist, .noprint, .catlinks"):
        tag.decompose()
    blocks, skipping = [], False
    for tag in body.select("p, h2, h3, h4, li"):
        if any(p.name in ("p", "li") for p in tag.parents if p is not body):
            continue
        text = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
        if tag.name == "h2":
            skipping = bool(re.fullmatch(r"(Примечания|Литература|Ссылки|Источники|См\. также|Библиография)", text, re.I))
        if not skipping and text:
            blocks.append(text)
    return "\n\n".join(blocks)


@contextmanager
# Защита от двух одновременно работающих сборщиков, которые могли бы повторно брать одни задания.
def process_lock():
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "collector.lock").open("a+b") as handle:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("Another collector already holds data/expansion/collector.lock")
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


# SQLite хранит состояние задания между запусками; это очередь обхода, а не поисковый индекс.
def queue_open():
    q = sqlite3.connect(STATE / "queue.sqlite")
    q.row_factory = sqlite3.Row
    q.execute("PRAGMA journal_mode=WAL")
    q.execute('''CREATE TABLE IF NOT EXISTS jobs (
        source TEXT, kind TEXT, key TEXT, payload TEXT, status TEXT DEFAULT 'pending',
        attempts INTEGER DEFAULT 0, retry_at REAL DEFAULT 0, error TEXT,
        PRIMARY KEY(source,kind,key))''')
    q.execute("CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(status,source,kind,retry_at)")
    q.execute("CREATE INDEX IF NOT EXISTS jobs_pick ON jobs(source,kind,status)")
    q.execute('''CREATE TABLE IF NOT EXISTS source_health (
        source TEXT PRIMARY KEY, consecutive_errors INTEGER NOT NULL DEFAULT 0,
        cooldown_until REAL NOT NULL DEFAULT 0, cooldown_round INTEGER NOT NULL DEFAULT 0)''')
    q.commit()
    return q


@contextmanager
def prevent_idle_sleep():
    """Keep Windows awake only for this process; display and power policy stay unchanged."""
    if os.name!='nt':
        yield
        return
    import ctypes
    set_state=ctypes.windll.kernel32.SetThreadExecutionState
    set_state.argtypes=[ctypes.c_uint];set_state.restype=ctypes.c_uint
    previous=set_state(0x80000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    if previous:print('POWER: automatic sleep prevented while collector is running',flush=True)
    else:print('POWER: unable to request prevention of automatic sleep',flush=True)
    try:yield
    finally:
        if previous:set_state(previous)


def enqueue(q, source, kind, key, **payload):
    q.execute("INSERT OR IGNORE INTO jobs(source,kind,key,payload) VALUES(?,?,?,?)",
              (source, kind, str(key), json.dumps(payload, ensure_ascii=False)))
    # A category later added as a root must get the full traversal depth.
    if kind == "category" and "depth" in payload:
        existing = q.execute("SELECT payload FROM jobs WHERE source=? AND kind=? AND key=?",
                             (source,kind,str(key))).fetchone()
        if existing and json.loads(existing["payload"])["depth"] > payload["depth"]:
            q.execute('''UPDATE jobs SET payload=?,status='pending',attempts=0,retry_at=0,error=NULL
                WHERE source=? AND kind=? AND key=?''',
                (json.dumps(payload,ensure_ascii=False),source,kind,str(key)))


def init_hashes(conn):
    # A companion table adds exact-content dedup without modifying existing documents.
    with conn.cursor() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS corpus_content_hashes (
            sha256 TEXT PRIMARY KEY, document_id INTEGER NOT NULL UNIQUE REFERENCES documents(id),
            text_bytes BIGINT NOT NULL, source TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS corpus_provenance (
            document_id INTEGER PRIMARY KEY REFERENCES documents(id), metadata JSONB NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS corpus_exclusions (
            document_id INTEGER PRIMARY KEY REFERENCES documents(id), reason TEXT NOT NULL)''')
    conn.commit()
    count = 0
    with conn.cursor(name="seed_content_hashes") as read:
        read.itersize = 200
        read.execute('''SELECT d.id,d.clean_text,d.source FROM documents d
            LEFT JOIN corpus_content_hashes h ON h.document_id=d.id
            WHERE h.document_id IS NULL AND d.clean_text IS NOT NULL AND d.clean_text<>'' ORDER BY d.id''')
        for doc_id, text, source in read:
            with conn.cursor() as c:
                c.execute('''INSERT INTO corpus_content_hashes VALUES(%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING''', (digest_text(text), doc_id, len(text.encode("utf-8")), source))
            count += 1
            if count % 10000 == 0:
                print(f"Hashing existing corpus: {count}", flush=True)
    conn.commit()


# Проверка минимального текста/дубликатов и сохранение HTML, текста и метаданных в PostgreSQL.
def save_article(conn, url, html, title, text, source, meta, minimum):
    if len(text.encode("utf-8")) < minimum:
        return "short"
    now = int(time.time())
    digest = digest_text(text)
    with conn.cursor() as c:
        c.execute("SELECT 1 FROM corpus_content_hashes WHERE sha256=%s", (digest,))
        if c.fetchone():
            return "duplicate"
        c.execute('''INSERT INTO documents(url,normalized_url,html,clean_text,title,author,publish_date,
            source,fetch_timestamp,last_fetch_attempt) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(normalized_url) DO NOTHING RETURNING id''',
            (url,url,html,text,title,meta.get("author", ""),meta.get("published"),NAMES[source],now,now))
        row = c.fetchone()
        if row is None:
            conn.commit()
            return "existing"
        c.execute("INSERT INTO corpus_content_hashes VALUES(%s,%s,%s,%s)",
                  (digest,row[0],len(text.encode("utf-8")),NAMES[source]))
        c.execute("INSERT INTO corpus_provenance VALUES(%s,%s::jsonb)",
                  (row[0],json.dumps(meta, ensure_ascii=False)))
    conn.commit()
    return "saved"


class WikiBackoff(requests.RequestException):
    def __init__(self, message, retry_after):
        super().__init__(message)
        self.retry_after = retry_after


def retry_after_seconds(response, default):
    value = response.headers.get("Retry-After", "")
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = date.timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            seconds = 0
    return max(default, seconds)


class WikiClient:
    def __init__(self, delay):
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["User-Agent"] = "MAI-InfoSearch/2026 (educational topical corpus; local research)"
        self.delay, self.last = delay, 0
        self.max_attempts = 3
        self.metrics = dict(http_requests=0,http_seconds=0.0,max_http_seconds=0.0,
                            requests_over_5_seconds=0,interval_wait_seconds=0.0,retry_sleep_seconds=0.0)

    def query(self, **params):
        for attempt in range(self.max_attempts):
            interval_wait = max(0,self.delay-(time.monotonic()-self.last))
            self.metrics["interval_wait_seconds"] += interval_wait
            time.sleep(interval_wait)
            self.last = time.monotonic()
            request_start = time.perf_counter()
            try:
                r = self.session.get(API, params=dict(format="json", formatversion=2, maxlag=5, **params), timeout=45)
            finally:
                elapsed = time.perf_counter()-request_start
                self.metrics["http_requests"] += 1
                self.metrics["http_seconds"] += elapsed
                self.metrics["max_http_seconds"] = max(self.metrics["max_http_seconds"],elapsed)
                self.metrics["requests_over_5_seconds"] += elapsed>5
            if r.status_code == 429 or r.status_code >= 500:
                if r.status_code == 429:
                    self.delay = min(5.0,max(0.5,self.delay*2))
                wait = retry_after_seconds(r,10*(attempt+1))
                if wait > 60 or self.max_attempts == 1:
                    raise WikiBackoff(f"Wikipedia HTTP {r.status_code}; server requested {wait:.0f}s pause",wait)
                self.metrics["retry_sleep_seconds"] += wait
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            if data.get("error", {}).get("code") in ("maxlag", "ratelimited"):
                if data["error"]["code"] == "ratelimited":
                    self.delay = min(5.0,max(0.5,self.delay*2))
                wait = retry_after_seconds(r,10*(attempt+1))
                if wait > 60 or self.max_attempts == 1:
                    raise WikiBackoff(f"Wikipedia {data['error']['code']}; server requested {wait:.0f}s pause",wait)
                self.metrics["retry_sleep_seconds"] += wait
                time.sleep(wait)
                continue
            if "error" in data:
                raise ValueError(json.dumps(data["error"], ensure_ascii=False))
            return data
        raise requests.RequestException("Wikipedia busy; job retained for retry")


def category_step(q, wiki, job, config):
    data = json.loads(job["payload"])
    params = dict(action="query", list="categorymembers", cmtitle=job["key"],
                  cmtype="page|subcat", cmnamespace="0|14", cmlimit=500)
    if data.get("continuation"):
        params["cmcontinue"] = data["continuation"]
    response = wiki.query(**params)
    for member in response["query"]["categorymembers"]:
        if member["ns"] == 0:
            enqueue(q, "wikipedia", "article", member["pageid"], title=member["title"], category=job["key"])
        elif data["depth"] < config["wiki_depth"] and not re.search(config["wiki_exclude_categories"], member["title"]):
            enqueue(q, "wikipedia", "category", member["title"], depth=data["depth"] + 1)
    continuation = response.get("continue", {}).get("cmcontinue")
    if continuation:
        data["continuation"] = continuation
        q.execute("UPDATE jobs SET payload=? WHERE source=? AND kind=? AND key=?",
                  (json.dumps(data),job["source"],job["kind"],job["key"]))
    else:
        finish(q, job, "done")
    q.commit()


def finish(q, job, status, error=None):
    q.execute("UPDATE jobs SET status=?,error=? WHERE source=? AND kind=? AND key=?",
              (status,error,job["source"],job["kind"],job["key"]))


def next_job(q, source, kind):
    # Hi-News starts with very short 2006 notices; begin with its more recent end.
    order = "rowid DESC" if source == "hi-news" and kind == "article" else "rowid ASC"
    if kind == "category":
        order = "json_extract(payload,'$.depth') ASC, rowid ASC"
    return q.execute('''SELECT * FROM jobs INDEXED BY jobs_pick WHERE source=? AND kind=? AND status='pending'
        AND retry_at<=? ORDER BY ''' + order + " LIMIT 1", (source,kind,time.time())).fetchone()


def active_source(q, config):
    """Delayed retries still belong to the current source: do not skip ahead."""
    for source in config["source_order"]:
        if q.execute("SELECT 1 FROM jobs WHERE source=? AND status='pending' LIMIT 1",(source,)).fetchone():
            return source
    return None


def source_delay(config, source):
    return float(config.get("source_delays",{}).get(source,config["delay_seconds"]))


def storage_status(config):
    paths=[ROOT]+[Path(value) for value in config.get('storage_paths',[])]
    result=[]
    for path in paths:
        path=path if path.is_absolute() else ROOT/path
        try:result.append(dict(path=str(path),free_bytes=shutil.disk_usage(path).free))
        except OSError as error:result.append(dict(path=str(path),free_bytes=0,error=str(error)))
    return result


def has_storage_space(config):
    return all('error' not in disk and disk['free_bytes']>=config['min_free_disk_bytes']
               for disk in storage_status(config))


def scheduled_jobs(q, source, config):
    if source != "wikipedia":
        return [(source,"sitemap"),(source,"article")]
    prefetch=max(1,int(config.get("wiki_prefetch_articles",5000)))
    enough=q.execute('''SELECT 1 FROM jobs WHERE source='wikipedia' AND kind='article'
        AND status='pending' AND retry_at<=? LIMIT 1 OFFSET ?''',(time.time(),prefetch-1)).fetchone()
    if enough:
        return [(source,"article")]
    return [(source,"category"),(source,"article")]


def source_health(q, source):
    row = q.execute("SELECT * FROM source_health WHERE source=?",(source,)).fetchone()
    return dict(row) if row else dict(source=source,consecutive_errors=0,cooldown_until=0,cooldown_round=0)


def note_response(q, source, failed, config):
    """Persist server pauses so a restart cannot bypass them."""
    health = source_health(q,source)
    if not failed:
        health.update(consecutive_errors=0,cooldown_until=0,cooldown_round=0)
    else:
        health["consecutive_errors"] += 1
        if health["consecutive_errors"] >= config.get("source_error_threshold",5):
            health["cooldown_round"] += 1
            pause = min(config.get("source_max_cooldown_seconds",1800),
                        config.get("source_cooldown_seconds",300)*2**min(health["cooldown_round"]-1,10))
            health.update(consecutive_errors=0,cooldown_until=time.time()+pause)
            print(f"PAUSE {source}: repeated server/network errors; retry in {pause} seconds",flush=True)
    q.execute('''INSERT INTO source_health VALUES(:source,:consecutive_errors,:cooldown_until,:cooldown_round)
        ON CONFLICT(source) DO UPDATE SET consecutive_errors=excluded.consecutive_errors,
        cooldown_until=excluded.cooldown_until,cooldown_round=excluded.cooldown_round''',health)
    q.commit()
    return health


def retryable_error(error):
    if not isinstance(error,requests.RequestException):
        return False
    response = getattr(error,"response",None)
    return response is None or response.status_code in (408,425,429) or response.status_code >= 500


def is_dns_error(error):
    return isinstance(error,requests.ConnectionError) and any(value in str(error).lower()
        for value in ('nameresolutionerror','getaddrinfo failed','temporary failure in name resolution','name or service not known'))


def log_error(q, job, error, config):
    if is_dns_error(error):
        # Losing DNS is not evidence that the publication has failed four times.
        pause=config.get('dns_retry_seconds',30)
        q.execute("UPDATE jobs SET status='pending',retry_at=?,error=? WHERE source=? AND kind=? AND key=?",
                  (time.time()+pause,str(error),job['source'],job['kind'],job['key']))
        q.execute("INSERT OR IGNORE INTO source_health(source,consecutive_errors,cooldown_until,cooldown_round) VALUES(?,0,0,0)",(job['source'],))
        q.execute('UPDATE source_health SET cooldown_until=MAX(cooldown_until,?) WHERE source=?',
                  (time.time()+pause,job['source']))
        q.commit()
        print(f"RETRY {job['source']} DNS: {pause}s; article attempts unchanged",flush=True)
        return
    attempts = job["attempts"]+1
    transient = retryable_error(error)
    retry = transient and attempts < 4
    rejected = job["kind"] == "article" and isinstance(error,ValueError)
    delay = min(1800,300*2**(attempts-1)) if retry else 0
    delay = max(delay,getattr(error,"retry_after",0))
    state = "pending" if retry else ("rejected" if rejected else "failed")
    q.execute('''UPDATE jobs SET attempts=?,status=?,retry_at=?,error=?
        WHERE source=? AND kind=? AND key=?''',
        (attempts,state,time.time()+delay,str(error),job["source"],job["kind"],job["key"]))
    q.commit()
    if transient:
        note_response(q,job["source"],True,config)
    if isinstance(error,WikiBackoff):
        q.execute("UPDATE source_health SET cooldown_until=MAX(cooldown_until,?) WHERE source=?",
                  (time.time()+error.retry_after,job["source"]))
        q.commit()
    label = "SKIP" if rejected else ("RETRY" if retry else "FAILED")
    print(f"{label} {job['source']} {job['key']}: {error}",flush=True)


def status(conn, q, config, state):
    with conn.cursor() as c:
        c.execute('''SELECT h.source,COUNT(*),SUM(h.text_bytes) FROM corpus_content_hashes h
            WHERE NOT EXISTS (SELECT 1 FROM corpus_exclusions e WHERE e.document_id=h.document_id)
            GROUP BY h.source''')
        sources = [{"source":s,"unique_documents":n,"text_bytes":int(b)} for s,n,b in c]
        c.execute("SELECT COUNT(*) FROM documents")
        document_count = c.fetchone()[0]
    config["_document_count"] = document_count
    total = sum(s["text_bytes"] for s in sources)
    report = dict(state=state, updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
                  pid=os.getpid(), target_bytes=config["target_bytes"], unique_text_bytes=total,
                  remaining_bytes=max(0,config["target_bytes"]-total), sources=sources,
                  target_reached=total>=config["target_bytes"],stop_at_target=config.get("stop_at_target",True),
                  max_documents=config["max_documents"],total_documents=document_count,
                  active_source=None if config.get("collection_mode")=="parallel" else active_source(q,config),
                  active_sources=config.get("_active_sources",[]),source_order=config["source_order"],
                  collection_mode=config.get("collection_mode","sequential"),
                  source_metrics=config.get("_source_metrics",[]),
                  source_delays=config.get("source_delays",{}),
                  source_health=[dict(r) for r in q.execute("SELECT * FROM source_health")],
                  wikipedia_metrics={k:round(v,3) if isinstance(v,float) else v
                                     for k,v in config.get("_wiki_metrics",{}).items()},
                  wiki_prefetch_articles=config.get("wiki_prefetch_articles",5000),
                  storage=storage_status(config),
                  queue=[dict(r) for r in q.execute("SELECT source,kind,status,COUNT(*) AS count FROM jobs GROUP BY source,kind,status")])
    tmp = STATE / "status.tmp"
    tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(STATE / "status.json")
    display_source='parallel' if config.get('collection_mode')=='parallel' else report['active_source']
    print(f'{state}: source={display_source}; documents={document_count}/{config["max_documents"]}; '
          f'{total/1e9:.4f} GB / {config["target_bytes"]/1e9:.2f} GB unique UTF-8',flush=True)
    return total


# Общий жизненный цикл: конфиг → блокировка → БД/очередь → выбор режима обхода → сохранение итогового состояния.
def run(args):
    from src.config import get_collection_config
    config = get_collection_config(load_config(str(ROOT / "config.yaml")))
    if config.get("collection_mode") == "parallel":
        from tools.collect_parallel import run_parallel
        return run_parallel(args,config,sys.modules[__name__])
    config.setdefault("source_order",["hi-news","wikipedia","naked-science"])
    config.setdefault("max_documents",1000000)
    if (set(config["source_order"]) != set(NAMES) or len(config["source_order"]) != len(NAMES)
            or config["max_documents"] < 1 or config["delay_seconds"] < 0
            or any(source_delay(config,name)<0 for name in NAMES)):
        raise ValueError("Invalid source order, document limit or delays in config.yaml collection settings")
    conn = connect(load_config(str(ROOT / "config.yaml")))
    q = queue_open()
    clients = {}
    wiki = WikiClient(source_delay(config,"wikipedia"))
    config["_wiki_metrics"] = wiki.metrics
    try:
        init_hashes(conn)
        if args.status:
            status(conn,q,config,"status_only")
            return
        # Seed complete, previously verified magazine URL lists if available.
        for name, source in SOURCES.items():
            if not q.execute("SELECT 1 FROM jobs WHERE source=? LIMIT 1",(name,)).fetchone():
                cached = sorted((ROOT / "data/source_probes").glob(f"*/{name}/urls.txt"))
                if cached:
                    for url in cached[-1].read_text(encoding="utf-8").splitlines():
                        if same_host(url,source) and re.fullmatch(source["article_pattern"],urlsplit(url).path):
                            enqueue(q,name,"article",normalize_url(url))
                else:
                    enqueue(q,name,"sitemap",source["sitemap"])
            # Discover publications added since the original trial URL lists.
            enqueue(q,name,"sitemap",source["sitemap"])
        for title in config["wiki_categories"]:
            enqueue(q,"wikipedia","category","Категория:"+title,depth=0)
        q.commit()
        count, last_status, last_source = 0, 0, None
        while True:
            if (STATE / "STOP").exists():
                status(conn,q,config,"stopped_by_file")
                return
            if not has_storage_space(config):
                status(conn,q,config,"low_disk_space")
                return
            if time.monotonic()-last_status >= 30:
                total = status(conn,q,config,"running")
                last_status = time.monotonic()
                if config.get("stop_at_target",True) and total >= config["target_bytes"]:
                    status(conn,q,config,"target_reached")
                    return
            if config.get("_document_count",0) >= config["max_documents"]:
                status(conn,q,config,"document_limit_reached")
                return
            current = active_source(q,config)
            if current is None:
                failures = q.execute("SELECT COUNT(*) FROM jobs WHERE status='failed'").fetchone()[0]
                status(conn,q,config,"sources_finished_with_errors" if failures else "sources_exhausted")
                return
            if current != last_source:
                if last_source is not None:
                    results = [dict(row) for row in q.execute(
                        "SELECT status,COUNT(*) AS count FROM jobs WHERE source=? GROUP BY status",(last_source,))]
                    print(f"SOURCE_DONE {last_source}: {json.dumps(results)}",flush=True)
                print(f"SOURCE {current}: collecting until its queue and deferred retries are exhausted",flush=True)
                last_source = current
                status(conn,q,config,"running")
            health = source_health(q,current)
            if health["cooldown_until"] > time.time():
                status(conn,q,config,"source_cooldown")
                time.sleep(min(30,max(0,health["cooldown_until"]-time.time())))
                continue
            if current != "wikipedia" and current not in clients:
                client = Client(SOURCES[current],source_delay(config,current))
                client.session.trust_env = False
                directory = STATE / current
                directory.mkdir(exist_ok=True)
                try:
                    client.load_robots(directory)
                    clients[current] = client
                except (requests.RequestException,ValueError) as error:
                    client.session.close()
                    note_response(q,current,True,config)
                    print(f"RETRY {current} robots.txt: {error}",flush=True)
                    status(conn,q,config,"source_unavailable")
                    time.sleep(30)
                    continue
            worked = False
            jobs = scheduled_jobs(q,current,config)
            for name, kind in jobs:
                if config.get("_document_count",0) >= config["max_documents"]:
                    status(conn,q,config,"document_limit_reached")
                    return
                if source_health(q,current)["cooldown_until"] > time.time():
                    break
                job = next_job(q,name,kind)
                if job is None:
                    continue
                worked = True
                try:
                    if kind == "category":
                        category_step(q,wiki,job,config)
                        note_response(q,name,False,config)
                    elif kind == "sitemap":
                        map_kind, urls = sitemap_entries(clients[name].get(job["key"]).content)
                        note_response(q,name,False,config)
                        for url in urls:
                            if not same_host(url,SOURCES[name]):
                                continue
                            if map_kind == "sitemapindex" and re.search(SOURCES[name]["map_pattern"],urlsplit(url).path):
                                enqueue(q,name,"sitemap",url)
                            elif map_kind == "urlset" and re.fullmatch(SOURCES[name]["article_pattern"],urlsplit(url).path):
                                enqueue(q,name,"article",normalize_url(url))
                        finish(q,job,"done")
                        q.commit()
                    else:
                        if name == "wikipedia":
                            parsed = wiki.query(action="parse",pageid=job["key"],prop="text|revid|properties",redirects=1,disableeditsection=1)["parse"]
                            note_response(q,name,False,config)
                            if re.match(r"^(Категория|Шаблон|Википедия|Портал|Обсуждение|Файл|Участник):",parsed["title"],re.I):
                                raise ValueError("Not an encyclopedia article")
                            if "disambiguation" in parsed.get("properties",{}):
                                raise ValueError("Disambiguation page")
                            html = parsed["text"]
                            text,title = wiki_text(html),parsed["title"]
                            if not accept_wiki_topic(title,text,config):
                                finish(q,job,"off_topic")
                                q.commit()
                                print(f"wikipedia off_topic: {title}",flush=True)
                                continue
                            url = "https://ru.wikipedia.org/wiki/"+quote(title.replace(" ","_"),safe="()_")
                            meta = dict(page_id=parsed["pageid"], revision_id=parsed["revid"],
                                        revision_url=f'https://ru.wikipedia.org/w/index.php?oldid={parsed["revid"]}',
                                        history_url=f'https://ru.wikipedia.org/w/index.php?curid={parsed["pageid"]}&action=history',
                                        category=json.loads(job["payload"])["category"],
                                        license="CC BY-SA; see article footer and history", author="Wikipedia contributors")
                        else:
                            response = clients[name].get(job["key"])
                            note_response(q,name,False,config)
                            if "html" not in response.headers.get("Content-Type","").lower():
                                raise ValueError("Not HTML")
                            response.encoding = "utf-8"
                            html = response.text
                            article = extract_article(html,SOURCES[name],
                                min_characters=config.get("source_min_text_characters",{}).get(name,300))
                            title,text = article.pop("title"),article.pop("clean_text")
                            meta = article
                            url = normalize_url(response.url)
                            if not accept_source_topic(name,title,text,config):
                                finish(q,job,"off_topic")
                                q.commit()
                                print(f"{name} off_topic: {title}",flush=True)
                                continue
                        minimum = minimum_text_bytes(name,config)
                        outcome = save_article(conn,url,html,title,text,name,meta,minimum)
                        finish(q,job,outcome)
                        q.commit()
                        count += outcome == "saved"
                        config["_document_count"] += outcome == "saved"
                        print(f"{name} {outcome}: {url}",flush=True)
                        if args.max_new and count >= args.max_new:
                            status(conn,q,config,"batch_complete")
                            return
                except (requests.RequestException,ValueError,KeyError,etree.XMLSyntaxError) as error:
                    conn.rollback()
                    log_error(q,job,error,config)
            if not worked:
                status(conn,q,config,"waiting_for_source_retry")
                time.sleep(30)
    except KeyboardInterrupt:
        conn.rollback()
        status(conn,q,config,"interrupted")
    finally:
        conn.close()
        q.close()
        wiki.session.close()
        for client in clients.values():
            client.session.close()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-new",type=int,default=30,help="New documents this run; 0 runs until configured limits or exhaustion")
    parser.add_argument("--status",action="store_true")
    args = parser.parse_args()
    if args.max_new < 0:
        parser.error("--max-new must be nonnegative")
    with process_lock(),prevent_idle_sleep():
        run(args)


if __name__ == "__main__":
    main()
