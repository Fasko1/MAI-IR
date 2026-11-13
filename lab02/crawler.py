# Пояснение к файлу: Базовый робот двух источников для ЛР2.
# Как читать: URL/sitemap → robots.txt и задержка → HTML → текст → PostgreSQL; отдельные блоки сохраняют очередь и проверяют обновления.
# Команды запуска и структура проекта: README.md.

# ============================================================
# ЛР2. ПОИСКОВЫЙ РОБОТ
# Версия с поясняющими комментариями для защиты.
#
# Общая схема работы:
# 1) читаем config.yaml;
# 2) подключаемся к PostgreSQL;
# 3) получаем URL из sitemap двух сайтов;
# 4) нормализуем и фильтруем URL;
# 5) проверяем robots.txt;
# 6) скачиваем HTML;
# 7) извлекаем чистый текст и метаданные;
# 8) сохраняем документ в БД;
# 9) после каждого документа сохраняем состояние краулера;
# 10) после основного обхода проверяем старые документы на изменения.
# ============================================================

from __future__ import annotations

import hashlib
import argparse
import shutil

import json

import pickle

import re

import signal

import sys

import time

from collections import deque

from pathlib import Path

from urllib.parse import urlparse, urlunparse

from urllib.robotparser import RobotFileParser

import requests

from bs4 import BeautifulSoup
from bs4.exceptions import ParserRejectedMarkup

from lxml import etree

# Корень проекта MAI-IR.
# Нужен, чтобы корректно находить src/ и crawler_state.pkl.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:

    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config

from src.db import (

    connect,

    count_documents,

    create_tables,

    document_exists,

)

# Файл состояния краулера: оставшаяся очередь + число сохранённых документов.
# Благодаря нему после Ctrl+C можно продолжить с нужного места.
STATE_FILE = PROJECT_ROOT / "crawler_state.pkl"

# ------------------------------------------------------------
# URL: нормализация
# ------------------------------------------------------------
def normalize_url(url: str) -> str:

    """

    Приводит URL к каноническому виду.

    Удаляет query-параметры и fragment.

    """

    # Разбиваем URL на scheme / domain / path / query / fragment.
    parsed = urlparse(url)

    scheme = parsed.scheme.lower() or "https"

    netloc = parsed.netloc.lower()

    path = parsed.path or "/"

    return urlunparse(

        (

            scheme,

            netloc,

            path,

            "",

            "",

            "",

        )

    )

# ------------------------------------------------------------
# Определение источника по домену
# ------------------------------------------------------------
def get_source_name(url: str) -> str:

    """

    Определяет источник по доменному имени.

    """

    domain = urlparse(url).netloc.lower()

    if domain.endswith("biomolecula.ru"):

        return "Биомолекула"

    if domain.endswith("techinsider.ru"):

        return "Техинсайдер"

    return "Неизвестный источник"

# ------------------------------------------------------------
# Фильтрация: статья это или служебная страница
# ------------------------------------------------------------
def qualifies_as_article(url: str) -> bool:

    """

    Отделяет страницы статей от служебных страниц.

    """

    parsed = urlparse(url)

    domain = (parsed.hostname or "").lower()

    path = parsed.path.lower()

    if parsed.scheme.lower() not in ("http", "https"):
        return False

    if domain not in ("biomolecula.ru", "www.biomolecula.ru",
                      "techinsider.ru", "www.techinsider.ru"):
        return False

    # Медиафайлы и вложения не являются HTML-статьями.
    if Path(path.rstrip("/")).suffix not in ("", ".html", ".htm"):
        return False

    if "biomolecula.ru" in domain:

        excluded = (

            "/tag/",

            "/category/",

            "/authors/",

            "/search",

            "/user",

            "/profile",

        )

        if not path or path == "/":

            return False

        if any(path.startswith(prefix) for prefix in excluded):

            return False

        return True

    if "techinsider.ru" in domain:

        excluded = (

            "/author/",

            "/authors/",

            "/category/",

            "/tag/",

            "/search",

            "/sitemap",

        )

        if any(path.startswith(prefix) for prefix in excluded):

            return False

        return any(character.isdigit() for character in path)

    return False

