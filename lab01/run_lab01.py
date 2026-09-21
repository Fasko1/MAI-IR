import subprocess
import sys
from pathlib import Path


LAB_DIR = Path(__file__).resolve().parent
PROJECT_DIR = LAB_DIR.parent


def run_step(description: str, command: list[str]) -> None:
    print()
    print("=" * 70)
    print(description)
    print("=" * 70)

    result = subprocess.run(
        command,
        cwd=PROJECT_DIR,
    )

    if result.returncode != 0:
        print(f"\nОшибка на этапе: {description}")
        sys.exit(result.returncode)


def main() -> None:
    python = sys.executable

    print("Лабораторная работа №1")
    print("Добыча корпуса документов")

    run_step(
        "1. Обработка документа PMC",
        [python, "lab01/src/extract_pmc.py"],
    )

    run_step(
        "2. Обработка документа ERIC",
        [python, "lab01/src/extract_eric.py"],
    )

    run_step(
        "3. Расчёт статистики",
        [python, "lab01/src/statistics.py"],
    )

    run_step(
        "4. Запуск автотестов",
        [
            python,
            "-m",
            "unittest",
            "discover",
            "-s",
            "lab01/tests",
            "-v",
        ],
    )

    print()
    print("=" * 70)
    print("Все этапы лабораторной работы успешно выполнены.")
    print("=" * 70)


if __name__ == "__main__":
    main()