# Пояснение к файлу: Запуск всех групп автоматических проверок.
# Как читать: При необходимости сборка → C++ unit/CLI и Python HTTP/сборщик → журналы tests/results → общий код завершения.
# Команды запуска и структура проекта: README.md.

"""Все проверки: C++ unit, настоящие CLI и HTTP-интерфейс."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from tools.project.build import ROOT, EXE, compiler


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--no-build", action="store_true")
    args = p.parse_args()
    compiler()
    if not args.no_build:
        subprocess.run([sys.executable, "-m", "tools.project.build"], cwd=ROOT, check=True)
    groups = [
        ("linguistics_unit", [str(ROOT / ("build/tests_lr3_4/unit_tests" + EXE))]),
        ("linguistics_cli", [str(ROOT / ("build/tests_lr3_4/tests" + EXE))]),
        ("linguistics_contracts", [str(ROOT / ("build/tests_lr3_4/contracts" + EXE))]),
        ("zipf_cli", [sys.executable, "tests_lr7_13/test_zipf.py"]),
        ("search_unit", [str(ROOT / ("build/tests_lr7_13/unit_tests" + EXE))]),
        ("search_cli", [sys.executable, "tests_lr7_13/test_cli.py"]),
        ("web", [sys.executable, "tests_lr7_13/test_web.py"]),
        ("stream", [sys.executable, "tests_lr7_13/test_stream.py"]),
        ("extensions", [sys.executable, "tests_lr7_13/test_extensions.py"]),
        ("collection", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]),
    ]
    results = []
    directory = ROOT / "tests/results"
    directory.mkdir(parents=True, exist_ok=True)
    for name, command in groups:
        print("Проверка:", name, flush=True)
        started = time.perf_counter()
        result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                encoding="utf-8", errors="replace")
        print(result.stdout, end="", flush=True)
        (directory / (name + ".txt")).write_text(result.stdout, encoding="utf-8")
        results.append({"group": name, "exit_code": result.returncode,
                        "seconds": round(time.perf_counter() - started, 3)})
    (directory / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [r["group"] for r in results if r["exit_code"]]
    if failed:
        raise SystemExit("Не прошли: " + ", ".join(failed))
    print("Все группы проверок пройдены.")


if __name__ == "__main__":
    main()
