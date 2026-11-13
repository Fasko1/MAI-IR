# Пояснение к файлу: Построение графиков Ципфа по посчитанным C++ частотам.
# Как читать: Чтение freq.txt/stats.json → ранги и аппроксимация C/r → PNG. Python не заменяет C++ алгоритм подсчёта частот.
# Команды запуска и структура проекта: README.md.

"""ЛР5: C++ считает частоты и сортирует; Python собирает, запускает и рисует."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent.parent


def compile_counter():
    compiler = shutil.which("g++")
    if not compiler:
        raise SystemExit('Не найден g++. В PowerShell: $env:Path = "C:\\msys64\\ucrt64\\bin;$env:Path"')
    binary = ROOT / "build/labs_3_5/zipf_count.exe"
    binary.parent.mkdir(parents=True, exist_ok=True)
    command = [compiler, "-std=c++17", "-O2", "-Wall", "-Wextra",
               "-finput-charset=UTF-8", "-fexec-charset=UTF-8"]
    if sys.platform == "win32":
        command.append("-municode")
    command += [str(ROOT / "lab05/zipf_count.cpp"), "-o", str(binary)]
    subprocess.run(command, check=True)
    return binary


def plot(out, label, stats):
    # Частоты уже посчитаны и отсортированы в C++; читаем только часть для графика.
    values = []
    with (out / "freq.txt").open(encoding="utf-8") as f:
        for line in f:
            if len(values) >= stats["plotted_ranks"]:
                break
            freq, _ = line.rstrip("\n").split(" ", 1)
            values.append(int(freq))
    if len(values) != stats["plotted_ranks"] or not values:
        raise SystemExit("Частотный файл не соответствует статистике")
    freqs = np.array(values, dtype=float)
    ranks = np.arange(1, len(freqs) + 1)
    theoretical = freqs[0] / ranks
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.loglog(ranks, freqs, ".", markersize=3, label="Фактические частоты")
    ax.loglog(ranks, theoretical, "-", label="Модель f(r) = f(1) / r")
    ax.set(xlabel="Ранг (логарифмическая шкала)", ylabel="Частота (логарифмическая шкала)",
           title=f"Закон Ципфа: {label}")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "zipf.png", dpi=180)
    plt.close(fig)


def build(input_dir, suffix, out, label, binary=None):
    # Python только перечисляет пути; содержимое и частоты обрабатывает C++.
    files = sorted(p.resolve() for p in input_dir.glob("*" + suffix) if p.is_file())
    if not files:
        raise SystemExit(f"Нет входных файлов {suffix} в {input_dir}")
    if any("\n" in str(p) or "\r" in str(p) for p in files):
        raise SystemExit("Перевод строки в имени файла не поддерживается")
    out.mkdir(parents=True, exist_ok=True)
    work = ROOT / "build/labs_3_5"
    work.mkdir(parents=True, exist_ok=True)
    listing = work / ("zipf_stems_inputs.txt" if suffix == ".stems" else "zipf_tokens_inputs.txt")
    listing.write_text("\n".join(str(p) for p in files) + "\n", encoding="utf-8")
    binary = binary or compile_counter()
    subprocess.run([str(binary), str(listing), str(out / "freq.txt"), str(out / "stats.json")], check=True)
    stats = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    plot(out, label, stats)
    print(f"{label}: документов {stats['documents']}, вхождений {stats['total_terms']}, уникальных {stats['unique_terms']}")
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stems-dir", type=Path, default=ROOT / "data/lab04_stems")
    parser.add_argument("--tokens-dir", type=Path, default=ROOT / "data/lab03_tokens")
    parser.add_argument("--results-dir", type=Path, default=ROOT / "lab05/results")
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()
    binary = ROOT / "build/labs_3_5/zipf_count.exe"
    if not binary.exists():
        binary = compile_counter()
    if args.stream:
        for kind in ("tokens", "stems"):
            out = args.results_dir / kind
            out.mkdir(parents=True, exist_ok=True)
            subprocess.run([str(binary), "--inline", str(ROOT / f"data/corpus/{kind}.tsv"),
                            str(out / "freq.txt"), str(out / "stats.json")], check=True)
            plot(out, "токены" if kind == "tokens" else "основы слов", json.loads((out / "stats.json").read_text(encoding="utf-8")))
        return
    build(args.tokens_dir, ".tokens", args.results_dir / "tokens", "токены без удаления стоп-слов", binary)
    build(args.stems_dir, ".stems", args.results_dir / "stems", "стеммы", binary)


if __name__ == "__main__":
    main()