# ------------------------------------------------------------
# Sitemap: загрузка XML и извлечение <loc>
# ------------------------------------------------------------
def request_with_retries(session, url: str, **kwargs):
    """Повторяет временные сетевые ошибки, 429 и ошибки сервера."""
    for attempt in range(3):
        try:
            response = session.get(url, **kwargs)
            if response.status_code == 429 or 500 <= response.status_code < 600:
                response.raise_for_status()
            return response
        except requests.RequestException:
            if attempt == 2:
                raise
            delay = 5 * (attempt + 1)
            print(f"[retry] Повтор запроса через {delay} с: {url}")
            time.sleep(delay)


def fetch_sitemap_urls(

    sitemap_url: str,

    session: requests.Session,

) -> list[str]:

    """

    Загружает sitemap и извлекает значения <loc>.

    """

    try:

        response = request_with_retries(session,

            sitemap_url,

            timeout=30,

        )

        response.raise_for_status()

        root = etree.fromstring(response.content)

        result: list[str] = []

        for element in root.xpath(

            '/*[local-name()="sitemapindex"]/*[local-name()="sitemap"]/*[local-name()="loc"]'
            ' | /*[local-name()="urlset"]/*[local-name()="url"]/*[local-name()="loc"]'

        ):

            if element.text:

                result.append(element.text.strip())

        return result

    except Exception as error:

        print(

            f"[sitemap] Ошибка загрузки "

            f"{sitemap_url}: {error}"

        )

        return []

# Кэш robots.txt, чтобы не скачивать один и тот же файл перед каждой страницей.
robots_cache: dict[str, RobotFileParser | None] = {}

# ------------------------------------------------------------
# Проверка robots.txt
# ------------------------------------------------------------
def allowed_by_robots(

    url: str,

    user_agent: str,

) -> bool:

    """

    Проверяет разрешение robots.txt.

    """

    parsed = urlparse(url)

    domain_root = (

        f"{parsed.scheme or 'https'}://{parsed.netloc}"

    )

    if domain_root not in robots_cache:

        parser = RobotFileParser()

        parser.set_url(

            f"{domain_root}/robots.txt"

        )

        try:

            parser.read()

            robots_cache[domain_root] = parser

        except Exception as error:

            print(

                f"[robots] Не удалось получить "

                f"robots.txt для {domain_root}: {error}"

            )

            robots_cache[domain_root] = None

    parser = robots_cache[domain_root]

    if parser is None:

        return True

    # True = нашему User-Agent разрешено скачивать этот URL.
    return parser.can_fetch(user_agent, url)

