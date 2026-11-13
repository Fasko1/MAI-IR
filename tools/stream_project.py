# Пояснение к файлу: Главный конвейер обработки большого сохранённого снимка.
# Как читать: extract_samples оставляет малые примеры; process запускает препроцессор, Ципфа, аудит, индексацию, сжатие и сравнения; затем публикует статистику.
# Команды запуска и структура проекта: README.md.

"""Полный конвейер для большого корпуса: три TSV вместо миллионов файлов."""
import csv
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.project.build import EXE
from tools.project.pipeline import run, digest


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def extract_samples():
    """Небольшие файлы для прежней оценки и независимого бенчмарка."""
    for name in ("lab03_texts", "lab03_tokens", "lab04_stems"):
        (ROOT / "data" / name).mkdir(parents=True, exist_ok=True)
    with (ROOT / "lab04/review_10000/review_10000.csv").open(encoding="utf-8-sig") as source:
        judged = {row["doc_id"] for row in csv.DictReader(source)}
    sample = ROOT / "build/benchmark_texts"
    sample.mkdir(parents=True, exist_ok=True)
    amount = 0
    found = set()
    with (ROOT / "data/corpus/texts.tsv").open(encoding="utf-8") as source:
        for line in source:
            doc, text, title, url = line.rstrip("\n").split("\t")
            if amount < 20 * 1048576:
                (sample / (doc + ".txt")).write_text(text, encoding="utf-8")
                amount += len(text.encode("utf-8"))
            if doc in judged:
                found.add(doc)
                (ROOT / f"data/lab03_texts/{doc}.txt").write_text(text, encoding="utf-8")
            if amount >= 20 * 1048576 and found == judged:
                break
    if found != judged:
        raise RuntimeError("В снимке отсутствуют документы исторической разметки")
    for kind, directory, suffix in (("tokens", "lab03_tokens", "tokens"), ("stems", "lab04_stems", "stems")):
        remaining = set(judged)
        with (ROOT / f"data/corpus/{kind}.tsv").open(encoding="utf-8") as source:
            for line in source:
                doc, terms, title, url = line.rstrip("\n").split("\t")
                if doc in remaining:
                    out = ROOT / f"data/{directory}/{doc}.{suffix}"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text("".join(word + "\n" for word in terms.split()), encoding="utf-8")
                    remaining.remove(doc)
                if not remaining:
                    break
        if remaining:
            raise RuntimeError("Неполный выход препроцессора")


