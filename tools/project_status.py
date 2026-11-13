# Пояснение к файлу: Быстрая локальная проверка готовности к показу.
# Как читать: Проверяет наличие движка, индекса, снимка, PDF; читает последний pipeline.json. Сеть и PostgreSQL не требуются.
# Команды запуска и структура проекта: README.md.

"""Краткая сводка готовности без обращения к PostgreSQL."""
import json
from pathlib import Path
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.report_sections import report_pdf
for name in ("build/ir_engine.exe", "data/corpus/snapshot.json", "data/lab11_index/index.ir", "report.tex", report_pdf().name):
    path = ROOT / name
    print(("OK     " if path.is_file() else "НЕТ    ") + name)
stats_path = ROOT / "results/project_stats.json"
if stats_path.exists():
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    print(f"\nИсходный прогон со стеммингом: {stats['date']}; документов: {stats['documents']:,}")
pipeline = ROOT / "results/pipeline.json"
if pipeline.exists():
    print("Этап конвейера:", json.loads(pipeline.read_text(encoding="utf-8"))["current"])
print("\nКоманды: .\\project.ps1 web | tests | rebuild | export | report | upgrade | collect")
print("Инструкция и примеры запросов: README.md")
upgrade = ROOT / "results/advanced/pipeline.json"
if upgrade.exists():
    state = json.loads(upgrade.read_text(encoding="utf-8"))
    print("Новый режим (леммы/цитаты/прыжки/сниппеты):", state.get("current"))
    print("Описание: README.md; формат данных: src/ir/FORMAT.md")