# ------------------------------------------------------------
# Метаданные: title / author / publish_date
# Сначала JSON-LD, затем запасные варианты под конкретный сайт.
# ------------------------------------------------------------
def extract_metadata(

    html: str,

    url: str,

) -> tuple[str, str, str | None]:

    """

    Извлекает заголовок, автора/авторов и дату публикации.

    Поддерживает Biomolecula и TechInsider.

    """

    soup = BeautifulSoup(

        html,

        "html.parser",

    )

    title = ""

    author = ""

    publish_date = None

    def parse_jsonld_author(value) -> str:

        """

        Разбирает поле author из JSON-LD.

        author может быть:

        - строкой;

        - объектом;

        - списком объектов.

        """

        names: list[str] = []

        if isinstance(value, str):

            if value.strip():

                names.append(value.strip())

        elif isinstance(value, dict):

            name = value.get("name")

            if isinstance(name, str) and name.strip():

                names.append(name.strip())

        elif isinstance(value, list):

            for item in value:

                parsed_name = parse_jsonld_author(item)

                if parsed_name:

                    names.extend(

                        name.strip()

                        for name in parsed_name.split(",")

                        if name.strip()

                    )

        return ", ".join(dict.fromkeys(names))

    # =========================================================

    # JSON-LD

    # =========================================================

    for script in soup.find_all(

        "script",

        type="application/ld+json",

    ):

        raw_data = script.string

        if not raw_data:

            continue

        try:

            data = json.loads(raw_data)

        except (json.JSONDecodeError, TypeError):

            continue

        # JSON-LD может быть вложенным: обходим структуру через стек.
        stack = [data]

        while stack:

            item = stack.pop()

            if isinstance(item, list):

                stack.extend(item)

                continue

            if not isinstance(item, dict):

                continue

            if not title:

                headline = item.get("headline")

                if isinstance(headline, str):

                    title = headline.strip()

            if not author:

                parsed_author = parse_jsonld_author(

                    item.get("author")

                )

                if parsed_author:

                    author = parsed_author

            if not publish_date:

                date_value = item.get(

                    "datePublished"

                )

                if date_value:

                    publish_date = str(

                        date_value

                    ).split("T")[0]

            stack.extend(item.values())

    # =========================================================

    # Заголовок — запасной вариант

    # =========================================================

    if not title:

        title_tag = soup.find("h1")

        if title_tag:

            title = title_tag.get_text(

                " ",

                strip=True,

            )

    # =========================================================

    # TECHINSIDER

    # =========================================================

    if "techinsider.ru" in url:

        if not author:

            for attrs in (

                {"name": "author"},

                {"property": "author"},

                {"property": "article:author"},

            ):

                meta = soup.find(

                    "meta",

                    attrs=attrs,

                )

                if meta and meta.get("content"):

                    author = meta["content"].strip()

                    break

        if not publish_date:

            for attrs in (

                {"property": "article:published_time"},

                {"name": "date"},

                {"name": "publish_date"},

            ):

                meta = soup.find(

                    "meta",

                    attrs=attrs,

                )

                if meta and meta.get("content"):

                    publish_date = (

                        meta["content"]

                        .strip()

                        .split("T")[0]

                    )

                    break

    # =========================================================

    # BIOMOLECULA

    # =========================================================

    elif "biomolecula.ru" in url:

        # -----------------------------------------------------

        # Автор / авторы

        # -----------------------------------------------------

        # У Biomolecula автор хранится в исходном HTML

        # в meta-теге:

        # \<meta content="Антон Чугунов" name="author">

        author_meta = soup.find(

            "meta",

            attrs={"name": "author"},

        )

        if author_meta and author_meta.get("content"):

            author = author_meta["content"].strip()

        # Запасной вариант:

        # если meta author вдруг отсутствует,

        # пробуем взять автора из видимого блока страницы.

        if not author:

            author_names: list[str] = []

            author_elements = soup.select(

                "ul.article-authors__list "

                "a.article-authors__link span"

            )

            for author_element in author_elements:

                name = author_element.get_text(

                    " ",

                    strip=True,

                )

                if name:

                    author_names.append(name)

            if author_names:

                author = ", ".join(

                    dict.fromkeys(author_names)

                )

        # -----------------------------------------------------

        # Дата публикации

        # -----------------------------------------------------

        if not publish_date:

            time_tag = soup.find("time")

            if time_tag:

                datetime_value = time_tag.get(

                    "datetime"

                )

                if datetime_value:

                    publish_date = (

                        datetime_value

                        .strip()

                        .split("T")[0]

                    )

        if not publish_date:

            meta = soup.find(

                "meta",

                attrs={

                    "property": "article:published_time"

                },

            )

            if meta and meta.get("content"):

                publish_date = (

                    meta["content"]

                    .strip()

                    .split("T")[0]

                )

        # Запасной вариант:

        # ищем дату вида "07 июня 2004".

        if not publish_date:

            page_text = soup.get_text(

                " ",

                strip=True,

            )

            match = re.search(

                r"\b(\d{1,2})\s+"

                r"(января|февраля|марта|апреля|мая|июня|"

                r"июля|августа|сентября|октября|ноября|декабря)"

                r"\s+(\d{4})\b",

                page_text,

                re.IGNORECASE,

            )

            if match:

                months = {

                    "января": 1,

                    "февраля": 2,

                    "марта": 3,

                    "апреля": 4,

                    "мая": 5,

                    "июня": 6,

                    "июля": 7,

                    "августа": 8,

                    "сентября": 9,

                    "октября": 10,

                    "ноября": 11,

                    "декабря": 12,

                }

                day = int(match.group(1))

                month = months[

                    match.group(2).lower()

                ]

                year = int(match.group(3))

                publish_date = (

                    f"{year:04d}-"

                    f"{month:02d}-"

                    f"{day:02d}"

                )

    return title, author, publish_date

