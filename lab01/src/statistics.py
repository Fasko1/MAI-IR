import json
from pathlib import Path


LAB_DIR = Path(__file__).resolve().parent.parent

DOCUMENTS = [
    {
        "name": "PMC8935530",
        "raw": LAB_DIR / "samples" / "pmc" / "PMC8935530.xml",
        "processed": LAB_DIR / "results" / "PMC8935530.json",
    },
    {
        "name": "EJ854309",
        "raw": LAB_DIR / "samples" / "eric" / "EJ854309.pdf",
        "processed": LAB_DIR / "results" / "EJ854309.json",
    },
]


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"

    if size < 1024 ** 2:
        return f"{size / 1024:.2f} KB"

    return f"{size / (1024 ** 2):.2f} MB"


def main() -> None:
    total_raw_size = 0
    total_text_size = 0

    print("Статистика примеров корпуса\n")

    for document in DOCUMENTS:
        raw_size = document["raw"].stat().st_size

        with document["processed"].open(
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        text = data.get("text", "")
        text_size = len(text.encode("utf-8"))

        total_raw_size += raw_size
        total_text_size += text_size

        print(document["name"])
        print(f"  Размер сырого документа: {format_size(raw_size)}")
        print(f"  Размер выделенного текста: {format_size(text_size)}")
        print(f"  Символов текста: {len(text)}")
        print(f"  Слов: {len(text.split())}")
        print()

    count = len(DOCUMENTS)

    print("Итого")
    print(f"  Количество примеров: {count}")
    print(f"  Общий размер raw: {format_size(total_raw_size)}")
    print(f"  Общий размер текста: {format_size(total_text_size)}")
    print(
        f"  Средний размер raw: "
        f"{format_size(total_raw_size // count)}"
    )
    print(
        f"  Средний размер текста: "
        f"{format_size(total_text_size // count)}"
    )


if __name__ == "__main__":
    main()