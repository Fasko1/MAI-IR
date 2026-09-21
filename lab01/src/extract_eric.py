import json
import re
from pathlib import Path

from pypdf import PdfReader


LAB_DIR = Path(__file__).resolve().parent.parent

PDF_FILE = LAB_DIR / "samples" / "eric" / "EJ854309.pdf"
METADATA_FILE = LAB_DIR / "samples" / "eric" / "EJ854309_metadata.json"
OUTPUT_FILE = LAB_DIR / "results" / "EJ854309.json"


def clean_text(text: str) -> str:
    """Удаляет лишние пробелы внутри строки."""
    return re.sub(r"[ \t]+", " ", text).strip()


def extract_pdf_text(pdf_path: Path) -> str:
    """Извлекает текст со всех страниц PDF."""
    reader = PdfReader(pdf_path)

    pages: list[str] = []

    for page in reader.pages:
        page_text = page.extract_text()

        if not page_text:
            continue

        lines = [
            clean_text(line)
            for line in page_text.splitlines()
            if clean_text(line)
        ]

        pages.append("\n".join(lines))

    return "\n\n".join(pages)


def main() -> None:
    if not PDF_FILE.exists():
        raise FileNotFoundError(f"PDF не найден: {PDF_FILE}")

    if not METADATA_FILE.exists():
        raise FileNotFoundError(
            f"Файл метаданных не найден: {METADATA_FILE}"
        )

    # Читаем метаданные ERIC
    with METADATA_FILE.open("r", encoding="utf-8") as file:
        metadata = json.load(file)

    # Извлекаем полный текст из PDF
    text = extract_pdf_text(PDF_FILE)

    document = {
        "id": metadata.get("id", ""),
        "source": "ERIC",
        "title": metadata.get("title", ""),
        "authors": metadata.get("authors", []),
        "publication_date": metadata.get("publication_date", ""),
        "keywords": metadata.get("descriptors", []),
        "abstract": metadata.get("abstract", ""),
        "text": text,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(
            document,
            file,
            ensure_ascii=False,
            indent=2,
        )

    print("Документ ERIC успешно обработан.")
    print(f"ID: {document['id']}")
    print(f"Название: {document['title']}")
    print(f"Авторов: {len(document['authors'])}")
    print(f"Ключевых слов: {len(document['keywords'])}")
    print(f"Длина текста: {len(text)} символов")
    print(f"Результат сохранён: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()