# ------------------------------------------------------------
# Основной текст статьи
# TechInsider: articleBody из JSON-LD.
# Biomolecula: основной HTML-контейнер + очистка параграфов.
# ------------------------------------------------------------
def extract_clean_text(

    html: str,

    url: str,

) -> str:

    """

    Извлекает основной текст статьи.

    """

    soup = BeautifulSoup(

        html,

        "html.parser",

    )

    if "techinsider.ru" in url:

        for script in soup.find_all(

            "script",

            type="application/ld+json",

        ):

            if not script.string:

                continue

            try:

                data = json.loads(script.string)

            except json.JSONDecodeError:

                continue

            stack = [data]

            while stack:

                item = stack.pop()

                if isinstance(item, list):

                    stack.extend(item)

                elif isinstance(item, dict):

                    body = item.get("articleBody")

                    if isinstance(body, str) and body.strip():

                        return body.strip()

                    stack.extend(item.values())

        # Некоторые статьи не содержат articleBody в JSON-LD.
        # Ищем только основной блок статьи, не весь текст страницы.
        for container in soup.select('[itemprop="articleBody"], article'):
            for tag in container.select('script, style, nav, footer, aside, form, figure'):
                tag.decompose()
            paragraphs = [p.get_text(" ", strip=True) for p in container.find_all("p")]
            text = " ".join(p for p in paragraphs if p)
            if text:
                return re.sub(r"\s+", " ", text).strip()

    if "biomolecula.ru" in url:

        container = soup.find(

            "div",

            class_="mainbar__insert",

        )

        if container is None:

            return ""

        for tag in container.find_all(

            [

                "script",

                "style",

                "nav",

                "footer",

                "aside",

            ]

        ):

            tag.decompose()

        paragraphs: list[str] = []

        for paragraph in container.find_all("p"):

            text = paragraph.get_text(

                " ",

                strip=True,

            )

            if len(text) < 20:

                continue

            if re.match(

                r"^(рисунок|автор|спонсор)",

                text,

                re.IGNORECASE,

            ):

                continue

            paragraphs.append(text)

        text = " ".join(paragraphs)

        text = re.sub(

            r"\[\d+(?:\s*[,–-]\s*\d+)*\]",

            "",

            text,

        )

        text = re.sub(

            r"\s+",

            " ",

            text,

        )

        return text.strip()

    return ""

# ------------------------------------------------------------
def parse_article_response(response, url: str):
    """Пропускает не-HTML ответы и повреждённую разметку без остановки обхода."""
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type not in ("text/html", "application/xhtml+xml"):
        print(f"[skip] Не HTML ({content_type or 'нет Content-Type'}): {url}")
        return None
    try:
        clean_text = extract_clean_text(response.text, url)
        if not clean_text:
            print(f"[skip] Не удалось выделить текст статьи: {url}")
            return None
        title, author, publish_date = extract_metadata(response.text, url)
    except (ParserRejectedMarkup, AssertionError) as error:
        print(f"[parse] Некорректная разметка: {url}: {error}")
        return None
    return clean_text, title, author, publish_date

# Сохранение одного документа в PostgreSQL
# ------------------------------------------------------------
def store_document(

    conn,

    original_url: str,

    normalized_url: str,

    html: str,

    clean_text: str,

    title: str,

    author: str,

    publish_date: str | None,

    source: str,

    fetch_timestamp: int,

    last_modified: str | None,

    etag: str | None,

) -> None:

    """

    Сохраняет документ в PostgreSQL.

    """

    with conn.cursor() as cursor:

        cursor.execute(

            """

            INSERT INTO documents (

                url,

                normalized_url,

                html,

                clean_text,

                title,

                author,

                publish_date,

                source,

                fetch_timestamp,

                last_fetch_attempt,

                last_modified_header,

                etag_header

            )

            VALUES (

                %s, %s, %s, %s,

                %s, %s, %s, %s,

                %s, %s, %s, %s

            )

            ON CONFLICT (normalized_url)

            DO NOTHING;

            """,

            (

                original_url,

                normalized_url,

                html,

                clean_text,

                title,

                author,

                publish_date,

                source,

                fetch_timestamp,

                fetch_timestamp,

                last_modified,

                etag,

            ),

        )

    conn.commit()

