# Пояснение к файлу: Сквозные проверки настоящего CLI индекса и поиска.
# Как читать: Малые временные корпуса → build/compress/search/verify → независимые ожидаемые множества и TF-IDF. Проверяются также повреждённые входы.
# Команды запуска и структура проекта: README.md.

"""Black-box проверки ЛР7/8/11/13. Эталон: множества и формулы из описания.

Запуск: python tests_lr7_13/test_cli.py --engine build/ir_engine.exe
Сторонние библиотеки, PostgreSQL и настоящий корпус не нужны.
Каждая проверка сохраняет свой результат и основание ожидаемого ответа.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import random
import subprocess
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]


class Checks:
    def __init__(self, engine: Path, work: Path):
        self.engine, self.work = engine.resolve(), work.resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        self.documents = {
            "90": ["кот", "кот", "дом", "мир"],
            "007": ["кот", "сад", "мир"],
            "20": ["дом", "дом", "сад", "мир", "луч"],
            "b": ["сад", "мир"],
            "a": ["кот", "дом", "сад", "мир"],
            "404": ["кот", "кот", "дом", "мир"],
            "x": ["мир"],
            "empty": [],
        }
        self.universe = set(self.documents)
        self.postings = {
            term: {doc_id for doc_id, terms in self.documents.items() if term in terms}
            for terms in self.documents.values() for term in terms
        }
        self.metadata = {
            doc_id: {"title": f'Документ «{doc_id}»: "кот" <дом> & сад',
                     "url": f"https://example.invalid/articles/{doc_id}?x=1&y=2"}
            for doc_id in self.documents
        }
        self.manifest = self.work / "вход с пробелами" / "manifest.tsv"
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for index, (doc_id, terms) in enumerate(self.documents.items()):
            source = self.manifest.parent / f"документ_{index}.tokens"
            # Одна строка — вхождение. Смешаны LF, CRLF и последний токен без LF.
            separator = "\r\n" if index % 2 else "\n"
            body = separator.join(terms) + (separator if terms and index % 2 else "")
            source.write_bytes(body.encode("utf-8"))
            meta = self.metadata[doc_id]
            rows.append(f"{doc_id}\t{source}\t{meta['title']}\t{meta['url']}")
        self.manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
        self.raw, self.vb = self.work / "raw.idx", self.work / "compressed.idx"
        self.invoke("build", "--manifest", self.manifest, "--index", self.raw)
        self.invoke("compress", "--input", self.raw, "--output", self.vb)

    def invoke(self, *arguments, text=None, success=True, timeout=45):
        result = subprocess.run(
            [str(self.engine), *(str(value) for value in arguments)],
            input=text.encode("utf-8") if text is not None else None,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT, timeout=timeout,
        )
        if success:
            assert result.returncode == 0, (
                f"{arguments}: exit={result.returncode}; "
                f"stderr={result.stderr.decode('utf-8', errors='replace')[:1000]}"
            )
        else:
            assert result.returncode != 0, f"Команда должна отклоняться: {arguments}"
        return result

    def search(self, query, *, ranked=False, index=None, limit=1000, offset=0):
        args = ["search", "--index", index or self.raw,
                "--limit", limit, "--offset", offset, "--query", query]
        if ranked:
            args.append("--ranked")
        result = self.invoke(*args)
        lines = result.stdout.decode("utf-8").splitlines()
        assert len(lines) == 1, f"На запрос нужна одна JSON-строка: {lines[:3]}"
        value = json.loads(lines[0])
        assert "error" not in value, value
        self.validate_response(value)
        return value

    @staticmethod
    def validate_response(value):
        assert isinstance(value, dict) and isinstance(value["results"], list), value
        assert isinstance(value["count"], int) and value["count"] >= 0, value
        ids = [str(row["id"]) for row in value["results"]]
        assert len(ids) == len(set(ids)), f"Повтор docID в выдаче: {ids}"
        assert len(ids) <= value["count"], value
        for row in value["results"]:
            assert isinstance(row["title"], str) and isinstance(row["url"], str), row
            assert math.isfinite(row["score"]), row

    @staticmethod
    def ids(value):
        return [str(row["id"]) for row in value["results"]]

    def expect_set(self, query, expected, **kwargs):
        value = self.search(query, **kwargs)
        assert set(self.ids(value)) == set(expected), (query, value, sorted(expected))
        assert value["count"] == len(expected), (query, value["count"], len(expected))
        return value

    def stream(self, queries, *, ranked=False, index=None):
        args = ["search", "--index", index or self.raw, "--limit", "1000"]
        if ranked:
            args.append("--ranked")
        result = self.invoke(*args, text="\n".join(queries) + "\n")
        values = [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]
        assert len(values) == len(queries), (len(values), len(queries))
        return values

    def cosine(self, positive_terms, candidates):
        # Независимый математический эталон: строит векторы из целого корпуса,
        # не читает бинарный индекс и не повторяет обход постингов поисковой системы.
        frequencies = {key: Counter(value) for key, value in self.documents.items()}
        document_count = len(self.documents)
        idf = {term: math.log1p(document_count / len(ids))
               for term, ids in self.postings.items()}
        weights = {
            doc_id: {term: (1 + math.log(freq)) * idf[term]
                     for term, freq in counts.items()}
            for doc_id, counts in frequencies.items()
        }
        query_counts = Counter(term for term in positive_terms if term in idf)
        query = {term: (1 + math.log(freq)) * idf[term]
                 for term, freq in query_counts.items()}
        query_norm = math.sqrt(sum(value * value for value in query.values()))
        expected = {}
        for doc_id in candidates:
            vector = weights[doc_id]
            norm = math.sqrt(sum(value * value for value in vector.values()))
            product = sum(vector.get(term, 0) * value for term, value in query.items())
            expected[doc_id] = product / (norm * query_norm) if norm and query_norm else 0.0
        return expected

    def expect_scores(self, query, terms, candidates, *, index=None):
        value = self.expect_set(query, candidates, ranked=True, index=index)
        expected = self.cosine(terms, candidates)
        wanted = sorted(candidates, key=lambda doc_id: (-expected[doc_id], doc_id))
        assert self.ids(value) == wanted, (query, self.ids(value), wanted)
        for row in value["results"]:
            reference = expected[str(row["id"])]
            assert math.isclose(row["score"], reference, rel_tol=1e-8, abs_tol=1e-10), (
                query, row["id"], row["score"], reference
            )


CASES = []


def case(name, basis):
    def register(function):
        CASES.append((name, basis, function))
        return function
    return register


@case("B01 binary_roundtrip", "ЛР7: собственный бинарный индекс и загрузка новым процессом")
def binary_roundtrip(checks):
    data = checks.raw.read_bytes()
    assert b"\0" in data[:128], "Индекс должен содержать бинарные числовые поля"
    checks.expect_set("кот", checks.postings["кот"])


@case("B02 direct_index", "ЛР7: title и URL доступны из индекса; дополнение включает пустой документ")
def direct_index(checks):
    value = checks.expect_set("!нетермин", checks.universe)
    assert checks.ids(value) == sorted(checks.universe)
    for row in value["results"]:
        meta = checks.metadata[str(row["id"])]
        assert row["title"] == meta["title"], row
        assert row["url"] == meta["url"], row


@case("B03 repetitions", "ЛР7: повтор термина увеличивает tf, но не дублирует документ в булевом ответе")
def repetitions(checks):
    checks.expect_set("кот кот", checks.postings["кот"])
    checks.expect_set("кот && кот", checks.postings["кот"])


@case("Q01 intersection", "ЛР8: пробел и && означают пересечение в булевом режиме")
def intersection(checks):
    expected = checks.postings["кот"] & checks.postings["дом"]
    checks.expect_set("кот дом", expected)
    checks.expect_set("кот && дом", expected)


@case("Q02 union", "ЛР8: || означает объединение, неизвестный термин — пустое множество")
def union(checks):
    checks.expect_set("кот || сад", checks.postings["кот"] | checks.postings["сад"])
    checks.expect_set("кот || нетермин", checks.postings["кот"])


@case("Q03 complement", "ЛР8: ! — дополнение относительно всех документов, включая пустые")
def complement(checks):
    checks.expect_set("!кот", checks.universe - checks.postings["кот"])
    checks.expect_set("!!кот", checks.postings["кот"])
    checks.expect_set("!(кот || дом)", checks.universe - (checks.postings["кот"] | checks.postings["дом"]))


@case("Q04 precedence", "ЛР8: приоритет !, затем &&, затем ||; скобки меняют порядок")
def precedence(checks):
    cat, house, garden = (checks.postings[t] for t in ("кот", "дом", "сад"))
    checks.expect_set("кот || дом && !сад", cat | (house - garden))
    checks.expect_set("(кот || дом) && !сад", (cat | house) - garden)
    checks.expect_set("кот !(дом || сад)", cat - (house | garden))


@case("Q05 adjacency", "ЛР8: неявное И между терминами, скобками и отрицанием")
def adjacency(checks):
    cat, house, garden = (checks.postings[t] for t in ("кот", "дом", "сад"))
    checks.expect_set("(кот || дом)(сад)", (cat | house) & garden)
    checks.expect_set("кот!сад", cat - garden)


@case("Q06 whitespace", "ЛР8: переменное число пробелов и табуляция не меняют смысл")
def whitespace(checks):
    expected = checks.postings["кот"] & checks.postings["дом"]
    for query in ("  кот   дом  ", "кот\tдом", " кот \t && \t дом "):
        checks.expect_set(query, expected)


@case("Q07 normalization", "ЛР7/8: общая нормализация индекса и запроса, регистр и словоформы")
def normalization(checks):
    checks.expect_set("КОТ", checks.postings["кот"])
    checks.expect_set("коты", checks.postings["кот"])
    checks.expect_set("дома", checks.postings["дом"])


@case("Q08 keyword_operators", "Документированное расширение языка: AND/OR/NOT эквивалентны символам")
def keyword_operators(checks):
    checks.expect_set("кот AND NOT сад", checks.postings["кот"] - checks.postings["сад"])
    checks.expect_set("кот OR дом", checks.postings["кот"] | checks.postings["дом"])


@case("Q09 unknown_terms", "ЛР8: неизвестные термины и их отрицание подчиняются законам множеств")
def unknown_terms(checks):
    checks.expect_set("нетермин", set())
    checks.expect_set("кот && нетермин", set())
    checks.expect_set("кот !нетермин", checks.postings["кот"])


@case("Q10 blank_queries", "Пустой запрос не должен неожиданно возвращать весь корпус")
def blank_queries(checks):
    for query in ("", "   ", "\t"):
        checks.expect_set(query, set())


@case("Q11 syntax_errors_stream", "ЛР8/17: неверный запрос диагностируется; следующая строка обрабатывается")
def syntax_errors_stream(checks):
    malformed = ["(", "кот &&", "|| кот", "кот || || дом", "кот)", "!", "()"]
    queries = [part for bad in malformed for part in (bad, "кот")]
    values = checks.stream(queries)
    for position, value in enumerate(values):
        if position % 2 == 0:
            assert value.get("error"), (queries[position], value)
        else:
            checks.validate_response(value)
            assert set(checks.ids(value)) == checks.postings["кот"], value


@case("Q12 generated_expression_oracle", "ЛР17: детерминированные выражения; независимый эталон — операции Python set")
def generated_expression_oracle(checks):
    generator = random.Random(20261023)
    terms = ["кот", "дом", "сад", "луч", "мир", "нетермин"]

    def expression(depth):
        if depth == 0 or generator.random() < 0.25:
            term = generator.choice(terms)
            return term, checks.postings.get(term, set())
        operation = generator.choice(["!", "&&", "||"])
        left_text, left_set = expression(depth - 1)
        if operation == "!":
            return f"!({left_text})", checks.universe - left_set
        right_text, right_set = expression(depth - 1)
        wanted = left_set & right_set if operation == "&&" else left_set | right_set
        return f"({left_text} {operation} {right_text})", wanted

    examples = [expression(4) for _ in range(80)]
    results = checks.stream([query for query, _ in examples])
    for (query, wanted), value in zip(examples, results):
        assert "error" not in value, (query, value)
        checks.validate_response(value)
        assert set(checks.ids(value)) == wanted, (query, value, sorted(wanted))
        assert value["count"] == len(wanted), (query, value)


@case("R01 soft_plain_query", "ЛР13: обычный запрос через пробелы допускает совпадение части терминов")
def soft_plain_query(checks):
    candidates = checks.postings["кот"] | checks.postings["дом"]
    checks.expect_scores("кот дом", ["кот", "дом"], candidates)


@case("R02 strict_boolean_query", "ЛР13: явный && сохраняет строгое соответствие перед ранжированием")
def strict_boolean_query(checks):
    candidates = checks.postings["кот"] & checks.postings["дом"]
    checks.expect_scores("кот && дом", ["кот", "дом"], candidates)


@case("R03 tf_idf_arithmetic", "ЛР13: веса (1+ln tf)*ln(1+N/df), L2-нормы и косинус считаются независимо")
def tf_idf_arithmetic(checks):
    for terms in (["кот"], ["луч", "мир"], ["кот", "сад", "мир"]):
        candidates = set().union(*(checks.postings[term] for term in terms))
        checks.expect_scores(" ".join(terms), terms, candidates)


@case("R04 repeated_query_terms", "ЛР13: повтор в запросе изменяет его tf, повтор не создаёт лишний результат")
def repeated_query_terms(checks):
    terms = ["кот", "кот", "дом"]
    checks.expect_scores(" ".join(terms), terms, checks.postings["кот"] | checks.postings["дом"])


@case("R05 negative_term_weights", "ЛР13: отрицательные термины фильтруют, но не добавляются в положительный вектор")
def negative_term_weights(checks):
    checks.expect_scores("кот && !сад", ["кот"], checks.postings["кот"] - checks.postings["сад"])
    checks.expect_scores("!кот", [], checks.universe - checks.postings["кот"])
    checks.expect_scores("!(кот || дом)", [], checks.universe - (checks.postings["кот"] | checks.postings["дом"]))


@case("R06 unseen_query_term", "ЛР13: отсутствующий в словаре термин не меняет веса известных терминов")
def unseen_query_term(checks):
    checks.expect_scores("кот нетермин", ["кот"], checks.postings["кот"])


@case("R07 ties", "ЛР13: равные векторы имеют равные scores; ничья разрешается лексикографическим ID")
def ties(checks):
    value = checks.search("кот дом", ranked=True)
    rows = {str(row["id"]): row for row in value["results"]}
    assert rows["404"]["score"] == rows["90"]["score"]
    assert checks.ids(value).index("404") < checks.ids(value).index("90")


@case("R08 hand_calculated_cosine", "ЛР13: три документа, df=2, численные ответы вычислены вручную")
def hand_calculated_cosine(checks):
    directory = checks.work / "manual_cosine"
    directory.mkdir(exist_ok=True)
    rows = []
    for doc_id, text in (("A", "кот\nкот\nдом\n"), ("B", "кот\n"), ("C", "дом\n")):
        source = directory / f"{doc_id}.tokens"
        source.write_text(text, encoding="utf-8")
        rows.append(f"{doc_id}\t{source}")
    manifest, index = directory / "manifest.tsv", directory / "manual.idx"
    manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
    checks.invoke("build", "--manifest", manifest, "--index", index)
    value = checks.search("кот дом", ranked=True, index=index)
    assert checks.ids(value) == ["A", "B", "C"], value
    # N=3, df(кот)=df(дом)=2 => общий множитель ln(2.5) сокращается.
    # A: ((1+ln2)+1)/sqrt(2*((1+ln2)^2+1)); B=C=1/sqrt2.
    expected = {"A": 0.9684388219443362, "B": 0.7071067811865475, "C": 0.7071067811865475}
    for row in value["results"]:
        assert math.isclose(row["score"], expected[str(row["id"])], rel_tol=1e-8), row


@case("Q13 pagination", "ЛР8: offset/limit меняют страницу, общее count сохраняется")
def pagination(checks):
    complete = checks.search("!нетермин")
    ids = checks.ids(complete)
    for offset, limit in ((0, 2), (2, 3), (7, 5), (8, 4), (100, 10)):
        page = checks.search("!нетермин", offset=offset, limit=limit)
        assert checks.ids(page) == ids[offset:offset + limit], page
        assert page["count"] == len(ids) and page["offset"] == offset, page


@case("C01 lossless_search", "ЛР11: все ID, метаданные, порядок и scores совпадают до/после сжатия")
def lossless_search(checks):
    queries = ["кот", "мир", "луч", "!кот", "кот дом", "кот || дом",
               "кот && !сад", "!(кот || дом)", "кот кот сад", "нетермин", ""]
    for ranked in (False, True):
        raw = checks.stream(queries, ranked=ranked, index=checks.raw)
        compressed = checks.stream(queries, ranked=ranked, index=checks.vb)
        for query, a, b in zip(queries, raw, compressed):
            for key in ("query", "count", "offset", "results"):
                assert a[key] == b[key], (query, ranked, key, a, b)


@case("C02 direct_compressed_build", "ЛР11: прямая сборка с --compressed эквивалентна упаковке готового индекса")
def direct_compressed_build(checks):
    destination = checks.work / "built_compressed.idx"
    checks.invoke("build", "--manifest", checks.manifest, "--index", destination, "--compressed")
    for query in ("кот дом", "!сад", "луч мир"):
        raw = checks.search(query, ranked=True)
        packed = checks.search(query, ranked=True, index=destination)
        assert raw["count"] == packed["count"] and raw["results"] == packed["results"]


@case("C03 verify_and_stats", "ЛР7/11: полный проход проверяет целостность каждого представления индекса")
def verify_and_stats(checks):
    for index in (checks.raw, checks.vb):
        for command in ("verify", "stats"):
            value = json.loads(checks.invoke(command, "--index", index).stdout.decode("utf-8"))
            assert value["verified"] is True and value["documents"] == 8, value
            assert value["terms"] == 5 and value["postings"] == 20 and value["tokens"] == 23, value
            assert value["compressed"] == (index == checks.vb), value
            assert value["bytes"] == index.stat().st_size, value
            assert 96 + value["document_bytes"] + value["dictionary_bytes"] + value["posting_bytes"] == value["bytes"], value


@case("E01 wrong_magic", "ЛР7/17: случайный или чужой файл отклоняется с ненулевым кодом")
def wrong_magic(checks):
    destination = checks.work / "wrong_magic.idx"
    data = bytearray(checks.raw.read_bytes())
    data[:4] = b"NOPE"
    destination.write_bytes(data)
    checks.invoke("search", "--index", destination, "--query", "кот", success=False)


@case("E06 version_and_checksum", "ЛР7/11: неизвестная версия и повреждение полезных данных отклоняются")
def version_and_checksum(checks):
    for index in (checks.raw, checks.vb):
        for name, position in (("version", 8), ("payload", index.stat().st_size - 1)):
            destination = checks.work / f"bad_{index.stem}_{name}.idx"
            data = bytearray(index.read_bytes())
            data[position] ^= 2
            destination.write_bytes(data)
            checks.invoke("verify", "--index", destination, success=False)


@case("E02 truncated_indices", "ЛР11/17: оборванные заголовки, словарь и постинги отклоняются")
def truncated_indices(checks):
    for index in (checks.raw, checks.vb):
        data = index.read_bytes()
        for length in (0, 7, len(data) // 2, len(data) - 1):
            destination = checks.work / f"cut_{index.stem}_{length}.idx"
            destination.write_bytes(data[:length])
            checks.invoke("verify", "--index", destination, success=False)


@case("E03 duplicate_ids", "ЛР7: одинаковые внешние ID не должны незаметно объединять разные документы")
def duplicate_ids(checks):
    manifest = checks.work / "duplicates.tsv"
    row = checks.manifest.read_text(encoding="utf-8").splitlines()[0]
    manifest.write_text(row + "\n" + row + "\n", encoding="utf-8")
    checks.invoke("build", "--manifest", manifest, "--index", checks.work / "duplicates.idx", success=False)


@case("E04 missing_source", "ЛР7: отсутствующий файл корпуса — явная ошибка, не тихий пропуск")
def missing_source(checks):
    manifest = checks.work / "missing.tsv"
    manifest.write_text(f"id\t{checks.work / 'absent.tokens'}\tTitle\thttps://example.invalid\n", encoding="utf-8")
    checks.invoke("build", "--manifest", manifest, "--index", checks.work / "missing.idx", success=False)


@case("E07 failed_build_preserves_index", "ЛР7: неудачная пересборка не заменяет существующий рабочий индекс")
def failed_build_preserves_index(checks):
    destination = checks.work / "keep_existing.idx"
    original = checks.raw.read_bytes()
    destination.write_bytes(original)
    manifest = checks.work / "failure_after_valid.tsv"
    valid = checks.manifest.read_text(encoding="utf-8").splitlines()[0]
    manifest.write_text(valid + f"\nbroken\t{checks.work / 'not_found.tokens'}\n", encoding="utf-8")
    checks.invoke("build", "--manifest", manifest, "--index", destination, success=False)
    assert destination.read_bytes() == original
    checks.invoke("compress", "--input", destination, "--output", destination, success=False)
    assert destination.read_bytes() == original


@case("E05 invalid_arguments", "ЛР17: неверные режимы и недопустимая пагинация диагностируются")
def invalid_arguments(checks):
    for args in (("unknown-command",), ("search", "--index", checks.raw, "--limit", "-1"),
                 ("search", "--index", checks.raw, "--offset", "-1"),
                 ("search", "--index", checks.raw, "--unknown-option")):
        checks.invoke(*args, success=False)


@case("C04 variable_byte_boundaries", "ЛР11: tf и промежутки docID на границах 127/128/16383/16384 кодируются без потерь")
def variable_byte_boundaries(checks):
    # 16 642 синтетических записей; пустые документы используют один общий файл.
    # Это тест целых чисел кодека, а не заявление о размере реального корпуса.
    directory = checks.work / "vb_boundaries"
    directory.mkdir(exist_ok=True)
    empty = directory / "empty.tokens"
    empty.write_text("", encoding="utf-8")
    special = {
        0: ["сад"],
        127: ["сад"] + ["кот"] * 127,
        255: ["сад"] + ["кот"] * 128,
        16638: ["сад"] + ["кот"] * 16383,
        16639: ["кот"] * 16384,
        16640: ["дом"] * 128,
    }
    files = {}
    for position, terms in special.items():
        path = directory / f"{position}.tokens"
        path.write_text("\n".join(terms), encoding="utf-8")
        files[position] = path
    manifest = directory / "manifest.tsv"
    manifest.write_text("".join(f"id{index:05d}\t{files.get(index, empty)}\n" for index in range(16642)), encoding="utf-8")
    raw, packed = directory / "raw.idx", directory / "vb.idx"
    checks.invoke("build", "--manifest", manifest, "--index", raw, timeout=90)
    checks.invoke("compress", "--input", raw, "--output", packed)
    expected = {
        "сад": {"id00000", "id00127", "id00255", "id16638"},
        "кот": {"id00127", "id00255", "id16638", "id16639"},
        "дом": {"id16640"},
    }
    for query in ("сад", "кот", "дом", "кот || сад", "кот && сад"):
        before = checks.search(query, ranked=True, index=raw)
        after = checks.search(query, ranked=True, index=packed)
        assert before["results"] == after["results"] and before["count"] == after["count"], query
        if query in expected:
            assert set(checks.ids(before)) == expected[query], (query, before)
    assert packed.stat().st_size < raw.stat().st_size, "Постинги синтетического корпуса должны сжиматься"
    checks.invoke("verify", "--index", packed)


@case("C05 block_group_measurements", "ЛР11: отдельно все/редкие/средние/частые блоки; канонические суммы одинаковы")
def block_group_measurements(checks):
    values = [json.loads(checks.invoke("benchmark", "--index", index).stdout.decode("utf-8"))
              for index in (checks.raw, checks.vb)]
    for name, term_count in (("rare", 1), ("medium", 4), ("frequent", 0), ("all", 5)):
        before, after = (value["groups"][name] for value in values)
        assert before["terms"] == after["terms"] == term_count, (name, before, after)
        assert before["postings"] == after["postings"], (name, before, after)
        assert before["checksum"] == after["checksum"], (name, before, after)
        assert before["seconds"] >= 0 and after["seconds"] >= 0


@case("B04 external_merge", "ЛР7: ограничение буфера вызывает несколько сортированных серий; результат не зависит от размера буфера")
def external_merge(checks):
    directory = checks.work / "external_merge"
    directory.mkdir(exist_ok=True)
    source = directory / "shared.tokens"
    # Эти технические ключи проверяют структуру индекса, не лингвистику.
    source.write_text("кот\n" + "".join(f"термин{index:05d}\n" for index in range(599)), encoding="utf-8")
    manifest = directory / "manifest.tsv"
    manifest.write_text("".join(f"doc{index:03d}\t{source}\n" for index in range(180)), encoding="utf-8")
    small, large = directory / "memory1.idx", directory / "memory64.idx"
    stats = []
    for memory, destination in ((1, small), (64, large)):
        stats.append(json.loads(checks.invoke("build", "--manifest", manifest, "--index", destination,
                                             "--memory-mb", memory).stdout.decode("utf-8")))
    assert stats[0]["runs"] >= 2 and stats[1]["runs"] == 1, stats
    assert stats[0]["peak_record_bytes"] <= 1048576, stats
    for value in stats:
        assert value["documents"] == 180 and value["terms"] == 600, value
        assert value["tokens"] == value["postings"] == 108000, value
    assert small.read_bytes() == large.read_bytes(), "Слияние серий должно давать тот же бинарный индекс"
    value = checks.search("кот", index=small)
    assert set(checks.ids(value)) == {f"doc{index:03d}" for index in range(180)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, default=ROOT / "build/ir_engine.exe")
    parser.add_argument("--work", type=Path, default=ROOT / "build/tests_lr7_13")
    parser.add_argument("--results", type=Path, default=ROOT / "tests_lr7_13/results")
    args = parser.parse_args()
    args.results.mkdir(parents=True, exist_ok=True)
    rows = []
    started = time.perf_counter()
    try:
        checks = Checks(args.engine, args.work)
    except Exception as error:
        rows.append({"name": "SETUP", "basis": "Сборка контрольного корпуса и обоих индексов",
                     "status": "FAIL", "detail": str(error), "seconds": time.perf_counter() - started})
        checks = None
    if checks:
        for name, basis, function in CASES:
            start = time.perf_counter()
            try:
                function(checks)
                status, detail = "PASS", ""
            except Exception as error:
                status, detail = "FAIL", str(error) or traceback.format_exc()
            rows.append({"name": name, "basis": basis, "status": status,
                         "detail": detail, "seconds": time.perf_counter() - start})
            print(f"{status} {name}" + (f": {detail[:500]}" if detail else ""), flush=True)
    passed = sum(row["status"] == "PASS" for row in rows)
    failed = len(rows) - passed
    result = {"suite": "IR_CLI", "cases": len(rows), "passed": passed, "failed": failed,
              "seconds": time.perf_counter() - started, "engine": str(args.engine.resolve()),
              "test_cases": rows}
    (args.results / "test_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f"IR_CLI: cases={len(rows)} PASS={passed} FAIL={failed}"]
    for row in rows:
        lines.extend([f"\n{row['status']} {row['name']}", f"Основание: {row['basis']}"])
        if row["detail"]:
            lines.append(f"Ошибка: {row['detail']}")
    (args.results / "test_results.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[0], flush=True)
    return int(failed != 0)


if __name__ == "__main__":
    raise SystemExit(main())
