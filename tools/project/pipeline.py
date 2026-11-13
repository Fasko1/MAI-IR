# Пояснение к файлу: Маршрутизация сборки, тестов, экспорта и полного пересчёта.
# Как читать: main разбирает флаги; run запускает этапы; большой TSV передаётся tools.stream_project, файловый режим оставлен для совместимости.
# Команды запуска и структура проекта: README.md.

"""Сборка, проверки и полный пересчёт ЛР3–5, 7, 8, 11, 13, 17 без БД."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from tools.project.build import ROOT, EXE, compiler


# Запуск этапа с обязательной проверкой exit code; измеряется внешнее время вместе с запуском и вводом-выводом.
def run(command, log=None):
    command = [str(x) for x in command]
    started = time.perf_counter()
    if log:
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        with Path(log).open("w", encoding="utf-8") as output:
            result = subprocess.run(command, cwd=ROOT, stdout=output)
        print(Path(log).read_text(encoding="utf-8"), end="", flush=True)
        result.check_returncode()
    else:
        subprocess.run(command, cwd=ROOT, check=True)
    return time.perf_counter() - started


# SHA-256 читается блоками: многогигабайтный файл не загружается целиком в Python.
def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_manifests():
    folder = ROOT / "data/lab03_texts"
    files = sorted(folder.glob("*.txt"), key=lambda p: int(p.stem))
    if not files:
        raise SystemExit("Нет data/lab03_texts/*.txt. Выгрузите существующий корпус через lab03/export_texts.py.")
    metadata_file = folder / "metadata.json"
    metadata = json.loads(metadata_file.read_text(encoding="utf-8")) if metadata_file.exists() else {}
    work = ROOT / "build/manifests"
    work.mkdir(parents=True, exist_ok=True)

    def clean(value):
        return str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")

    for suffix, target in (("tokens", "tokens.tsv"), ("stems", "stems.tsv")):
        source = "lab03_tokens" if suffix == "tokens" else "lab04_stems"
        rows = []
        for path in files:
            info = metadata.get(path.stem, {})
            rows.append("\t".join([path.stem, (ROOT / "data" / source / (path.stem + "." + suffix)).as_posix(),
                                   clean(info.get("title", "Документ " + path.stem)), clean(info.get("url", ""))]))
        (work / target).write_text("\n".join(rows) + "\n", encoding="utf-8")
    return files


# Команды tests-only/export/stream разделены: изменение отчёта не требует повторного пересчёта корпуса.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tests-only", action="store_true")
    parser.add_argument("--skip-processing", action="store_true", help="Только для уже пересчитанных токенов/основ")
    parser.add_argument("--skip-pdf", action="store_true")
    parser.add_argument("--export", action="store_true", help="Новый потоковый снимок PostgreSQL")
    parser.add_argument("--legacy", action="store_true", help="Прежний режим отдельных файлов")
    args = parser.parse_args()
    compiler()
    run([sys.executable, "-m", "tools.project.build"])
    run([sys.executable, "-m", "tools.project.tests", "--no-build"])
    if args.tests_only:
        return
    if args.export:
        run([sys.executable, "lab03/export_texts.py", "--stream"])
    if not args.legacy and (ROOT / "data/corpus/texts.tsv").is_file():
        from tools.stream_project import process
        process(args)
        return
    files = write_manifests()
    results = ROOT / "results"
    results.mkdir(exist_ok=True)
    previous = results / "preprocessing.json"
    timing = json.loads(previous.read_text(encoding="utf-8")) if args.skip_processing and previous.exists() else {}
    if not args.skip_processing:
        timing["tokenization_seconds"] = run([ROOT / ("build/labs_3_5/tokenization" + EXE), ROOT / "data/lab03_texts",
                                                ROOT / "data/lab03_tokens", ROOT / "lab03/abbrevs.txt"], results / "tokenization.txt")
        (ROOT / "data/lab04_stems").mkdir(parents=True, exist_ok=True)
        timing["stemming_seconds"] = run([ROOT / ("build/labs_3_5/stemming" + EXE), "--batch",
                                            ROOT / "build/manifests/tokens.tsv", ROOT / "data/lab04_stems"], results / "stemming.txt")
        (results / "preprocessing.json").write_text(json.dumps(timing, indent=2), encoding="utf-8")
    expected = {p.stem for p in files}
    for directory, suffix in (("lab03_tokens", ".tokens"), ("lab04_stems", ".stems")):
        actual = {p.stem for p in (ROOT / "data" / directory).glob("*" + suffix)}
        if actual != expected:
            raise SystemExit(f"Документы {directory} не совпадают с корпусом: отсутствуют {len(expected-actual)}, лишние {len(actual-expected)}.")
    run([sys.executable, "lab05/zipfs_law.py"])
    run([sys.executable, "lab03/benchmark.py"])
    engine = ROOT / ("build/ir_engine" + EXE)
    raw = ROOT / "data/lab07_index/index.ir"
    packed = ROOT / "data/lab11_index/index.ir"
    raw.parent.mkdir(parents=True, exist_ok=True)
    packed.parent.mkdir(parents=True, exist_ok=True)
    timing["index_seconds"] = run([engine, "build", "--manifest", ROOT / "build/manifests/stems.tsv", "--index", raw], results / "index_build.json")
    timing["compression_seconds"] = run([engine, "compress", "--input", raw, "--output", packed], results / "compression.json")
    run([engine, "verify", "--index", raw], results / "verify_raw.txt")
    run([engine, "verify", "--index", packed], results / "verify_packed.txt")
    run([engine, "stats", "--index", raw], results / "index_raw.json")
    run([engine, "stats", "--index", packed], results / "index_packed.json")
    tokens = json.loads((ROOT / "lab05/results/tokens/stats.json").read_text(encoding="utf-8"))
    stems = json.loads((ROOT / "lab05/results/stems/stats.json").read_text(encoding="utf-8"))
    if tokens["total_terms"] != stems["total_terms"]:
        raise SystemExit("Без стоп-фильтра число вхождений токенов и основ должно совпадать.")
    run([ROOT / ("build/labs_3_5/audit" + EXE), ROOT / "build/manifests/tokens.tsv",
         ROOT / "build/manifests/stems.tsv", results / "stemming_audit.json"])
    stats = {
        "date": datetime.now(timezone(timedelta(hours=3))).date().isoformat(), "documents": len(files), "text_bytes": sum(p.stat().st_size for p in files),
        "tokens": tokens["total_terms"], "unique_tokens": tokens["unique_terms"],
        "stems": stems["total_terms"], "unique_stems": stems["unique_terms"],
        "stemmer_implementations": 1, "stopwords_removed": False,
        "vocabulary_reduction_percent": 100 * (1 - stems["unique_terms"] / tokens["unique_terms"]),
        "raw_index_bytes": raw.stat().st_size, "packed_index_bytes": packed.stat().st_size,
        "packed_to_raw_ratio": packed.stat().st_size / raw.stat().st_size,
        "raw_sha256": digest(raw), "packed_sha256": digest(packed), "timings": timing,
        "minimum_required_documents": 30000, "corpus_requirement_met": len(files) >= 30000,
    }
    (results / "project_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    run([sys.executable, "lab04/evaluate_stemming.py"])
    run([sys.executable, "lab13/evaluate.py"])
    run([sys.executable, "lab11/benchmark.py"])
    run([sys.executable, "-m", "tools.project.report", *( ["--skip-pdf"] if args.skip_pdf else [])])
    print("Готово: результаты в results/, журнал проверок в tests/results/, отчёт report.tex" +
          ("." if args.skip_pdf else " и report.pdf."))


if __name__ == "__main__":
    main()
