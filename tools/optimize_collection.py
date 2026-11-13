# Пояснение к файлу: Настройка параметров и очереди расширенного сбора.
# Как читать: Подготовка оптимизаций по состоянию источников. Изменяет служебные данные, поэтому не требуется при показе готового поиска.
# Команды запуска и структура проекта: README.md.

"""Idempotent queue upgrades; original alias jobs remain available for audit."""
import json
import re
import time
from collections import defaultdict
from urllib.parse import urlsplit

from tools.corpus_catalog import canonical


def migrate_queue(q,cfg,core,definitions):
    summary={}
    if '3dnews' in cfg['source_order'] and not q.execute("SELECT 1 FROM collector_meta WHERE key='3dnews_identity_v2'").fetchone():
        q.execute('''CREATE TABLE IF NOT EXISTS job_aliases (
            source TEXT,kind TEXT,original_key TEXT,canonical_key TEXT,snapshot TEXT NOT NULL,
            PRIMARY KEY(source,kind,original_key))''')
        groups=defaultdict(list)
        for row in q.execute("SELECT * FROM jobs WHERE source='3dnews' AND kind='article' AND status!='alias'"):
            key=canonical('3dnews',row['key'],definitions['3dnews'])
            if key:groups[key].append(dict(row))
        priority={'saved':0,'existing':1,'duplicate':2,'pending':3,'failed':4,'rejected':5}
        aliases=0
        for key,rows in groups.items():
            winner=min(rows,key=lambda r:(priority.get(r['status'],6),r['attempts']))
            for row in rows:
                q.execute('INSERT OR IGNORE INTO job_aliases VALUES(?,?,?,?,?)',
                    ('3dnews','article',row['key'],key,json.dumps(row,ensure_ascii=False)))
                if row['key']!=key:
                    q.execute("UPDATE jobs SET status='alias' WHERE source='3dnews' AND kind='article' AND key=?",(row['key'],))
                    aliases+=1
            payload=json.loads(winner['payload']);payload['request_url']=key
            q.execute('''INSERT INTO jobs(source,kind,key,payload,status,attempts,retry_at,error)
                VALUES('3dnews','article',?,?,?,?,?,?) ON CONFLICT(source,kind,key) DO UPDATE SET
                payload=excluded.payload,status=excluded.status,attempts=excluded.attempts,
                retry_at=excluded.retry_at,error=excluded.error''',
                (key,json.dumps(payload,ensure_ascii=False),winner['status'],winner['attempts'],winner['retry_at'],winner['error']))
        q.execute("INSERT INTO collector_meta VALUES('3dnews_identity_v2',?)",(str(time.time()),))
        q.commit();summary['3dnews_aliases_retained']=aliases;summary['3dnews_id_page_groups']=len(groups)
    if 'nkj' in cfg['source_order'] and not q.execute("SELECT 1 FROM collector_meta WHERE key='nkj_news_body_v2'").fetchone():
        reset=0
        for row in q.execute("SELECT key FROM jobs WHERE source='nkj' AND kind='article' AND status='rejected' AND error='Missing title or article container'").fetchall():
            if re.fullmatch(r'/(?:news|facts)/\d+/?',urlsplit(row['key']).path):
                q.execute("UPDATE jobs SET status='pending',attempts=0,retry_at=0,error=NULL WHERE source='nkj' AND kind='article' AND key=?",(row['key'],))
                reset+=1
        q.execute("INSERT INTO collector_meta VALUES('nkj_news_body_v2',?)",(str(time.time()),));q.commit()
        summary['nkj_publications_requeued']=reset
    if 'vokrugsveta' in cfg['source_order'] and not q.execute("SELECT 1 FROM collector_meta WHERE key='vokrugsveta_sections_v2'").fetchone():
        reset=q.execute("UPDATE jobs SET status='pending',attempts=0,retry_at=0,error=NULL WHERE source='vokrugsveta' AND kind='sitemap' AND status='done'").rowcount
        q.execute("INSERT INTO collector_meta VALUES('vokrugsveta_sections_v2',?)",(str(time.time()),));q.commit()
        summary['vokrugsveta_maps_refreshed']=reset
    if summary:
        (core.STATE/'optimization-migration.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        print('OPTIMIZATION_MIGRATION '+json.dumps(summary),flush=True)
    return summary