# ------------------------------------------------------------
# Построение очереди URL из sitemap.
# После сбора URL источники чередуются для баланса корпуса.
# ------------------------------------------------------------
def collect_urls(

    config: dict,

    session: requests.Session,

) -> deque[tuple[str, str]]:

    """

    Собирает URL статей из sitemap

    и чередует документы разных источников.

    """

    logic = config["logic"]

    urls_by_source: list[tuple[str, list[str]]] = []

    for source in logic["sources"]:

        source_name = source["name"]

        source_urls: list[str] = []

        # set нужен для быстрого удаления дублей URL.
        seen: set[str] = set()

        for start_url in source["start_urls"]:

            print(

                f"\nЗагрузка sitemap: {start_url}"

            )

            root_urls = fetch_sitemap_urls(

                start_url,

                session,

            )

            for current_url in root_urls:

                if "sitemap" in current_url.lower():

                    candidates = fetch_sitemap_urls(

                        current_url,

                        session,

                    )

                else:

                    candidates = [current_url]

                for candidate in candidates:

                    normalized = normalize_url(

                        candidate

                    )

                    if normalized in seen:

                        continue

                    if not qualifies_as_article(

                        normalized

                    ):

                        continue

                    seen.add(normalized)

                    source_urls.append(normalized)

        print(

            f"{source_name}: найдено "

            f"{len(source_urls)} подходящих URL"

        )

        urls_by_source.append(

            (

                source_name,

                source_urls,

            )

        )

    # FIFO-очередь; в main элементы будут браться через popleft().
    queue: deque[tuple[str, str]] = deque()

    max_length = max(

        (

            len(urls)

            for _, urls in urls_by_source

        ),

        default=0,

    )

    for index in range(max_length):

        for source_name, urls in urls_by_source:

            if index < len(urls):

                queue.append(

                    (

                        urls[index],

                        source_name,

                    )

                )

    return queue

# ------------------------------------------------------------
# Сохранение состояния краулера
# ------------------------------------------------------------
def save_state(

    queue: deque[tuple[str, str]],

    saved_count: int,

) -> None:

    """

    Сохраняет состояние робота на диск.

    """

    state = {

        # deque сохраняем как обычный список.
        "queue": list(queue),

        "saved_count": saved_count,

    }

    temporary_state = STATE_FILE.with_suffix(".pkl.tmp")
    with temporary_state.open("wb") as file:

        pickle.dump(

            state,

            file,

        )

    temporary_state.replace(STATE_FILE)

# ------------------------------------------------------------
# Восстановление состояния краулера
# ------------------------------------------------------------
def load_state():

    """

    Восстанавливает состояние робота.

    """

    if not STATE_FILE.exists():

        return None

    with STATE_FILE.open("rb") as file:

        return pickle.load(file)

# ------------------------------------------------------------
# MD5-хеш полезного содержимого документа.
# HTML не включён, чтобы изменения рекламы/разметки не считались
# изменением самой статьи.
# ------------------------------------------------------------
def build_document_hash(

    title: str,

    author: str,

    publish_date: str | None,

    clean_text: str,

) -> str:

    """

    Строит хеш содержимого документа.

    """

    value = (

        f"{title}|"

        f"{author}|"

        f"{publish_date}|"

        f"{clean_text}"

    )

    return hashlib.md5(

        value.encode(

            "utf-8",

            errors="ignore",

        )

    ).hexdigest()

