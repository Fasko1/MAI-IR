# Пояснение к файлу: Правила и пробная проверка дополнительной группы сайтов.
# Как читать: Каталог сайтов и извлечение публикаций → сетевые пробы → диагностическая сводка. Рабочие правила переиспользует общий каталог.
# Команды запуска и структура проекта: README.md.

"""Isolated HTTP samples of further corpus candidates; never touches production state."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import re
import statistics
import sys
from urllib.parse import urljoin, urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bs4 import BeautifulSoup
from lxml import etree
from tools.probe_sources import Client, normalize_url, same_host, sitemap_entries
from tools.probe_candidates import extract

SITES={
 'nplus1':dict(root='https://nplus1.ru',label='N+1',pattern=r'^/(?:news|material|blog)/\d{4}/\d{2}/\d{2}/[^/]+/?$',selectors=('.mb-14:has(.n1_material)','article','[itemprop="articleBody"]')),
 'indicator':dict(root='https://indicator.ru',label='Индикатор',pattern=r'^/[^/]+/[^/]+\.htm$',selectors=('[itemprop="articleBody"]','article')),
 'ferra':dict(root='https://www.ferra.ru',label='Ferra',pattern=r'^/(?:news|review|lifehack|experts)/[^/]+/[^/]+\.htm$',selectors=('[itemprop="articleBody"]','article')),
 'ichip':dict(root='https://ichip.ru',label='CHIP',pattern=r'^/(?:novosti|obzory|sovety|tekhnologii)/.+-\d+/?$',selectors=('[itemprop="articleBody"]','.article__body','.article__content','article')),
 'computerra':dict(root='https://www.computerra.ru',label='Компьютерра',pattern=r'^/\d+/[^/]+/?$',selectors=('.single-post__content','.entry-content','[itemprop="articleBody"]','article')),
 'scientificrussia':dict(root='https://scientificrussia.ru',label='Научная Россия',pattern=r'^/articles/[^/]+/?$',selectors=('.article-text','[itemprop="articleBody"]','article')),
 '4pda':dict(root='https://4pda.to',label='4PDA',pattern=r'^/\d{4}/\d{2}/\d{2}/\d+/[^/]+/?$',selectors=('[itemprop="articleBody"]','.content-box')),
 'overclockers':dict(root='https://overclockers.ru',label='Overclockers.ru',pattern=r'^/(?:hardnews/\d+|softnews/\d+|lab/show/\d+|blog/[^/]+/show/\d+)/[^/]+/?$',selectors=('.material-content','.article-content','[itemprop="articleBody"]','article')),
 'trashbox':dict(root='https://trashbox.ru',label='Trashbox',pattern=r'^/link/[^/]+/?$',selectors=('[itemprop="articleBody"]','.topic-body','.topic-content','article')),
 'ixbt-news':dict(root='https://www.ixbt.com',label='iXBT: новости',pattern=r'^/news/.+\.html$',selectors=('[itemprop="articleBody"]','[id^="publication-"]','.news-body','article'),seed_urls='data/source_candidates/census-20261108/ixbt-news/urls.txt'),
}


def extract_more(html,definition,min_characters=300):
    original_raw=None
    if definition['root']=='https://4pda.to':
        # Generic .content is also used for user comments and must never be a fallback.
        tree=etree.HTML(html)
        headings=tree.xpath('//h1') if tree is not None else []
        bodies=tree.xpath('//*[@itemprop="articleBody"]') if tree is not None else []
        if not bodies and tree is not None:
            bodies=tree.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," content-box ")]')
        if not headings or not bodies:raise ValueError('Missing verified 4PDA article body')
        html=etree.tostring(headings[0],encoding='unicode')+etree.tostring(bodies[0],encoding='unicode')
        definition=dict(definition,selectors=('[itemprop="articleBody"]','.content-box'))
    if definition['root']=='https://www.computerra.ru':
        soup=BeautifulSoup(html,'html.parser');body=soup.select_one('.single-post__content')
        if body:
            original_raw=body.get_text('\n',strip=True)
            for tag in list(body.select('p,h2,h3')):
                if re.fullmatch(r'Читайте также\s*:',tag.get_text(' ',strip=True),re.I):
                    sibling=tag.find_next_sibling()
                    if sibling and sibling.name in ('ul','ol'):sibling.decompose()
                    tag.decompose()
        html=str(soup)
    if definition['root']=='https://overclockers.ru':
        soup=BeautifulSoup(html,'html.parser')
        if not soup.h1:
            meta=soup.select_one('meta[property="og:title"]')
            if meta and meta.get('content'):
                heading=soup.new_tag('h1');heading.string=meta['content'];soup.insert(0,heading)
        html=str(soup)
    result=extract(html,definition,min_characters=min_characters)
    if original_raw is not None:result['raw_text']=original_raw
    return result


def links(html,definition):
    values=[]
    for anchor in BeautifulSoup(html,'html.parser').select('a[href]'):
        url=normalize_url(urljoin(definition['root'],anchor['href']))
        if same_host(url,definition) and re.fullmatch(definition['pattern'],urlsplit(url).path):values.append(url)
    return list(dict.fromkeys(values))


def probe(name,output,scout_only=False):
    definition=SITES[name];folder=output/name;folder.mkdir(parents=True,exist_ok=True)
    report=dict(source=name,label=definition['label'],root=definition['root'],records=[],maps=[])
    client=Client(definition,1);client.session.trust_env=False
    try:
        client.load_robots(folder);report['delay_seconds']=client.delay
        response=client.get(definition['root']+'/');response.encoding=response.apparent_encoding
        html=response.text;(folder/'home.html').write_text(html,encoding='utf-8')
        urls=links(html,definition);report['homepage_article_urls']=urls
        if definition.get('seed_urls'):
            from tools.probe_sources import sample_urls
            urls=sample_urls((ROOT/definition['seed_urls']).read_text(encoding='utf-8').splitlines(),5)
        maps=re.findall(r'^Sitemap:\s*(\S+)',client.robots_text,re.I|re.M)
        report['sitemaps_advertised']=maps
        for index,url in enumerate(maps[:1]):
            try:
                r=client.get(url);(folder/f'map-{index}.xml').write_bytes(r.content)
                kind,locations=sitemap_entries(r.content)
                report['maps'].append(dict(url=url,kind=kind,count=len(locations),examples=locations[:8]))
                if kind=='urlset':report['mapped_article_urls']=sum(bool(re.fullmatch(definition['pattern'],urlsplit(loc).path)) for loc in locations)
            except Exception as error:report['maps'].append(dict(url=url,error=str(error)))
        if 'Auth-controller' in html:
            report['fatal_error']='HTTP returned Auth-controller challenge instead of article content'
            urls=[]
        # The first footer/editorial links and interactive quizzes are unsuitable text samples.
        urls=[u for u in urls if not any(x in u for x in ('/samotek','test-smozhete','test-smozhete-li'))]
        for index,url in enumerate(urls[:1] if scout_only else urls[:5],1):
            row=dict(url=url,ok=False)
            try:
                response=client.get(url)
                if 'html' not in response.headers.get('Content-Type','').lower():raise ValueError('Not HTML')
                response.encoding=response.apparent_encoding
                html=response.text;(folder/f'{index:02d}.html').write_text(html,encoding='utf-8')
                soup=BeautifulSoup(html,'html.parser');row['title']=soup.h1.get_text(' ',strip=True) if soup.h1 else ''
                row['containers']=[dict(tag=t.name,classes=t.get('class',[]),id=t.get('id'),length=len(t.get_text()))
                    for t in soup.select('article, [itemprop="articleBody"], [class*="content"], [class*="body"], [class*="text"]') if len(t.get_text())>500][:35]
                article=extract_more(html,definition);text=article.pop('clean_text');raw=article.pop('raw_text')
                row.update(article,ok=True,bytes=len(text.encode('utf-8')),words=len(text.split()))
                (folder/f'{index:02d}.txt').write_text(text,encoding='utf-8')
                (folder/f'{index:02d}.source.txt').write_text(raw,encoding='utf-8')
            except Exception as error:row['error']=str(error)
            report['records'].append(row)
    except Exception as error:report['fatal_error']=str(error)
    finally:client.session.close()
    good=[r for r in report['records'] if r['ok']]
    report.update(saved=len(good),attempted=len(report['records']),mean_bytes=round(statistics.mean(r['bytes'] for r in good)) if good else 0,
                  median_bytes=round(statistics.median(r['bytes'] for r in good)) if good else 0)
    (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    output=ROOT/'data/source_candidates'/datetime.now(timezone.utc).strftime('more-%Y%m%dT%H%M%SZ');output.mkdir(parents=True)
    print(f'OUTPUT {output}',flush=True);reports=[]
    names=sys.argv[sys.argv.index('--sources')+1].split(',') if '--sources' in sys.argv else list(SITES)
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        for future in as_completed([pool.submit(probe,name,output,'--scout' in sys.argv) for name in names]):
            report=future.result();reports.append(report)
            (output/'summary.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({k:v for k,v in report.items() if k not in ('records','homepage_article_urls')},ensure_ascii=False),flush=True)
    cards=[]
    for r in reports:
        items=[]
        for i,row in enumerate(r['records'],1):
            if not row['ok']:continue
            raw=(output/r['source']/f'{i:02d}.source.txt').read_text(encoding='utf-8')
            clean=(output/r['source']/f'{i:02d}.txt').read_text(encoding='utf-8')
            items.append(f'<details><summary>{escape(row["title"])}</summary><a href="{escape(row["url"],quote=True)}">Original</a><div><pre>{escape(raw)}</pre><pre>{escape(clean)}</pre></div></details>')
        cards.append(f'<h2>{escape(r["label"])}: {r["saved"]}/{r["attempted"]}; {r["mean_bytes"]/1000:.1f} KB</h2>'+''.join(items))
    (output/'index.html').write_text('<!doctype html><meta charset="utf-8"><style>body{font:16px system-ui;margin:24px}div{display:grid;grid-template-columns:1fr 1fr;gap:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere;min-width:0}</style>'+''.join(cards),encoding='utf-8')


if __name__=='__main__':main()
