# Пояснение к файлу: Общие HTTP-примитивы и правила первоначальных дополнительных источников.
# Как читать: Client соблюдает задержки/robots; разбор sitemap находит статьи; extract_article очищает HTML. Другие сборщики используют эти функции.
# Команды запуска и структура проекта: README.md.

"""Small, isolated source trial. Does not connect to PostgreSQL or crawler state."""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import statistics
import time
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "MAI-InfoSearch/2026 educational crawler"
SOURCES = {
    "naked-science": {
        "root": "https://naked-science.ru",
        "sitemap": "https://naked-science.ru/sitemap.xml",
        "map_pattern": r"/sitemap-posttype-post(?:[.\d-]*)\.xml$",
        "article_pattern": r"^/article/[^/]+/[^/]+/?$",
        "selectors": ("article.ns-editorial-article .body", "article .body", '[itemprop="articleBody"]'),
    },
    "hi-news": {
        "root": "https://hi-news.ru",
        "sitemap": "https://hi-news.ru/wp-sitemap.xml",
        "map_pattern": r"/wp-sitemap-posts-post-\d+\.xml$",
        "article_pattern": r"^/[^/]+/[^/]+\.html$",
        "selectors": ("main .item .text", ".item .text", ".entry-content", '[itemprop="articleBody"]'),
    },
}


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))


def same_host(url: str, source: dict) -> bool:
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and parts.hostname == urlsplit(source["root"]).hostname


def robots_allowed(robots_text: str, url: str, user_agent: str = USER_AGENT) -> bool:
    """Match Allow/Disallow paths with wildcards and longest-match precedence.

    urllib.robotparser normalizes '/?' to '/', accidentally blocking the entire
    Naked Science archive. Keep the literal query marker in rules and paths.
    """
    groups, agents, rules = [], [], []
    has_directives = False
    for raw_line in robots_text.splitlines() + ["User-agent: __end__"]:
        line = raw_line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if has_directives:
                groups.append((agents, rules))
                agents, rules, has_directives = [], [], False
            agents.append(value.lower())
        elif agents:
            has_directives = True
            if key in ("allow", "disallow") and value:
                rules.append((value, key == "allow"))
    matching = []
    for group_agents, group_rules in groups:
        scores = [0 if agent == "*" else len(agent) for agent in group_agents
                  if agent == "*" or agent in user_agent.lower()]
        if scores:
            matching.append((max(scores), group_rules))
    if not matching:
        return True
    best_agent = max(score for score, _ in matching)
    parts = urlsplit(url)
    path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
    matches = []
    for score, group_rules in matching:
        if score != best_agent:
            continue
        for pattern, allow in group_rules:
            anchored = pattern.endswith("$")
            expression = re.escape(pattern[:-1] if anchored else pattern).replace(r"\*", ".*")
            if re.match("^" + expression + ("$" if anchored else ""), path):
                matches.append((len(pattern.rstrip("$").replace("*", "")), allow))
    return max(matches)[1] if matches else True


class Client:
    def __init__(self, source: dict, delay: float):
        self.source = source
        self.delay = delay
        self.last_request = 0.0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.robots = None
        self.robots_text = ""

    def get(self, url: str, *, robots_request: bool = False):
        # Check every redirect as well as the original URL before requesting it.
        for _ in range(6):
            if not same_host(url, self.source):
                raise ValueError(f"URL outside source: {url}")
            if not robots_request and (self.robots is None or not robots_allowed(self.robots_text, url)):
                raise ValueError(f"robots.txt does not allow: {url}")
            time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            response = self.session.get(url, timeout=30, allow_redirects=False)
            if response.status_code in (301, 302, 303, 307, 308):
                url = urljoin(url, response.headers["Location"])
                continue
            response.raise_for_status()
            return response
        raise ValueError("Too many redirects")

    def load_robots(self, directory: Path):
        response = self.get(self.source["root"] + "/robots.txt", robots_request=True)
        (directory / "robots.txt").write_text(response.text, encoding="utf-8")
        self.robots_text = response.text
        self.robots = RobotFileParser()
        self.robots.parse(response.text.splitlines())
        self.delay = max(self.delay, float(self.robots.crawl_delay(USER_AGENT) or 0))
        rate = self.robots.request_rate(USER_AGENT)
        if rate and rate.requests:
            self.delay = max(self.delay, rate.seconds / rate.requests)