# ------------------------------------------------------------
# Повторная проверка сохранённых документов на изменения.
# ETag / Last-Modified -> 304 или повторный парсинг + сравнение хешей.
# ------------------------------------------------------------
def run_update_phase(

    conn,

    config: dict,

    session: requests.Session,

    should_stop=None,
    document_limit=None,

) -> None:

    """

    Проверяет сохранённые документы на изменения.

    """

    logic = config["logic"]

    delay_seconds = float(

        logic.get("delay_seconds", 1)

    )

    update_interval = int(

        logic.get(

            "update_interval_seconds",

            604800,

        )

    )

    # Текущее Unix-время в секундах.
    current_time = int(time.time())

    with conn.cursor() as cursor:

        cursor.execute(

            """

            SELECT

                url,

                normalized_url,

                clean_text,

                title,

                author,

                publish_date,

                last_modified_header,

                etag_header

            FROM documents

            WHERE last_fetch_attempt < %s

            ORDER BY last_fetch_attempt ASC, id ASC
            LIMIT %s;

            """,

            (

                current_time - update_interval,
                document_limit,

            ),

        )

        documents = cursor.fetchall()

    if not documents:

        print(

            "\nНет документов, "

            "требующих повторной проверки."

        )

        return

    print(

        f"\nПроверка обновлений: "

        f"{len(documents)} документов"

    )

    updated_count = 0

    for (

        original_url,

        normalized_url,

        old_clean_text,

        old_title,

        old_author,

        old_publish_date,

        old_last_modified,

        old_etag,

    ) in documents:

        if should_stop is not None and should_stop():
            print("\nПроверка обновлений остановлена.")
            break

        # Заголовки условного запроса: сервер может вернуть 304 без HTML.
        headers = {}

        if old_last_modified:

            headers[

                "If-Modified-Since"

            ] = old_last_modified

        if old_etag:

            headers[

                "If-None-Match"

            ] = old_etag

        print(

            f"[update] {normalized_url}"

        )

        try:

            response = request_with_retries(session,

                original_url,

                headers=headers,

                timeout=30,

                allow_redirects=True,

            )

        except requests.RequestException as error:

            print(

                f"[update] Ошибка запроса: "

                f"{error}"

            )

            with conn.cursor() as cursor:

                cursor.execute(

                    """

                    UPDATE documents

                    SET last_fetch_attempt = %s

                    WHERE normalized_url = %s;

                    """,

                    (

                        current_time,

                        normalized_url,

                    ),

                )

            conn.commit()

            time.sleep(delay_seconds)

            continue

        # 304 Not Modified: сервер подтвердил, что документ не изменился.
        if response.status_code == 304:

            print(

                "[update] Документ "

                "не изменился (304)."

            )

            with conn.cursor() as cursor:

                cursor.execute(

                    """

                    UPDATE documents

                    SET last_fetch_attempt = %s

                    WHERE normalized_url = %s;

                    """,

                    (

                        current_time,

                        normalized_url,

                    ),

                )

            conn.commit()

            time.sleep(delay_seconds)

            continue

        if response.status_code != 200:

            print(

                f"[update] HTTP "

                f"{response.status_code}"

            )

            with conn.cursor() as cursor:

                cursor.execute(

                    """

                    UPDATE documents

                    SET last_fetch_attempt = %s

                    WHERE normalized_url = %s;

                    """,

                    (

                        current_time,

                        normalized_url,

                    ),

                )

            conn.commit()

            time.sleep(delay_seconds)

            continue

        print("[update] HTTP 200: сравнение содержимого статьи.")
        final_url = normalize_url(

            response.url

        )

        if not qualifies_as_article(

            final_url

        ):

            print(

                "[update] После редиректа "

                "получена не статья."

            )

            time.sleep(delay_seconds)

            continue

        article = parse_article_response(response, final_url)
        if article is None:
            time.sleep(delay_seconds)
            continue
        new_clean_text, new_title, new_author, new_publish_date = article

        old_hash = build_document_hash(

            old_title or "",

            old_author or "",

            old_publish_date,

            old_clean_text or "",

        )

        new_hash = build_document_hash(

            new_title or "",

            new_author or "",

            new_publish_date,

            new_clean_text,

        )

        # При 200 сравниваем полезное содержимое локально по хешу.
        if old_hash == new_hash:

            print(

                "[update] Содержимое "

                "не изменилось."

            )

            with conn.cursor() as cursor:

                cursor.execute(

                    """

                    UPDATE documents

                    SET

                        last_fetch_attempt = %s,

                        last_modified_header = %s,

                        etag_header = %s

                    WHERE normalized_url = %s;

                    """,

                    (

                        current_time,

                        response.headers.get(

                            "Last-Modified"

                        ),

                        response.headers.get(

                            "ETag"

                        ),

                        normalized_url,

                    ),

                )

            conn.commit()

            time.sleep(delay_seconds)

            continue

        new_normalized_url = normalize_url(

            final_url

        )

        new_fetch_timestamp = int(

            time.time()

        )

        with conn.cursor() as cursor:

            cursor.execute(

                """

                UPDATE documents

                SET

                    url = %s,

                    normalized_url = %s,

                    html = %s,

                    clean_text = %s,

                    title = %s,

                    author = %s,

                    publish_date = %s,

                    source = %s,

                    fetch_timestamp = %s,

                    last_fetch_attempt = %s,

                    last_modified_header = %s,

                    etag_header = %s

                WHERE normalized_url = %s;

                """,

                (

                    original_url,

                    new_normalized_url,

                    response.text,

                    new_clean_text,

                    new_title,

                    new_author,

                    new_publish_date,

                    get_source_name(

                        final_url

                    ),

                    new_fetch_timestamp,

                    new_fetch_timestamp,

                    response.headers.get(

                        "Last-Modified"

                    ),

                    response.headers.get(

                        "ETag"

                    ),

                    normalized_url,

                ),

            )

        conn.commit()

        updated_count += 1

        print(

            "[update] Документ обновлён."

        )

        time.sleep(delay_seconds)

    print(

        f"\nПроверка завершена. "

        f"Обновлено документов: "

        f"{updated_count}"

    )

