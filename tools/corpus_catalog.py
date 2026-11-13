# Пояснение к файлу: Объединённые определения сайтов и извлечение публикаций.
# Как читать: configure добавляет описания; canonical нормализует URL; extract_publication выбирает правила очистки конкретного сайта.
# Команды запуска и структура проекта: README.md.

"""Full publication scopes for the shared crawler; no keyword/topic selection."""
import json
import re
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup
from tools.probe_candidates import CANDIDATES, extract
from tools.probe_more_sources import SITES, extract_more
from tools.probe_sources import SOURCES, normalize_url, same_host

MORE=('nplus1','scientificrussia','computerra','ichip','trashbox','overclockers','ferra','indicator','4pda')
CATALOG={name:dict(SITES[name],article_pattern=SITES[name]['pattern'],map_pattern=r'\.xml(?:\.gz)?$',
                  sitemap={'nplus1':'https://nplus1.ru/sitemap.xml','scientificrussia':'https://scientificrussia.ru/sitemap.xml',
                    'computerra':'https://www.computerra.ru/sitemap_index.xml','ichip':'https://ichip.ru/out/sitemap/sitemap.xml',
                    'trashbox':'https://trashbox.ru/sitemap.xml','overclockers':'https://overclockers.ru/sitemap/index.xml.gz',
                    'ferra':'https://www.ferra.ru/sitemap.xml','indicator':'https://indicator.ru/sitemap.xml.gz',
                    '4pda':'https://4pda.to/sitemap.xml'}[name]) for name in MORE}
CATALOG.update({
 'hightech':dict(root='https://hightech.fm',label='Хайтек',sitemap='https://hightech.fm/wp-sitemap.xml',
                    article_pattern=r'^/\d{4}/\d{2}/\d{2}/[^/]+/?$',map_pattern=r'/wp-sitemap-posts-post-\d+\.xml$',selectors=('.page-content__body',)),
 'securitylab':dict(root='https://www.securitylab.ru',label='SecurityLab',sitemap='https://www.securitylab.ru/sitemap.xml',
                    article_pattern=r'^/(?:news|analytics)/\d+\.php$',map_pattern=r'\.xml$',selectors=('[itemprop="articleBody"]',)),
 'vokrugsveta':dict(root='https://www.vokrugsveta.ru',label='Вокруг света',sitemap='https://www.vokrugsveta.ru/sitemaps/index.xml.gz',
                    article_pattern=r'^/(?:articles|article|vs|news|science|history|nauka|telegraph|travels)/(?:[^/]+/)*[^/]+/?$',map_pattern=r'/articles_[^/]+\.xml\.gz$',selectors=('.ds-article-content',)),
 'techinsider':dict(root='https://www.techinsider.ru',label='Техинсайдер',sitemap='https://www.techinsider.ru/sitemap.xml',
                    article_pattern=r'^/(?!authors?/|tags?/|search/|sitemap)[^/]+/(?:news-)?\d+[^/]*/?$',map_pattern=r'\.xml(?:\.gz)?$',selectors=('[itemprop="articleBody"]','article')),
 'biomolecula':dict(root='https://biomolecula.ru',label='Биомолекула',sitemap='https://biomolecula.ru/sitemap.xml',
                    article_pattern=r'^/(?!tags?/|category/|authors?/|search|user|profile)[^?#]+$',map_pattern=r'\.xml(?:\.gz)?$',selectors=('.mainbar__insert',)),
})


def configure(definitions):
    definitions.update(CATALOG)
    definitions['3dnews'].update(article_pattern=r'^/\d+(?:/[^/]+)?/?$',selectors=('.entry-body .js-mediator-article','.entry-body','[itemprop="articleBody"]'),map_pattern=r'/publications\..*\.xml$')
    definitions['ixbt'].update(article_pattern=r'^/(?!export/|forum/|authors?/|tags?/).+\.html$',map_pattern=r'\.xml(?:\.gz)?$',selectors=('[itemprop="articleBody"]','[id^="publication-"]','.article-content','.news-body','article'))
    definitions['scfh'].update(article_pattern=r'^/(?:papers|news)/[^/]+/?$',selectors=('.article-content','[itemprop="articleBody"]','article'))
    definitions['nkj'].update(article_pattern=r'^/.*/\d+/?$',
        selectors=('[itemprop="articleBody"]','article.news-detail'),
        remove_selectors='.rel-articles,.news-detail-prop,.news-detail-info,.news-detail-footer')
    # CHIP includes selections/guides as well as the original four sections.
    definitions['ichip']['article_pattern']=r'^/(?!out/|authors?/|tags?/).+-\d+/?$'


def canonical(name,url,definition):
    if name in ('techinsider','biomolecula'):
        parts=urlsplit(url);root=urlsplit(definition['root'])
        if parts.hostname in (root.hostname.removeprefix('www.'),'www.'+root.hostname.removeprefix('www.')):
            url=urlunsplit((root.scheme,root.netloc,parts.path,parts.query,parts.fragment))
    if not same_host(url,definition) or not re.fullmatch(definition['article_pattern'],urlsplit(url).path):return None
    key=normalize_url(url)
    if name=='3dnews':
        parts=urlsplit(key);match=re.fullmatch(r'/(\d+)(?:/([^/]+))?/?',parts.path)
        if match:
            # Continuations have their own identity; slugs/schemes/slashes do not.
            page=match[2] if match[2] and re.fullmatch(r'page[-_]?\d+(?:\.html)?',match[2]) else None
            key='https://3dnews.ru/'+match[1]+('/'+page if page else '')
    if name=='habr':key='https://habr.com/ru/articles/'+re.search(r'/articles/(\d+)',key).group(1)+'/'
    if name=='nkj' and urlsplit(key).path.startswith('/archive/'):
        key=definition['root']+'/archive/articles/'+urlsplit(key).path.rstrip('/').split('/')[-1]+'/'
    return key


def extract_publication(name,html,url,definition,min_characters=1):
    if name in ('techinsider','biomolecula'):
        from lab02.crawler import extract_clean_text, extract_metadata
        text=extract_clean_text(html,url)
        title,author,published=extract_metadata(html,url)
        if not title or not text.strip():raise ValueError('Missing title or article text')
        return dict(title=title,clean_text=text,raw_text=text,flags=[],author=author,published=published)
    adapted=dict(definition)
    # Helpers select by the current production selectors, not the narrow trial scope.
    if name in MORE:return extract_more(html,adapted,min_characters=min_characters)
    return extract(html,adapted,min_characters=min_characters)
