# Пояснение к файлу: Проверка сайтов-кандидатов и правил извлечения.
# Как читать: Описания сайтов → пробные страницы → заголовок/основной текст/метаданные. Эти функции также используются пилотным сборщиком.
# Команды запуска и структура проекта: README.md.

"""Isolated trials of additional long-article sources. Never connects to the corpus DB."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import escape
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys
import time
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bs4 import BeautifulSoup
from tools.probe_sources import Client, normalize_url, same_host, sample_urls, sitemap_entries

CANDIDATES = {
    "nkj": dict(label="Наука и жизнь",root="https://www.nkj.ru",
                article=r"^/archive/(?:articles|\d+)/\d+/?$",maps=r"/sitemap-iblock-\d+\.xml$",
                selectors=('[itemprop="articleBody"]',)),
    "scfh": dict(label="Наука из первых рук",root="https://scfh.ru",
                 article=r"^/papers/[^/]+/?$",maps=r"/sitemap-iblock-\d+\.xml$",
                 selectors=(".article-content",)),
    "3dnews": dict(label="3DNews: обзоры и тесты",root="https://3dnews.ru",
                   article=r"^/\d+/(?:obzor|testirovanie|sravnitel|kompyuter-mesyatsa)[^/]*$",
                   maps=r"/publications\.all\.\d+\.xml$",selectors=('.entry-body .js-mediator-article','[itemprop="articleBody"]')),
    "ixbt": dict(label="iXBT: редакционные обзоры",root="https://www.ixbt.com",
                 article=r"^/(?!news/|live/|export/)[a-z0-9_-]+/[^/]+\.html$",
                 maps=r"/sitemap-articles-\d+\.xml$",selectors=('[id^="publication-"]', '[itemprop="articleBody"]',".article-content")),
    "habr": dict(label="Хабр: статьи",root="https://habr.com",
                 article=r"^/ru/(?:companies/[^/]+/)?articles/\d+/?$",maps=None,
                 selectors=("#post-content-body", ".article-formatted-body")),
    "elementy": dict(label="Элементы: новости науки",root="https://elementy.ru",
                    article=r"^/novosti_nauki/\d+/[^/]+/?$",maps=None,selectors=(".memo",)),
}


def article_url(url, definition):
    url=normalize_url(url)
    if not same_host(url,definition) or not re.fullmatch(definition["article"],urlsplit(url).path):
        return None
    if definition is CANDIDATES["nkj"]:
        url=definition["root"]+"/archive/articles/"+urlsplit(url).path.rstrip('/').split('/')[-1]+"/"
    return url


def extract(html, definition, min_characters=300, parser='html.parser'):
    soup=BeautifulSoup(html,parser)
    heading=soup.find('h1')
    title=heading.get_text(' ',strip=True) if heading else ''
    body=next((found for sel in definition['selectors'] if (found:=soup.select_one(sel)) is not None),None)
    if body is None or not title:
        raise ValueError('Missing title or article container')
    raw=body.get_text('\n',strip=True)
    if definition.get('remove_selectors'):
        for tag in body.select(definition['remove_selectors']):tag.decompose()
    for tag in body.select('script,style,nav,footer,aside,form,figure,figcaption,pre,.comments,.related,.share,.social,.caption,.piccaption,.table-caption,.article-date,.article-author,.image-slider,.slider-slide-content'):
        tag.decompose()
    for details in body.select('details'):
        summary=details.find('summary')
        if summary and re.match(r'^(может быть интересно|читайте также)',summary.get_text(' ',strip=True),re.I):
            details.decompose()
    paragraphs=[]
    for tag in body.select('p,h2,h3,h4,li,pre'):
        if any(parent.name in ('p','li','pre') for parent in tag.parents if parent is not body):
            continue
        text=re.sub(r'\s+',' ',tag.get_text(' ',strip=True)).strip()
        if not text or re.match(r'^(читайте также|подписывай|мой telegram|реклама\b)',text,re.I):
            continue
        if re.search(r'в наш(?:ем|их).{0,60}(?:канал|telegram)',text,re.I):
            continue
        paragraphs.append(text)
    text='\n\n'.join(paragraphs)
    if not text.strip() and min_characters<=1:text=body.get_text('\n',strip=True)
    if not text.strip() or len(text)<min_characters:
        raise ValueError('Too little article text')
    flags=[]
    if re.search(r'полный текст.{0,60}(подписк|доступ)|материал доступен.{0,60}подпис|чтобы прочитать.{0,60}подпис',text,re.I):
        flags.append('possible_paywall_or_teaser')
    if '\ufffd' in text:flags.append('unicode_replacement')
    if re.search(r'подписывай|мой telegram|наши? канал',text,re.I):flags.append('possible_promotion')
    return dict(title=title,clean_text=text,raw_text=raw,flags=flags)


def trial(name, scouting, output, limit):
    definition=CANDIDATES[name];cached=scouting/name;folder=output/name;folder.mkdir(parents=True)
    discovery=json.loads((cached/'discovery.json').read_text(encoding='utf-8'))
    scout=json.loads((cached/'scout.json').read_text(encoding='utf-8'))
    client=Client(definition,1);client.session.trust_env=False
    client.robots_text=(cached/'robots.txt').read_text(encoding='utf-8')
    (folder/'robots.txt').write_text(client.robots_text,encoding='utf-8')
    client.robots=RobotFileParser();client.robots.parse(client.robots_text.splitlines())
    client.delay=max(1,float(client.robots.crawl_delay(client.session.headers['User-Agent']) or 0))
    rate=client.robots.request_rate(client.session.headers['User-Agent'])
    if rate and rate.requests:client.delay=max(client.delay,rate.seconds/rate.requests)
    last_file=max(cached.glob('*'),key=lambda p:p.stat().st_mtime)
    client.last_request=time.monotonic()-max(0,time.time()-last_file.stat().st_mtime)
    report=dict(source=name,label=definition['label'],delay_seconds=client.delay,discovered_urls=0,
                discovery_scope='selected_sitemaps' if definition['maps'] else 'homepage_examples',
                map_errors=[],attempted=0,saved=0,records=[])
    urls=[]
    try:
        if definition['maps']:
            kind,maps=sitemap_entries((cached/'sitemap-root.xml').read_bytes())
            pending=[m for m in maps if re.search(definition['maps'],urlsplit(m).path)] if kind=='sitemapindex' else []
            report['selected_sitemaps']=len(pending)
            for index,url in enumerate(pending):
                try:
                    response=client.get(url);(folder/f'map-{index}.xml').write_bytes(response.content)
                    map_kind,locations=sitemap_entries(response.content)
                    if map_kind!='urlset':raise ValueError('Nested sitemap requires separate discovery')
                    urls.extend(value for loc in locations if (value:=article_url(loc,definition)))
                except Exception as error:
                    report['map_errors'].append(dict(url=url,error=str(error)))
        else:
            soup=BeautifulSoup((cached/'home.html').read_text(encoding='utf-8'),'html.parser')
            urls=[value for a in soup.select('a[href]') if (value:=article_url(urljoin(definition['root'],a['href']),definition))]
        urls=list(dict.fromkeys(urls));report['discovered_urls']=len(urls)
        (folder/'urls.txt').write_text('\n'.join(urls),encoding='utf-8')
        chosen=sample_urls(urls,limit)
        # Reuse the inspection page when it belongs to the selected archive.
        inspection=article_url(scout.get('article_url',''),definition)
        if inspection and inspection in urls and inspection not in chosen:
            chosen=chosen[:-1]+[inspection]
        hashes=set()
        for index,url in enumerate(chosen,1):
            row=dict(url=url,ok=False);report['attempted']+=1
            try:
                if url==inspection and (cached/'inspect.html').exists():
                    html=(cached/'inspect.html').read_text(encoding='utf-8');row['cached']=True
                else:
                    response=client.get(url)
                    if 'html' not in response.headers.get('Content-Type','').lower():raise ValueError('Not HTML')
                    if response.encoding is None or response.encoding.lower() in ('iso-8859-1','ascii'):
                        response.encoding=response.apparent_encoding
                    html=response.text
                stem=f'{index:02d}';(folder/f'{stem}.html').write_text(html,encoding='utf-8')
                article=extract(html,definition);text=article.pop('clean_text');raw=article.pop('raw_text')
                digest=hashlib.sha256(re.sub(r'\s+',' ',text).strip().encode('utf-8')).hexdigest()
                row.update(article,ok=True,bytes=len(text.encode('utf-8')),words=len(text.split()),
                           duplicate_in_sample=digest in hashes,text_file=f'{stem}.txt',raw_file=f'{stem}.source.txt')
                hashes.add(digest);report['saved']+=1
                (folder/row['text_file']).write_text(text,encoding='utf-8')
                (folder/row['raw_file']).write_text(raw,encoding='utf-8')
            except Exception as error:row['error']=str(error)
            report['records'].append(row)
            (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        good=[r for r in report['records'] if r['ok']]
        report.update(mean_bytes=round(statistics.mean(r['bytes'] for r in good)) if good else 0,
                      median_bytes=round(statistics.median(r['bytes'] for r in good)) if good else 0,
                      mean_words=round(statistics.mean(r['words'] for r in good)) if good else 0)
        report['projected_selected_archive_bytes']=(round(len(urls)*sum(r['bytes'] for r in good if not r['duplicate_in_sample'])/report['attempted'])
            if report['attempted'] and definition['maps'] and not report['map_errors'] else None)
    except Exception as error:report['fatal_error']=str(error)
    finally:client.session.close()
    (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def write_summary(output,reports):
    (output/'summary.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
    cards=[]
    for report in reports:
        rows=[]
        for row in report['records']:
            if not row['ok']:
                rows.append(f'<li>{escape(row["url"])}: {escape(row.get("error",""))}</li>');continue
            folder=output/report['source']
            text=(folder/row['text_file']).read_text(encoding='utf-8');raw=(folder/row['raw_file']).read_text(encoding='utf-8')
            rows.append(f'<details><summary>{escape(row["title"])} — {row["bytes"]/1000:.1f} КБ</summary><a href="{escape(row["url"],quote=True)}">Оригинал</a><div class="columns"><pre>{escape(raw)}</pre><pre>{escape(text)}</pre></div></details>')
        cards.append(f'<h2>{escape(report["label"])}</h2><p>{report["saved"]}/{report["attempted"]}; средний текст {report.get("mean_bytes",0)/1000:.1f} КБ; медиана {report.get("median_bytes",0)/1000:.1f} КБ; интервал {report["delay_seconds"]} с; найдено {report["discovered_urls"]} URL ({report["discovery_scope"]}).</p>'+''.join(rows))
    header='''<!doctype html><html lang="ru"><meta charset="utf-8"><title>Кандидаты для корпуса</title><style>body{font:16px system-ui;max-width:1400px;margin:24px auto;padding:12px}details{padding:12px;border:1px solid #aaa;margin:8px}summary{cursor:pointer}.columns{display:grid;grid-template-columns:1fr 1fr;gap:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:65vh;overflow:auto;font:15px system-ui;min-width:0}</style><h1>Проба дополнительных источников</h1><p>Небольшие целевые выборки, не случайная оценка всего сайта. Слева текст исходного контейнера, справа извлечённый текст. Проверьте начало/конец, списки, рекламу, наличие полного текста. Исходные скрипты и изображения не исполняются. Главный корпус и очередь не менялись.</p>'''
    (output/'index.html').write_text(header+''.join(cards)+'</html>',encoding='utf-8')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scouting',type=Path,required=True)
    parser.add_argument('--source',choices=['all',*CANDIDATES],default='all')
    parser.add_argument('--limit',type=int,default=5)
    args=parser.parse_args()
    if not 1<=args.limit<=20:parser.error('--limit must be 1..20')
    output=ROOT/'data/source_candidates'/datetime.now(timezone.utc).strftime('trial-%Y%m%dT%H%M%S.%fZ')
    output.mkdir(parents=True)
    names=list(CANDIDATES) if args.source=='all' else [args.source]
    print(f'OUTPUT {output}',flush=True);reports=[]
    # Different hosts can be tested independently; requests within each host stay serial.
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        futures={pool.submit(trial,name,args.scouting.resolve(),output,args.limit):name for name in names}
        for future in as_completed(futures):
            report=future.result();reports.append(report)
            write_summary(output,reports)
            print(json.dumps({k:report.get(k) for k in ('source','discovered_urls','saved','attempted','mean_bytes','median_bytes','projected_selected_archive_bytes','fatal_error')},ensure_ascii=False),flush=True)
    print(f'REPORT {output/"index.html"}',flush=True)


if __name__=='__main__':main()
