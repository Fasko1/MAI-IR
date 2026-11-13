# Пояснение к файлу: Ограниченная диагностика поиска до/после стемминга.
# Как читать: Исторические оценки двух запросов → выдачи → precision/recall и таблицы. Пул не представляет весь современный корпус.
# Команды запуска и структура проекта: README.md.

"""Оценить AND-поиск до/после Snowball на прежнем фиксированном размеченном пуле.

Токенизация, стемминг и поиск выполняются настоящими C++ CLI. Python только
готовит списки документов, читает неизменённые qrels и считает метрики.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools.project.build import compiler
BIN = ROOT / "build/labs_3_5"
OUT = ROOT / "lab04/results_snowball"
QRELS = ROOT / "lab04/review_10000/review_10000.csv"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def run(*args: object) -> None:
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL)


def labels() -> tuple[dict, list[str]]:
    queries: dict[str, dict] = {}
    assessors: set[str] = set()
    with QRELS.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        required = {"query_id", "query", "doc_id", "relevant", "assessor"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"Missing qrels columns: {sorted(required)}")
        for row in reader:
            qid, query, doc = row["query_id"].strip(), row["query"].strip(), row["doc_id"].strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]+", qid) or not re.fullmatch(r"[0-9]+", doc):
                raise ValueError("Query/doc ids must have the documented ASCII format")
            if not query or row["relevant"].strip() not in {"0", "1"}:
                raise ValueError(f"Invalid qrel: {qid}/{doc}")
            group = queries.setdefault(qid, {"query": query, "qrels": {}, "intent": row.get("query_intent", "")})
            if group["query"] != query or doc in group["qrels"]:
                raise ValueError(f"Conflicting query or repeated qrel: {qid}/{doc}")
            group["qrels"][doc] = int(row["relevant"].strip())
            assessors.add(row["assessor"].strip())
    if not queries:
        raise ValueError("The qrels file contains no judged documents")
    return queries, sorted(assessors)


def main() -> None:
    compiler()
    for binary in ("tokenization.exe", "stemming.exe", "search.exe"):
        if not (BIN / binary).is_file():
            raise FileNotFoundError(f"Build the C++ program first: {BIN / binary}")
    queries, assessors = labels()
    OUT.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    descriptions: list[dict] = []
    pool_hashes: dict[str, dict[str, str]] = {}
    with tempfile.TemporaryDirectory(prefix="mai_stemming_eval_", dir=OUT) as temporary:
        workspace = Path(temporary)
        for number, (qid, group) in enumerate(sorted(queries.items()), start=1):
            query = group["query"]
            judged = group["qrels"]
            ids = sorted(judged, key=int)
            relevant = {doc for doc, value in judged.items() if value == 1}
            query_directory = workspace / str(number)
            input_directory = query_directory / "input"
            input_directory.mkdir(parents=True)
            (input_directory / "1.txt").write_text(query, encoding="utf-8")
            token_directory = query_directory / "tokens"
            run(BIN / "tokenization.exe", input_directory, token_directory, ROOT / "lab03/abbrevs.txt")
            query_tokens = token_directory / "1.tokens"
            query_stems = query_directory / "query.stems"
            # Политика основного корпуса: STOPLIST не передаётся.
            run(BIN / "stemming.exe", query_tokens, query_stems)
            token_terms = query_tokens.read_text(encoding="utf-8").splitlines()
            stem_terms = query_stems.read_text(encoding="utf-8").splitlines()
            if not token_terms or len(token_terms) != len(stem_terms):
                raise ValueError(f"Empty query or mismatched token/stem counts: {qid}")
            descriptions.append({"query_id": qid, "query": query, "intent": group["intent"],
                                 "judged_documents": len(ids), "relevant_documents": len(relevant),
                                 "pool_ids": ids, "query_tokens": token_terms, "query_stems": stem_terms})
            pool_hashes[qid] = {}
            for mode, folder, suffix, query_file in (
                ("tokens", "lab03_tokens", ".tokens", query_tokens),
                ("stems", "lab04_stems", ".stems", query_stems),
            ):
                documents = [ROOT / "data" / folder / f"{doc}{suffix}" for doc in ids]
                for document in documents:
                    if not document.is_file():
                        raise FileNotFoundError(f"Missing judged document in current corpus: {document}")
                pool_digest = hashlib.sha256()
                for doc, document in zip(ids, documents):
                    pool_digest.update(f"{doc}\t{sha256(document)}\n".encode("utf-8"))
                pool_hashes[qid][mode] = pool_digest.hexdigest()
                listing = query_directory / f"{mode}_inputs.txt"
                listing.write_text("\n".join(map(str, documents)) + "\n", encoding="utf-8")
                retrieved_file = query_directory / f"{mode}_results.txt"
                run(BIN / "search.exe", listing, query_file, retrieved_file)
                raw_ids = [Path(line).stem for line in retrieved_file.read_text(encoding="utf-8").splitlines()]
                found = set(raw_ids)
                if len(found) != len(raw_ids) or not found.issubset(judged):
                    raise ValueError(f"Search returned duplicate or unjudged documents: {qid}/{mode}")
                true_positive = found & relevant
                precision = len(true_positive) / len(found) if found else 0.0
                recall = len(true_positive) / len(relevant) if relevant else None
                f1 = (2 * precision * recall / (precision + recall) if precision + recall else 0.0) if recall is not None else None
                results.append({"query_id": qid, "query": query, "mode": mode,
                                "judged_documents": len(ids), "relevant_documents": len(relevant),
                                "retrieved": len(found), "relevant_retrieved": len(true_positive),
                                "precision": precision, "pool_recall": recall, "f1": f1,
                                "retrieved_ids": sorted(found, key=int),
                                "relevant_retrieved_ids": sorted(true_positive, key=int),
                                "missed_relevant_ids": sorted(relevant - found, key=int)})
    source_paths = ["lab03/funcs/tokenizer.cpp", "lab03/funcs/tokenizer.h", "lab03/tokenization.cpp",
                    "lab03/abbrevs.txt", "lab04/funcs/stemmer.cpp", "lab04/funcs/stemmer.h",
                    "lab04/stemming.cpp", "lab04/search.cpp", "lab04/evaluate_stemming.py"]
    metrics = {
        "evaluation": "AND search before/after Russian Snowball on existing fixed per-query judged pools",
        "collection_policy": "Each query searches only document ids judged for that query; token/stem modes use the same ids. STOPLIST is not used.",
        "assessor": assessors,
        "qrels_source": str(QRELS.relative_to(ROOT)).replace("\\", "/"),
        "qrels_sha256": sha256(QRELS),
        "source_sha256": {path: sha256(ROOT / path) for path in source_paths},
        "binary_sha256": {name: sha256(BIN / name) for name in ("tokenization.exe", "stemming.exe", "search.exe")},
        "pool_sha256": pool_hashes,
        "queries": descriptions,
        "results": results,
        "limitations": [
            "Existing AI relevance judgments are reused unchanged; they have no independent human validation.",
            "Recall is measured relative to each closed judged pool, not all relevant documents in the 10000-document corpus.",
            "The small historical pools were selected for previous experiments and can be biased toward documents found by the old stemmer.",
            "Precision/F1 here apply to the closed pool and do not estimate overall search quality on unseen queries.",
            "Historical before/after columns from the qrels file are ignored; both result sets are produced again by current C++ programs.",
        ],
    }
    (OUT / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (OUT / "metrics.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(results[0]))
        writer.writeheader()
        for result in results:
            writer.writerow({key: " ".join(value) if isinstance(value, list) else value for key, value in result.items()})
    for row in results:
        print(f"{row['query_id']} {row['mode']}: retrieved={row['retrieved']}, TP={row['relevant_retrieved']}, "
              f"P={row['precision']:.6f}, pool_R={row['pool_recall']}, F1={row['f1']}")
    print("Saved: lab04/results_snowball/metrics.json and metrics.csv")


if __name__ == "__main__":
    main()