# ============================================================
# MAIN — главный сценарий работы робота.
# ============================================================
def refresh_queue(config, session, state):
    """Собирает ссылки заново, сохраняя резервную копию и остаток очереди."""
    if STATE_FILE.exists():
        backup = STATE_FILE.with_name(f"crawler_state.backup-{time.time_ns()}.pkl")
        shutil.copy2(STATE_FILE, backup)
        print(f"Резервная копия очереди: {backup}")
    discovered = collect_urls(config, session)
    queue = deque()
    seen = set()
    # Старые ссылки сохраняются даже при недоступности одного из sitemap.
    for url, source in list((state or {}).get("queue", [])) + list(discovered):
        url = normalize_url(url)
        if url not in seen and qualifies_as_article(url):
            seen.add(url)
            queue.append((url, source))
    return queue


def main(config_path: str, rescan: bool = False) -> None:

    config = load_config(config_path)

    logic = config["logic"]

    delay_seconds = float(

        logic.get("delay_seconds", 1)

    )

    max_documents = int(

        logic.get("max_documents", 100)

    )

    user_agent = logic.get(

        "user_agent",

        "MAI-InfoSearch",

    )

    respect_robots = bool(

        logic.get(

            "respect_robots_txt",

            True,

        )

    )

    conn = connect(config)

    create_tables(conn)

    # Одна Session переиспользуется для всех запросов и хранит User-Agent.
    session = requests.Session()

    session.headers.update(

        {

            "User-Agent": user_agent,

        }

    )

    # Если есть crawler_state.pkl, продолжаем предыдущий запуск.
    state = load_state()

    if rescan:
        saved_count = count_documents(conn)
        if saved_count < max_documents:
            print("Повторный сбор ссылок; сохранённые документы будут пропущены.")
            try:
                queue = refresh_queue(config, session, state)
                save_state(queue, saved_count)
            except BaseException:
                conn.close()
                raise
        else:
            queue = deque()

    elif state:

        print("Восстановление состояния...")

        queue = deque(

            (url, source) for url, source in state["queue"]
            if qualifies_as_article(url)

        )

        saved_count = count_documents(conn)
        removed_count = len(state["queue"]) - len(queue)
        print(f"Из сохранённой очереди исключено не-статей: {removed_count}")
        save_state(queue, saved_count)

    else:

        saved_count = count_documents(conn)

        if saved_count < max_documents:

            print(

                "Получение URL из sitemap..."

            )

            queue = collect_urls(

                config,

                session,

            )

        else:

            queue = deque()

    print(

        f"URL в очереди: {len(queue)}"

    )

    print(

        f"Документов уже сохранено: "

        f"{saved_count}"

    )

    # Флаг мягкой остановки по Ctrl+C.
    interrupted = False

    def handle_interrupt(

        _signal,

        _frame,

    ):

        nonlocal interrupted

        interrupted = True

        print(

            "\nПолучен Ctrl+C. "

            "Завершаю текущую операцию..."

        )

    signal.signal(

        signal.SIGINT,

        handle_interrupt,

    )

    try:

        while (

            queue

            and saved_count < max_documents

        ):

            if interrupted:

                break

            # Берём первый URL из FIFO-очереди.
            url, source_name = queue.popleft()

            if not qualifies_as_article(url):
                continue

            if document_exists(

                conn,

                url,

            ):

                continue

            if (

                respect_robots

                and not allowed_by_robots(

                    url,

                    user_agent,

                )

            ):

                print(

                    f"[robots] Запрещено: {url}"

                )

                continue

            print(

                f"[{saved_count + 1}/"

                f"{max_documents}] {url}"

            )

            try:

                response = request_with_retries(session,

                    url,

                    timeout=30,

                    allow_redirects=True,

                )

            except requests.RequestException as error:

                print(

                    f"[request] Ошибка: {error}"

                )

                queue.appendleft((url, source_name))
                print("Соединение не восстановилось. Очередь сохранится; повторите запуск позже.")
                return

            if response.status_code != 200:

                print(

                    f"[HTTP "

                    f"{response.status_code}] "

                    f"{url}"

                )

                continue

            final_url = normalize_url(

                response.url

            )

            if not qualifies_as_article(

                final_url

            ):

                print(

                    "Редирект ведёт не "

                    "на статью."

                )

                continue

            # Из HTML выделяем только полезный текст статьи.
            article = parse_article_response(response, final_url)
            if article is None:
                continue
            clean_text, title, author, publish_date = article

            timestamp = int(time.time())

            store_document(

                conn=conn,

                original_url=url,

                normalized_url=final_url,

                html=response.text,

                clean_text=clean_text,

                title=title,

                author=author,

                publish_date=publish_date,

                source=source_name,

                fetch_timestamp=timestamp,

                last_modified=response.headers.get(

                    "Last-Modified"

                ),

                etag=response.headers.get(

                    "ETag"

                ),

            )

            # Документ успешно сохранён.
            saved_count += 1

            save_state(

                queue,

                saved_count,

            )

            time.sleep(delay_seconds)

    finally:

        if interrupted:

            save_state(

                queue,

                saved_count,

            )

            print(

                "\nСостояние сохранено."

            )

        elif not queue or saved_count >= max_documents:

            if STATE_FILE.exists():

                STATE_FILE.unlink()

        else:
            # Сохраняем продвижение очереди и при неожиданной ошибке.
            save_state(queue, saved_count)

        conn.close()

    print(

        f"\nВсего документов в корпусе: "

        f"{saved_count}"

    )

    if not interrupted:

        update_conn = connect(config)

        try:

            run_update_phase(

                conn=update_conn,

                config=config,

                session=session,

                should_stop=lambda: interrupted,

            )

        finally:

            update_conn.close()