# Этапы используют один и тот же снимок; результаты связываются хешем входного texts.tsv и фактическими счётчиками.
def process(args):
    results = ROOT / "results"
    folder = ROOT / "data/corpus"
    snapshot = load("data/corpus/snapshot.json")
    if digest(folder / "texts.tsv") != snapshot["sha256"]:
        raise RuntimeError("Текстовый снимок изменён после экспорта; обновите его manifest")
    progress = {"started_at": datetime.now(timezone.utc).isoformat(), "documents": snapshot["documents"], "completed": []}
    def stage(name, command, log=None):
        progress["current"] = name
        (results / "pipeline.json").write_text(json.dumps(progress, indent=2), encoding="utf-8")
        print(f"Stage: {name}", flush=True)
        elapsed = run(command, log)
        progress["completed"].append({"stage": name, "seconds": elapsed})
        return elapsed
    previous = results / "preprocessing.json"
    timing = load("results/preprocessing.json") if args.skip_processing and previous.exists() else {}
    if not args.skip_processing:
        timing["preprocessing_seconds"] = stage("tokenization_and_stemming", [ROOT / ("build/labs_3_5/preprocess_stream" + EXE),
            folder / "texts.tsv", folder / "tokens.tsv.building", folder / "stems.tsv.building", ROOT / "lab03/abbrevs.txt",
            results / "preprocessing_counts.json"])
        for name in ("tokens", "stems"):
            (folder / (name + ".tsv.building")).replace(folder / (name + ".tsv"))
        timing["snapshot_sha256"] = snapshot["sha256"]
        previous.write_text(json.dumps(timing, indent=2), encoding="utf-8")
    if timing.get("snapshot_sha256") != snapshot["sha256"]:
        raise RuntimeError("Токены относятся к другому снимку; повторите без --skip-processing")
    stage("zipf", [sys.executable, "lab05/zipfs_law.py", "--stream"])
    stage("alignment_audit", [ROOT / ("build/labs_3_5/audit_stream" + EXE), folder / "tokens.tsv", folder / "stems.tsv", results / "stemming_audit.json"])
    extract_samples()
    stage("tokenization_benchmark", [sys.executable, "lab03/benchmark.py", "--input-dir", ROOT / "build/benchmark_texts"])
    engine = ROOT / ("build/ir_engine" + EXE)
    raw, packed = ROOT / "data/lab07_index/index.ir", ROOT / "data/lab11_index/index.ir"
    raw.parent.mkdir(parents=True, exist_ok=True)
    packed.parent.mkdir(parents=True, exist_ok=True)
    timing["index_seconds"] = stage("index", [engine, "build", "--inline", "--manifest", folder / "stems.tsv", "--index", raw], results / "index_build.json")
    timing["compression_seconds"] = stage("compression", [engine, "compress", "--input", raw, "--output", packed], results / "compression.json")
    for name, path in (("raw", raw), ("packed", packed)):
        stage("verify_" + name, [engine, "verify", "--index", path], results / ("index_" + name + ".json"))
    tokens, stems, audit, raw_stats = load("lab05/results/tokens/stats.json"), load("lab05/results/stems/stats.json"), load("results/stemming_audit.json"), load("results/index_raw.json")
    if not (snapshot["documents"] == audit["documents"] == raw_stats["documents"] == tokens["documents"] == stems["documents"]):
        raise RuntimeError("Число документов расходится между этапами")
    if not (tokens["total_terms"] == stems["total_terms"] == raw_stats["tokens"] == audit["tokens"]):
        raise RuntimeError("Число вхождений расходится между этапами")
    if raw_stats["terms"] != stems["unique_terms"]:
        raise RuntimeError("Словарь индекса расходится с частотным словарём основ")
    packed_stats = load("results/index_packed.json")
    if any(raw_stats[key] != packed_stats[key] for key in ("documents", "terms", "postings", "tokens")):
        raise RuntimeError("Статистика raw/VB расходится")
    stats = {"date": datetime.now(timezone(timedelta(hours=3))).date().isoformat(), "documents": snapshot["documents"],
             "text_bytes": snapshot["text_bytes"], "sources": snapshot["sources"], "snapshot_sha256": snapshot["sha256"],
             "tokens": tokens["total_terms"], "stems": stems["total_terms"], "unique_tokens": tokens["unique_terms"],
             "unique_stems": stems["unique_terms"], "stemmer_implementations": 1, "stopwords_removed": False,
             "vocabulary_reduction_percent": 100 * (1 - stems["unique_terms"] / tokens["unique_terms"]),
             "raw_index_bytes": raw.stat().st_size, "packed_index_bytes": packed.stat().st_size,
             "packed_to_raw_ratio": packed.stat().st_size / raw.stat().st_size, "raw_sha256": digest(raw), "packed_sha256": digest(packed),
             "timings": timing, "minimum_required_documents": 30000, "corpus_requirement_met": snapshot["documents"] >= 30000,
             "input_mode": "stream_tsv"}
    (results / "project_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    stage("stemming_quality", [sys.executable, "lab04/evaluate_stemming.py"])
    stage("ranking_quality", [sys.executable, "lab13/evaluate.py"])
    stage("compression_benchmark", [sys.executable, "lab11/benchmark.py", "--stream"])
    stage("report", [sys.executable, "-m", "tools.project.report", *(["--skip-pdf"] if args.skip_pdf else [])])
    progress["current"] = "complete"
    progress["finished_at"] = datetime.now(timezone.utc).isoformat()
    (results / "pipeline.json").write_text(json.dumps(progress, indent=2), encoding="utf-8")
    print("Полный корпус обработан; results/project_stats.json, report.tex, report.pdf", flush=True)
