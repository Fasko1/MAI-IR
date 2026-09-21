import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path


LAB_DIR = Path(__file__).resolve().parent.parent

INPUT_FILE = LAB_DIR / "samples" / "pmc" / "PMC8935530.xml"
OUTPUT_FILE = LAB_DIR / "results" / "PMC8935530.json"


def clean_text(text: str) -> str:
    """Удаляет лишние пробелы и переводы строк."""
    return re.sub(r"\s+", " ", text).strip()


def element_text(element: ET.Element | None) -> str:
    """Получает весь текст внутри XML-элемента."""
    if element is None:
        return ""

    return clean_text(" ".join(element.itertext()))


def main() -> None:
    tree = ET.parse(INPUT_FILE)
    root = tree.getroot()

    # В XML используются пространства имён,
    # поэтому {*} позволяет найти тег независимо от namespace.
    article = root.find(".//{*}article")

    if article is None:
        raise ValueError("Тег <article> не найден")

    article_meta = article.find(".//{*}article-meta")

    if article_meta is None:
        raise ValueError("Тег <article-meta> не найден")

    # PMCID
    pmcid = ""
    for article_id in article_meta.findall("{*}article-id"):
        if article_id.attrib.get("pub-id-type") == "pmcid":
            pmcid = element_text(article_id)
            break

    # Название
    title_element = article_meta.find(".//{*}article-title")
    title = element_text(title_element)

    # Авторы
    authors: list[str] = []

    for contrib in article_meta.findall(".//{*}contrib"):
        if contrib.attrib.get("contrib-type") != "author":
            continue

        given_names = element_text(contrib.find(".//{*}given-names"))
        surname = element_text(contrib.find(".//{*}surname"))

        full_name = clean_text(f"{given_names} {surname}")

        if full_name:
            authors.append(full_name)

    # Дата публикации.
    # Сначала пытаемся использовать электронную дату.
    publication_date = ""

    pub_dates = article_meta.findall("{*}pub-date")

    selected_date = None

    for pub_date in pub_dates:
        if pub_date.attrib.get("pub-type") == "epub":
            selected_date = pub_date
            break

    if selected_date is None and pub_dates:
        selected_date = pub_dates[0]

    if selected_date is not None:
        year = element_text(selected_date.find("{*}year"))
        month = element_text(selected_date.find("{*}month"))
        day = element_text(selected_date.find("{*}day"))

        publication_date = "-".join(
            part for part in (year, month, day) if part
        )

    # Ключевые слова
    keywords = [
        element_text(keyword)
        for keyword in article_meta.findall(".//{*}kwd")
        if element_text(keyword)
    ]

    # Аннотация
    abstract_element = article_meta.find(".//{*}abstract")
    abstract = element_text(abstract_element)

    # Основной текст статьи
    body = article.find("{*}body")

    body_parts: list[str] = []

    if body is not None:
        for element in body.iter():
            tag = element.tag.split("}")[-1]

            if tag in {"title", "p"}:
                text = element_text(element)

                if text:
                    body_parts.append(text)

    body_text = "\n\n".join(body_parts)

    document = {
        "id": pmcid,
        "source": "PMC",
        "title": title,
        "authors": authors,
        "publication_date": publication_date,
        "keywords": keywords,
        "abstract": abstract,
        "text": body_text,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(
            document,
            file,
            ensure_ascii=False,
            indent=2,
        )

    print("Документ успешно обработан.")
    print(f"PMCID: {pmcid}")
    print(f"Название: {title}")
    print(f"Авторов: {len(authors)}")
    print(f"Ключевых слов: {len(keywords)}")
    print(f"Длина основного текста: {len(body_text)} символов")
    print(f"Результат сохранён: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()