# ------------------------------------------------------------
# Точка входа:
# python lab02/crawler.py config.yaml
# ------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Скачивание корпуса статей")
    parser.add_argument("config", help="Путь к config.yaml")
    parser.add_argument("--rescan", action="store_true",
                        help="Повторно собрать sitemap и дополнить сохранённую очередь")
    parser.add_argument("--update-only", action="store_true", help="Только повторная проверка сохранённых статей, без обхода")
    parser.add_argument("--limit", type=int, default=3, help="Число статей в режиме --update-only")
    parser.add_argument("--update-interval", type=int, default=None, help="Интервал проверки в секундах только на этот запуск")
    args = parser.parse_args()
    if args.update_only:
        if args.limit < 1 or (args.update_interval is not None and args.update_interval < 0):
            parser.error("Лимит должен быть положительным, интервал — неотрицательным")
        config = load_config(args.config)
        if args.update_interval is not None:
            config['logic']['update_interval_seconds'] = args.update_interval
        conn = connect(config)
        try:
            with requests.Session() as session:
                session.headers['User-Agent'] = config['logic'].get('user_agent', 'MAI-InfoSearch/2026 educational crawler')
                print(f"Повторная проверка: максимум {args.limit} статей; интервал {config['logic'].get('update_interval_seconds', 604800)} с.")
                run_update_phase(conn, config, session, document_limit=args.limit)
        except KeyboardInterrupt:
            conn.rollback()
            print("\nПовторная проверка остановлена.")
        finally:
            conn.close()
    else:
        main(args.config, rescan=args.rescan)