def sitemap_entries(content: bytes) -> tuple[str, list[str]]:
    if content.startswith(b"\x1f\x8b"):
        content = gzip.decompress(content)
    root = etree.fromstring(content, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    kind = etree.QName(root).localname
    if kind not in ("sitemapindex", "urlset"):
        raise ValueError(f"Unexpected sitemap root: {kind}")
    # Only direct page/map locations; exclude nested image/video <loc> elements.
    locations = root.xpath('./*[local-name()="sitemap" or local-name()="url"]/*[local-name()="loc"]/text()')
    return kind, [value.strip() for value in locations]


def discover(client: Client) -> tuple[list[str], list[dict]]:
    source = client.source
    pending = deque([source["sitemap"]])
    seen_maps, articles, seen_articles, errors = set(), [], set(), []
    while pending:
        url = pending.popleft()
        if url in seen_maps:
            continue
        if len(seen_maps) >= 100:
            errors.append({"url": url, "error": "Sitemap request limit reached"})
            break
        seen_maps.add(url)
        try:
            kind, locations = sitemap_entries(client.get(url).content)
            if kind == "sitemapindex":
                pending.extend(loc for loc in locations if same_host(loc, source)
                               and re.search(source["map_pattern"], urlsplit(loc).path))
            else:
                for loc in locations:
                    normalized = normalize_url(loc)
                    if (same_host(normalized, source)
                            and re.fullmatch(source["article_pattern"], urlsplit(normalized).path)
                            and normalized not in seen_articles):
                        articles.append(normalized)
                        seen_articles.add(normalized)
            print(f"  sitemap {len(seen_maps)}: {len(articles)} article URLs", flush=True)
        except (requests.RequestException, ValueError, etree.XMLSyntaxError, OSError) as error:
            errors.append({"url": url, "error": str(error)})
    return articles, errors


def extract_article(html: str, source: dict, *, min_characters: int = 300) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    title = heading.get_text(" ", strip=True) if heading else ""
    container, selector = None, ""
    for selector in source["selectors"]:
        container = soup.select_one(selector)
        if container is not None:
            break
    if container is None:
        raise ValueError("Article container not found")
    lead = soup.select_one("article .post-lead") if source is SOURCES["naked-science"] else None
    lead_text = re.sub(r"\s+", " ", lead.get_text(" ", strip=True)).strip() if lead else ""
    for tag in container.select("script, style, nav, footer, aside, form, figure, .related, .comments, .sharedaddy, .wp-caption-text"):
        tag.decompose()
    blocks = []
    for tag in container.select("p, h2, h3, h4, li"):
        if any(parent.name in ("p", "h2", "h3", "h4", "li")
               for parent in tag.parents if parent is not container):
            continue
        text = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
        if tag.select_one('a[href*="t.me/hinews_ru"], a[href*="t.me/hi_news_chat"]'):
            continue
        if text and not re.match(r"^(читайте также|читай также|е[щш][её] больше .*?(?:канал|материал)|подписывайтесь)", text, re.I):
            blocks.append(text)
    if lead_text and (not blocks or lead_text != blocks[0]):
        blocks.insert(0, lead_text)
    text = "\n\n".join(blocks)
    if not text.strip() and min_characters<=1:text=container.get_text("\n",strip=True)
    if not title or len(text) < min_characters:
        raise ValueError(f"Missing title or fewer than {min_characters} characters of article text")
    date = soup.select_one('meta[property="article:published_time"]')
    return {"title": title, "published": date.get("content") if date else None,
            "selector": selector, "clean_text": text}


def sample_urls(urls: list[str], limit: int) -> list[str]:
    # Evenly spaced across the complete discovered archive, including both ends.
    if len(urls) <= limit:
        return urls
    if limit == 1:
        return [urls[len(urls) // 2]]
    return [urls[round(index * (len(urls) - 1) / (limit - 1))] for index in range(limit)]


def probe(name: str, directory: Path, args) -> dict:
    directory.mkdir(parents=True)
    source = SOURCES[name]
    client = Client(source, args.delay)
    result = {"source": name, "discovered_urls": 0, "attempted": 0,
              "saved": 0, "records": [], "discovery_errors": [], "fatal_error": None}
    try:
        client.load_robots(directory)
        urls, errors = discover(client)
        result["discovered_urls"] = len(urls)
        result["discovery_errors"] = errors
        (directory / "urls.txt").write_text("\n".join(urls), encoding="utf-8")
        hashes = set()
        for index, url in enumerate(sample_urls(urls, args.limit), 1):
            record = {"url": url, "ok": False}
            result["attempted"] += 1
            print(f"  article {index}: {url}", flush=True)
            try:
                response = client.get(url)
                if "html" not in response.headers.get("Content-Type", "").lower():
                    raise ValueError("Response is not HTML")
                # Both sources publish UTF-8. Avoid requests' text/html Latin-1 default.
                response.encoding = "utf-8"
                stem = f"{index:03d}"
                (directory / f"{stem}.html").write_bytes(response.content)
                article = extract_article(response.text, source)
                text = article.pop("clean_text")
                digest = hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode("utf-8")).hexdigest()
                record.update(article, ok=True, bytes=len(text.encode("utf-8")),
                              sha256=digest, duplicate_in_sample=digest in hashes,
                              text_file=f"{stem}.txt", final_url=response.url)
                hashes.add(digest)
                (directory / f"{stem}.txt").write_text(text, encoding="utf-8")
                result["saved"] += 1
            except (requests.RequestException, ValueError) as error:
                record["error"] = str(error)
            result["records"].append(record)
            (directory / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        good = [r for r in result["records"] if r["ok"]]
        unique_bytes = sum(r["bytes"] for r in good if not r["duplicate_in_sample"])
        result.update(sample_unique_bytes=unique_bytes,
                      mean_text_bytes=round(statistics.mean(r["bytes"] for r in good)) if good else 0,
                      median_text_bytes=round(statistics.median(r["bytes"] for r in good)) if good else 0,
                      projected_bytes=round(len(urls) * unique_bytes / result["attempted"]) if result["attempted"] else 0)
    except (requests.RequestException, ValueError, etree.XMLSyntaxError) as error:
        result["fatal_error"] = str(error)
    finally:
        client.session.close()
    (directory / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["all", *SOURCES], default="all")
    parser.add_argument("--limit", type=int, default=20, help="Articles per source (1 to 500)")
    parser.add_argument("--delay", type=float, default=1.0, help="Minimum seconds between requests, at least 1")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "source_probes")
    args = parser.parse_args()
    if not 1 <= args.limit <= 500 or args.delay < 1:
        parser.error("--limit must be 1..500 and --delay must be at least 1 second")
    run = args.output.resolve() / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run.mkdir(parents=True)
    results = []
    for name in SOURCES if args.source == "all" else [args.source]:
        print(f"Source: {name}", flush=True)
        results.append(probe(name, run / name, args))
    (run / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Пробная загрузка источников", "",
             "| Источник | URL в картах | Сохранено / попыток | Средний текст, КБ | Оценка архива, МБ |",
             "|---|---:|---:|---:|---:|"]
    for r in results:
        lines.append(f'| {r["source"]} | {r["discovered_urls"]} | {r["saved"]} / {r["attempted"]} | '
                     f'{r.get("mean_text_bytes", 0) / 1000:.1f} | {r.get("projected_bytes", 0) / 1e6:.1f} |')
    lines += ["", "КБ/МБ десятичные; измеряется только UTF-8 текст без HTML.", "",
              "Оценка — экстраполяция небольшой равномерной выборки, не измеренный размер всего архива. "
              "Ошибка выделения текста может увеличить или уменьшить оценку. "
              "Дубли проверены только внутри выборки каждого сайта; сравнения с основной БД не было. "
              "Ошибки sitemap делают подсчёт архива неполным. "
              "У Naked Science проверены редакционные статьи /article/, без сообщества и blitz.", "",
              "Сначала вручную сравните 10–15 пар .html/.txt, затем запустите пилот на 300 статьях. "
              "report.json содержит ошибки, даты (где доступны), URL, селекторы и размеры."]
    for r in results:
        if r["fatal_error"] or r["discovery_errors"]:
            lines += ["", f'Ошибки {r["source"]}: {r["fatal_error"] or r["discovery_errors"]}']
    (run / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Reports: {run}", flush=True)
    for r in results:
        print(f'{r["source"]}: {r["saved"]}/{r["attempted"]}; URLs: {r["discovered_urls"]}; '
              f'projected MB: {r.get("projected_bytes", 0) / 1e6:.1f}', flush=True)
    return int(any(r["fatal_error"] or r["discovery_errors"] or not r["saved"] for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
