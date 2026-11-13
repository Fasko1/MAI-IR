# Пояснение к файлу: Диагностика TF-IDF по сохранённым оценкам релевантности.
# Как читать: Запросы к C++ → P/DCG/NDCG/ERR → CSV/JSON. Два исторических пула с ИИ-разметкой не доказывают качество на всём корпусе.
# Команды запуска и структура проекта: README.md.

"""Оценка TF-IDF на сохранённом размеченном пуле; полная выдача — отдельно."""
import csv
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.project.build import EXE, compiler

ENGINE = ROOT / ("build/ir_engine" + EXE)


def search(index, queries, ranked, limit=50):
    command = [str(ENGINE), "search", "--index", str(index), "--limit", str(limit)]
    if ranked:
        command.append("--ranked")
    result = subprocess.run(command, input="\n".join(queries) + "\n", cwd=ROOT, check=True,
                            capture_output=True, encoding="utf-8")
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    if len(rows) != len(queries) or any(row.get("error") for row in rows):
        raise RuntimeError("Неполный результат оценки либо ошибка запроса")
    return rows


def metrics(ids, qrels, k):
    grades = [qrels.get(str(i), 0) for i in ids[:k]]
    dcg = sum((2**grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(grades))
    ideal = sorted(qrels.values(), reverse=True)[:k]
    idcg = sum((2**grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(ideal))
    continuation, err = 1., 0.
    for rank, grade in enumerate(grades, 1):
        probability = (2**grade - 1) / 2  # Бинарная шкала 0/1, max_grade=1.
        err += continuation * probability / rank
        continuation *= 1 - probability
    return {"precision": sum(grade > 0 for grade in grades) / k,
            "dcg": dcg, "ndcg": dcg / idcg if idcg else 0., "err": err}


def main():
    compiler()
    out = ROOT / "lab13/results"
    out.mkdir(parents=True, exist_ok=True)
    # Это уже имеющиеся оценки, а не новая разметка под результаты алгоритма.
    source = ROOT / "lab04/review_10000/review_10000.csv"
    with source.open(encoding="utf-8-sig", newline="") as f:
        judgments = list(csv.DictReader(f))
    qrels, queries = {}, {}
    for row in judgments:
        qid = row["query_id"]
        qrels.setdefault(qid, {})[row["doc_id"]] = int(row["relevant"])
        queries[qid] = row["query"]
    all_ids = {row["doc_id"] for row in judgments}
    document_count = json.loads((ROOT / "results/project_stats.json").read_text(encoding="utf-8"))["documents"]
    # Кандидатное множество фиксировано для обеих систем: явное ИЛИ между словами.
    # Метрики считаются только на размеченных для данного запроса документах.
    rows = []
    index = ROOT / "data/lab11_index/index.ir"
    for qid in sorted(queries):
        query = " || ".join(queries[qid].split())
        for ranked in (False, True):
            result = search(index, [query], ranked, document_count)[0]
            if len(result["results"]) != result["count"]:
                raise RuntimeError("Усечённая выдача при оценке")
            ids = [row["id"] for row in result["results"] if str(row["id"]) in qrels[qid]]
            for k in (1, 3, 5):
                rows.append({"query_id": qid, "query": queries[qid], "mode": "tfidf" if ranked else "boolean",
                             "k": k, "judged_documents": len(qrels[qid]), "retrieved": len(ids),
                             **metrics(ids, qrels[qid], k), "ids": ids[:k]})
    # На полном корпусе неизвестные оценки остаются неизвестными.
    # Нижняя граница P@5 не выдаётся за полноценную оценку качества.
    full_rows = []
    full_index = ROOT / "data/lab11_index/index.ir"
    for qid in sorted(queries):
        data = search(full_index, [queries[qid]], True, 5)[0]
        ids = [str(row["id"]) for row in data["results"]]
        judged = sum(i in qrels[qid] for i in ids)
        relevant = sum(qrels[qid].get(i, 0) for i in ids)
        full_rows.append({"query_id": qid, "query": queries[qid], "results": data,
                          "judged_at_5": judged, "unjudged_at_5": len(ids) - judged,
                          "precision_at_5_lower_bound": relevant / 5})
    report = {
        "scope": "Диагностический закрытый пул отдельно для каждого запроса; не ЛР6 и не оценка всего корпуса",
        "assessor": "Сохранённая ИИ-разметка исходного проекта, без независимой человеческой проверки",
        "qrels_source": str(source.relative_to(ROOT)), "unique_pool_documents": len(all_ids),
        "queries": len(queries), "query_document_judgments": len(judgments),
        "candidate_policy": "explicit OR identical for both modes; full-corpus IDF/norms; ranking filtered to fixed judged pool",
        "metrics": rows, "full_corpus": full_rows,
    }
    (out / "quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "quality.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (ROOT / "lab04/queries.csv").open(encoding="utf-8-sig", newline="") as f:
        examples = list(csv.DictReader(f))
    results = search(full_index, [row["query"] for row in examples], True, 10)
    (out / "examples.json").write_text(json.dumps([{"query_id": q["query_id"], **r} for q, r in zip(examples, results)],
                                                 ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"TF-IDF: {len(queries)} запроса, {len(judgments)} сохранённых оценок; ограничения указаны в quality.json.")


if __name__ == "__main__":
    main()
