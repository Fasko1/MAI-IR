# Пояснение к файлу: Организация замеров токенизации и построение графика.
# Как читать: Подвыборки → запуск C++ benchmark → повторы/CSV → time_vs_size.png. Это отдельный эксперимент, не время полного конвейера.
# Команды запуска и структура проекта: README.md.

# Запуск C++ замеров, расчёт медианы пяти повторов и построение графика.
import csv
import subprocess
from pathlib import Path
import statistics
import argparse
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
ROOT = Path(__file__).resolve().parent.parent

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=ROOT / "data/lab03_texts")
    args = parser.parse_args()
    out = ROOT / "lab03/results"
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(args.input_dir.glob("*.txt"), key=lambda p: int(p.stem))
    listing = out / "inputs.txt"
    listing.write_text("\n".join(str(p) for p in files), encoding="utf-8")
    raw = out / "benchmark_raw.csv"
    subprocess.run([str(ROOT / "build/labs_3_5/benchmark.exe"), str(listing), str(ROOT / "lab03/abbrevs.txt"), str(raw)], check=True)
    # Объединяем пять измерений для каждого размера входного текста.
    groups = {}
    for row in csv.DictReader(raw.open(encoding="utf-8")):
        groups.setdefault(int(row["input_bytes"]), []).append(row)
    summary = []
    for n, rows in sorted(groups.items()):
        # Медиана уменьшает влияние случайных задержек отдельных запусков.
        elapsed = statistics.median(float(row["seconds"]) for row in rows)
        summary.append({"input_bytes": n, "input_kib": n / 1024, "median_seconds": elapsed,
                        "kib_per_second": n / 1024 / elapsed, "seconds_per_kib": elapsed / (n / 1024),
                        "tokens": int(rows[0]["tokens"]), "average_token_length": float(rows[0]["average_token_length"])})
    with (out / "benchmark_summary.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0]))
        writer.writeheader(); writer.writerows(summary)
    # График показывает зависимость времени алгоритма от объёма текста.
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot([row["input_bytes"] / 1000 for row in summary], [row["median_seconds"] for row in summary], "o-")
    ax.set(xlabel="Объём текста, КБ", ylabel="Время токенизации, с", title="Токенизация: медиана пяти измерений")
    ax.grid(alpha=.3);fig.tight_layout();fig.savefig(out / "time_vs_size.png", dpi=180);plt.close(fig)
    print("Замеры алгоритма: lab03/results/benchmark_summary.csv")

if __name__ == "__main__":
    main()
