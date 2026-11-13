# Пояснение к файлу: Сравнение исходного и VB-индекса на одинаковых запросах.
# Как читать: Проверка полных выдач потоковыми хешами → повторные замеры → сравнение групп постингов. Размер всего индекса отличается от размера постингов.
# Команды запуска и структура проекта: README.md.

"""Повторяемые замеры бинарного и сжатого индекса на реальном корпусе."""
import csv
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
import argparse
import hashlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.project.build import EXE, compiler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()
    compiler()
    engine = ROOT / ("build/ir_engine" + EXE)
    out = ROOT / "lab11/results"
    out.mkdir(parents=True, exist_ok=True)
    with (ROOT / "lab04/queries.csv").open(encoding="utf-8-sig", newline="") as f:
        queries = [row["query"] for row in csv.DictReader(f)]
    queries += ["клетки && иммунитет", "(космос || нейроны) && !вирус", "!несуществующийтермин", "(в || и) && !(ген && клетка)", "агент 007"]
    query_rows, blocks, reference = [], {}, {}
    document_count = json.loads((ROOT / "results/project_stats.json").read_text(encoding="utf-8"))["documents"]
    for mode, index in (("raw", ROOT / "data/lab07_index/index.ir"), ("packed", ROOT / "data/lab11_index/index.ir")):
        print(f"Benchmark: {mode}", flush=True)
        for ranked in (False, True):
            repeats = []
            for repeat in range(3):
                print(f"  queries ranked={ranked}, repeat={repeat + 1}/3", flush=True)
                command = [str(engine), "search", "--index", str(index), "--limit", str(50 if args.stream else document_count)]
                if ranked:
                    command.append("--ranked")
                started = time.perf_counter()
                process = subprocess.run(command, cwd=ROOT, input="\n".join(queries) + "\n", capture_output=True,
                                         encoding="utf-8", check=True)
                repeats.append(time.perf_counter() - started)
                rows = [json.loads(line) for line in process.stdout.splitlines()]
                if len(rows) != len(queries) or any(row.get("error") for row in rows):
                    raise RuntimeError("Ошибка в пакете поисковых запросов")
                for query, row in zip(queries, rows):
                    if not args.stream and len(row["results"]) != row["count"]:
                        raise RuntimeError("Сравнение получило усечённую выдачу")
                    key = (ranked, query)
                    # Метаданные/документы/веса и весь порядок должны совпадать.
                    if key in reference and reference[key] != row:
                        raise RuntimeError("Выдачи raw/packed расходятся: " + query)
                    reference[key] = row
            query_rows.append({"index": mode, "ranked": ranked, "queries": len(queries),
                               "repeats_seconds": repeats, "median_batch_seconds": statistics.median(repeats),
                               "median_seconds_per_query": statistics.median(repeats) / len(queries),
                               "timing_scope": "process startup, index loading, all queries, JSON output included; limit=" + str(50 if args.stream else document_count)})
            if args.stream:
                # Полная выдача может занимать сотни МБ: сравниваем каждый байт
                # через потоковый SHA-256, не удерживая JSON всех документов в RAM.
                with tempfile.TemporaryFile() as output:
                    command = [str(engine), "search", "--index", str(index), "--limit", str(document_count)]
                    if ranked:
                        command.append("--ranked")
                    subprocess.run(command, cwd=ROOT, input=("\n".join(queries)+"\n").encode("utf-8"), stdout=output, check=True)
                    output.seek(0)
                    digest = hashlib.sha256(); lines = 0; error = False
                    tail = b""
                    for chunk in iter(lambda: output.read(1048576), b""):
                        digest.update(chunk); lines += chunk.count(b"\n")
                        error |= b'"error":' in tail + chunk
                        tail = chunk[-32:]
                if lines != len(queries) or error:
                    raise RuntimeError("Неполная выдача или ошибка в полном сравнении")
                key = (ranked, "full_sha256")
                if key in reference and reference[key] != digest.hexdigest():
                    raise RuntimeError("Полные выдачи raw/packed расходятся")
                reference[key] = digest.hexdigest()
        passes = []
        for repeat in range(3):
            print(f"  all posting blocks, repeat={repeat + 1}/3", flush=True)
            process = subprocess.run([str(engine), "benchmark", "--index", str(index)], cwd=ROOT,
                                     capture_output=True, encoding="utf-8", check=True)
            passes.append(json.loads(process.stdout))
        blocks[mode] = passes
    for group in ("rare", "medium", "frequent", "all"):
        expected = {key: blocks["raw"][0]["groups"][group][key] for key in ("terms", "postings", "checksum")}
        for passes in blocks.values():
            for sample in passes:
                actual = {key: sample["groups"][group][key] for key in expected}
                if actual != expected:
                    raise RuntimeError("Списки постингов расходятся после сжатия: " + group)
    # Масштаб: разные подмножества настоящих документов, без дублирования статей.
    source = ROOT / ("data/corpus/stems.tsv" if args.stream else "build/manifests/stems.tsv")
    if args.stream:
        from itertools import islice
        with source.open(encoding="utf-8") as file:
            manifest_lines = [line.rstrip("\n") for line in islice(file, 1000)]
    else:
        manifest_lines = source.read_text(encoding="utf-8").splitlines()
    scale = []
    with tempfile.TemporaryDirectory(prefix="mai_ir_scale_") as temporary:
        folder = Path(temporary)
        for size in sorted({min(n, len(manifest_lines)) for n in (10, 100, 1000, len(manifest_lines))}):
            subset = folder / "manifest.tsv"
            subset.write_text("\n".join(manifest_lines[:size]) + "\n", encoding="utf-8")
            index = folder / "scale.ir"
            started = time.perf_counter()
            process = subprocess.run([str(engine), "build", *(["--inline"] if args.stream else []), "--manifest", str(subset), "--index", str(index)],
                                     cwd=ROOT, capture_output=True, encoding="utf-8", check=True)
            seconds = time.perf_counter() - started
            scale.append({"documents": size, "seconds": seconds, "bytes": index.stat().st_size,
                          "build_stats": json.loads(process.stdout)})
    if args.stream:
        full = json.loads((ROOT / "results/index_build.json").read_text(encoding="utf-8"))
        scale.append({"documents": full["documents"], "seconds": full["seconds"], "bytes": full["bytes"], "build_stats": full,
                      "scope": "main full-corpus build; not rebuilt just for scaling"})
    report = {"query_benchmark": query_rows, "posting_passes": blocks, "scaling": scale,
              "comparison_queries": queries, "query_cases": len(queries) * 2,
              "lossless_equal": True, "compared_full_result_lists": True,
              "full_result_sha256": {str(k): v for k, v in reference.items() if k[1] == "full_sha256"},
              "scope": "Текущая машина; 3 повтора с медианой; размер корпуса указан в scaling"}
    (out / "benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Сжатие: {len(queries)*2} полных выдач совпали; замеры сохранены в lab11/results/benchmark.json.")


if __name__ == "__main__":
    main()
