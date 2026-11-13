# Пояснение к файлу: Сборка всех C++ исполняемых файлов.
# Как читать: compiler ищет C++17; compile_one задаёт флаги; main проверяет эталоны Snowball и собирает цели. Сборка не меняет корпус.
# Команды запуска и структура проекта: README.md.

"""Сборка C++17-компонентов; внешние библиотеки C++ не нужны."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
EXE = ".exe" if os.name == "nt" else ""
os.environ.setdefault("PYTHONUTF8", "1")
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")


# Поиск компилятора из CXX, PATH или стандартных каталогов MSYS2; его DLL-путь нужен при запуске.
def compiler():
    candidate = os.environ.get("CXX") or shutil.which("g++")
    if not candidate and os.name == "nt":
        for path in ("C:/msys64/ucrt64/bin/g++.exe", "C:/msys64/mingw64/bin/g++.exe"):
            if Path(path).is_file():
                candidate = path
                break
    if not candidate:
        raise SystemExit("Не найден g++: установите компилятор C++17 или задайте CXX.")
    # MinGW DLL нужны также при запуске собранных файлов.
    os.environ["PATH"] = str(Path(candidate).resolve().parent) + os.pathsep + os.environ.get("PATH", "")
    return candidate


# Единый набор флагов C++17/UTF-8; на Windows статическая линковка позволяет запускать бинарник из обычного терминала.
def compile_one(cxx, output, sources, extra=()):
    destination = ROOT / (output + EXE)
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [cxx, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Wpedantic",
               "-finput-charset=UTF-8", "-fexec-charset=UTF-8", *extra,
               *sources, "-o", str(destination)]
    if os.name == "nt":
        # Includes libwinpthread as well: binaries must run in a fresh terminal
        # without the MSYS2 directory in PATH.
        command += ["-static", "-static-libgcc", "-static-libstdc++", "-lshell32"]
    print("Сборка:", output, flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--linguistics-only", action="store_true")
    args = parser.parse_args()
    for name, expected in {
        "snowball_voc.txt": "6e4cd2ed5c908fe3d4cabaa74a088d9043cb626417c1eadc2c157cafbf9772a0",
        "snowball_expected.txt": "c6265f9071e41b24590d567ea69181f5781790930907c77b28044f1d71b1bab3",
    }.items():
        path = ROOT / "tests_lr3_4/reference" / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise SystemExit("Изменился официальный эталон: " + str(path))
    cxx = compiler()
    tokenizer = "lab03/funcs/tokenizer.cpp"
    stemmer = "lab04/funcs/stemmer.cpp"
    targets = [
        ("build/labs_3_5/tokenization", ["lab03/tokenization.cpp", tokenizer], ()),
        ("build/labs_3_5/stemming", ["lab04/stemming.cpp", stemmer], ()),
        ("build/labs_3_5/audit", ["lab04/audit.cpp", stemmer], ()),
        ("build/labs_3_5/audit_stream", ["lab04/audit_stream.cpp", stemmer], ()),
        ("build/labs_3_5/search", ["lab04/search.cpp"], ()),
        ("build/labs_3_5/benchmark", ["lab03/benchmark.cpp", tokenizer], ()),
        ("build/labs_3_5/zipf_count", ["lab05/zipf_count.cpp"], ("-municode",) if os.name == "nt" else ()),
        ("build/labs_3_5/preprocess_stream", ["lab03/preprocess_stream.cpp", "src/ir/engine.cpp", "src/ir/extensions.cpp", "src/ir/skips.cpp", "src/ir/query_normalizer.cpp", tokenizer, stemmer], ()),
        ("build/tests_lr3_4/unit_tests", ["tests_lr3_4/unit/main.cpp", "tests_lr3_4/unit/test_tokenizer.cpp",
         "tests_lr3_4/unit/test_stemmer.cpp", tokenizer, stemmer], ()),
        ("build/tests_lr3_4/tests", ["tests_lr3_4/tests.cpp"], ()),
        ("build/tests_lr3_4/contracts", ["tests_lr3_4/contracts.cpp"], ()),
    ]
    if not args.linguistics_only:
        targets += [
            ("build/ir_engine", ["src/ir/main.cpp", "src/ir/engine.cpp", "src/ir/extensions.cpp", "src/ir/skips.cpp", "src/ir/query_normalizer.cpp", tokenizer, stemmer], ()),
            ("build/tests_lr7_13/unit_tests", ["tests_lr7_13/unit.cpp", "src/ir/engine.cpp", "src/ir/extensions.cpp", "src/ir/skips.cpp", "src/ir/query_normalizer.cpp", tokenizer, stemmer], ()),
        ]
    for output, sources, extra in targets:
        compile_one(cxx, output, sources, extra)
    # Старые CLI-тесты ожидают программы рядом со своим проверяющим.
    for name in ("tokenization", "stemming", "audit"):
        shutil.copy2(ROOT / ("build/labs_3_5/" + name + EXE), ROOT / ("build/tests_lr3_4/" + name + EXE))
    print("Сборка завершена.")


if __name__ == "__main__":
    main()
