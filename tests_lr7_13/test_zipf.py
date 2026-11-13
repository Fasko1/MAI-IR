# Пояснение к файлу: CLI-проверки C++ частотного счётчика.
# Как читать: Контрольные термины → ожидаемые CF/порядок → проверка freq.txt/stats.json; отдельно проверяются некорректные входы.
# Команды запуска и структура проекта: README.md.

"""Независимые CLI-тесты C++ подсчёта частот ЛР5. Только Python stdlib.

Пример: python tests_lr7_13/test_zipf.py --engine build/labs_3_5/zipf_count.exe
Эталон — явно заданные частоты и Counter, без чтения исходников программы.
Временные входы создаются tempfile внутри build/tests_zipf и удаляются после теста.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import tempfile
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
CASES = []


def case(name, basis):
    def register(function):
        CASES.append((name, basis, function))
        return function
    return register


class Checks:
    def __init__(self, engine, work):
        self.engine, self.work = engine.resolve(), work.resolve()
        self.work.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory(prefix="zipf_", dir=self.work) as directory:
            yield Path(directory)

    def invoke(self, *arguments, success=True):
        result = subprocess.run([str(self.engine), *(str(arg) for arg in arguments)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=ROOT, timeout=30)
        if success:
            assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
        else:
            assert result.returncode != 0, "Неверный вход должен завершаться ненулевым кодом"
        return result

    def run(self, directory, documents, *, list_body=None, success=True):
        paths = []
        for index, data in enumerate(documents):
            path = directory / f"документ {index}.tokens"
            path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
            paths.append(path)
        listing = directory / "inputs.txt"
        listing.write_bytes(list_body if list_body is not None else
                            ("\n".join(str(path) for path in paths) + "\n").encode("utf-8"))
        freq, stats = directory / "freq.txt", directory / "stats.json"
        self.invoke(listing, freq, stats, success=success)
        if not success:
            return None
        rows = []
        for line in freq.read_text(encoding="utf-8").splitlines():
            count, term = line.split(" ", 1)
            rows.append((term, int(count)))
        return rows, json.loads(stats.read_text(encoding="utf-8"))

    @staticmethod
    def expect(result, frequencies, document_count):
        rows, stats = result
        expected = sorted(frequencies.items(), key=lambda item: (-item[1], item[0]))
        assert rows == expected, (rows[:20], expected[:20])
        assert stats["documents"] == document_count, stats
        assert stats["total_terms"] == sum(frequencies.values()), stats
        assert stats["unique_terms"] == len(frequencies), stats
        assert stats["top20"] == [[term, count] for term, count in expected[:20]], stats
        assert stats["plotted_ranks"] == min(len(frequencies), 10000), stats


@case("Z01 corpus_occurrences", "ЛР5: считаются все вхождения, включая повторы внутри одного документа")
def corpus_occurrences(checks):
    with checks.fixture() as directory:
        result = checks.run(directory, ["кот\nкот\nдом\n", "дом\nсад\nкот\n", ""])
        checks.expect(result, {"кот": 3, "дом": 2, "сад": 1}, 3)


@case("Z02 frequency_and_tie_order", "ЛР5: частота по убыванию, при равенстве — термин по Unicode")
def frequency_and_tie_order(checks):
    with checks.fixture() as directory:
        checks.expect(checks.run(directory, ["яя\nба\na\n007\n7\na\n"]),
                      {"a": 2, "яя": 1, "ба": 1, "007": 1, "7": 1}, 1)


@case("Z03 utf8_whitespace", "Границы строк обрезаются по Unicode-пробелам; внутренние пробелы сохраняются")
def utf8_whitespace(checks):
    with checks.fixture() as directory:
        result = checks.run(directory, [" \tкот\t \n\u00a0кот\u2003\n\u3000\nдве части\nдве  части\n"])
        checks.expect(result, {"кот": 2, "две части": 1, "две  части": 1}, 1)


@case("Z04 line_endings", "ЛР5: LF, CRLF и CR одинаково отделяют вхождения")
def line_endings(checks):
    with checks.fixture() as directory:
        checks.expect(checks.run(directory, ["кот\r\nдом\rсад\nкот\r\n"]),
                      {"кот": 2, "дом": 1, "сад": 1}, 1)


@case("Z05 final_line_without_lf", "ЛР5: последнее вхождение без завершающего LF не теряется")
def final_line_without_lf(checks):
    with checks.fixture() as directory:
        checks.expect(checks.run(directory, ["кот\nдом", "кот"]), {"кот": 2, "дом": 1}, 2)


@case("Z06 empty_lines", "ЛР5: пустые строки и строки из пробелов не создают пустой термин")
def empty_lines(checks):
    with checks.fixture() as directory:
        checks.expect(checks.run(directory, ["\n \t\nкот\n\n", "\r\n\n"]), {"кот": 1}, 2)


@case("Z07 input_list_format", "Пустые строки списка, CRLF и последний путь без LF поддерживаются")
def input_list_format(checks):
    with checks.fixture() as directory:
        first, second = directory / "документ 0.tokens", directory / "документ 1.tokens"
        listing = f"\r\n{first}\r\n\r\n{second}".encode("utf-8")
        checks.expect(checks.run(directory, ["кот\n", "дом\n"], list_body=listing), {"кот": 1, "дом": 1}, 2)


@case("Z08 long_term", "Собственный буфер растёт; корректная UTF-8 строка длиннее начальной ёмкости не обрезается")
def long_term(checks):
    with checks.fixture() as directory:
        term = "я" * 4096
        checks.expect(checks.run(directory, [term + "\n" + term]), {term: 2}, 1)


@case("Z09 table_growth", "Рехеширование и сортировка не теряют ключи при росте словаря")
def table_growth(checks):
    terms = [f"термин{index:05d}" for index in range(1800)]
    # Детализация синтетическая: здесь тестируется счётчик, а не правила токенизации.
    documents = ["\n".join(terms) + "\n", "\n".join(terms[::2]), "\n".join(terms[::5])]
    reference = Counter(term for document in documents for term in document.splitlines())
    with checks.fixture() as directory:
        checks.expect(checks.run(directory, documents), reference, len(documents))


@case("Z10 invalid_utf8", "Общий контракт UTF-8: обрыв, overlong, surrogate и неверные continuation отклоняются")
def invalid_utf8(checks):
    for invalid in (b"\xd0", b"\xc0\x80", b"\xed\xa0\x80", b"\xf0\x80\x80\x80", b"\xd0x"):
        with checks.fixture() as directory:
            checks.run(directory, [invalid], success=False)


@case("Z11 nul", "NUL не должен тихо обрезать строку или объединять разные термины")
def nul(checks):
    with checks.fixture() as directory:
        checks.run(directory, [b"term\0other\n"], success=False)


@case("Z12 missing_input", "Отсутствующий входной документ — ошибка, не молчаливое занижение частот")
def missing_input(checks):
    with checks.fixture() as directory:
        listing = directory / "inputs.txt"
        listing.write_text(str(directory / "absent.tokens") + "\n", encoding="utf-8")
        checks.invoke(listing, directory / "freq.txt", directory / "stats.json", success=False)


@case("Z13 empty_corpus", "Частоты и модель Ципфа не определены для корпуса без вхождений")
def empty_corpus(checks):
    for documents in ([], [""], [" \n\t\r\n"]):
        with checks.fixture() as directory:
            checks.run(directory, documents, success=False)


@case("Z14 missing_list", "Отсутствующий список входов диагностируется")
def missing_list(checks):
    with checks.fixture() as directory:
        checks.invoke(directory / "absent.txt", directory / "freq.txt", directory / "stats.json", success=False)


@case("Z15 output_error", "Ошибка создания выхода возвращает ненулевой код")
def output_error(checks):
    with checks.fixture() as directory:
        source, listing = directory / "source.tokens", directory / "inputs.txt"
        source.write_text("кот\n", encoding="utf-8")
        listing.write_text(str(source) + "\n", encoding="utf-8")
        checks.invoke(listing, directory / "absent_folder" / "freq.txt", directory / "stats.json", success=False)


@case("Z16 arguments", "Число аргументов CLI проверяется до начала обработки")
def arguments(checks):
    checks.invoke(success=False)
    checks.invoke("one", "two", success=False)
    checks.invoke("one", "two", "three", "four", success=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, default=ROOT / "build/labs_3_5/zipf_count.exe")
    parser.add_argument("--work", type=Path, default=ROOT / "build/tests_zipf")
    parser.add_argument("--results", type=Path, default=ROOT / "tests_lr7_13/results")
    args = parser.parse_args()
    checks = Checks(args.engine, args.work)
    args.results.mkdir(parents=True, exist_ok=True)
    rows = []
    started = time.perf_counter()
    for name, basis, function in CASES:
        start = time.perf_counter()
        try:
            function(checks)
            status, detail = "PASS", ""
        except Exception as error:
            status, detail = "FAIL", str(error) or traceback.format_exc()
        row = {"name": name, "basis": basis, "status": status, "detail": detail,
               "seconds": time.perf_counter() - start}
        rows.append(row)
        print(f"{status} {name}" + (f": {detail[:500]}" if detail else ""), flush=True)
    passed = sum(row["status"] == "PASS" for row in rows)
    result = {"suite": "ZIPF_CLI", "cases": len(rows), "passed": passed, "failed": len(rows) - passed,
              "seconds": time.perf_counter() - started, "test_cases": rows}
    (args.results / "zipf_test_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f"ZIPF_CLI: cases={len(rows)} PASS={passed} FAIL={len(rows) - passed}"]
    for row in rows:
        lines.extend([f"\n{row['status']} {row['name']}", f"Основание: {row['basis']}"])
        if row["detail"]:
            lines.append(f"Ошибка: {row['detail']}")
    (args.results / "zipf_test_results.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[0], flush=True)
    return int(result["failed"] != 0)


if __name__ == "__main__":
    raise SystemExit(main())
