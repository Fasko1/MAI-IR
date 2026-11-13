# Пояснение к файлу: Генерация единого report.tex и PDF из реальных результатов.
# Как читать: Вспомогательные функции форматируют таблицы/рисунки; main собирает разделы и вызывает XeLaTeX. Скриншоты подставляются по имени файла.
# Стиль отчёта: описание готовой системы, методов, измерений и проверок. Личная история защиты и исправлений в текст не включается.
# Команды запуска и структура проекта: README.md.

"""Собрать редактируемый отчёт и PDF по сохранённым результатам проверок."""
import argparse
import csv
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
# Ссылка на GitHub и QR-код размещены в конце отчёта после списка источников.
sys.path.insert(0, str(ROOT))


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def tex(value):
    replacements = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
                    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(replacements.get(c, c) for c in str(value))


def num(value, digits=0):
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


def table(caption, headers, rows, widths=None):
    spec = widths or ("X" + "r" * (len(headers) - 1))
    body = r"\begin{table}[H]\centering\small" + "\n" + r"\caption{" + tex(caption) + "}\n"
    body += r"\begin{tabularx}{\linewidth}{|" + "|".join(spec) + r"|}\hline" + "\n"
    for row in [headers, *rows]:
        body += " & ".join(tex(cell) for cell in row) + r" \\\hline" + "\n"
    return body + r"\end{tabularx}\end{table}" + "\n"


def figure(path, caption):
    return r"\begin{figure}[H]\centering\includegraphics[width=0.94\linewidth]{" + path + "}" + r"\caption{" + tex(caption) + r"}\end{figure}" + "\n"


# PNG по фиксированному имени заменяет рамку; если изображения ещё нет, в PDF остаётся подписанное место.
def screenshot(name, caption):
    path = "images/screenshots/" + name + ".png"
    if (ROOT / path).is_file():
        # Кадрирование выполняет вёрстка: пиксели исходного снимка не меняются.
        layout_path = ROOT / "images/screenshots/layout.json"
        layout = json.loads(layout_path.read_text(encoding="utf-8")) if layout_path.exists() else {}
        if name in layout:
            item = layout[name]
            w, h = item["size"]
            x0, y0, x1, y1 = item["crop"]
            fractions = (x0 / w, (h - y1) / h, (w - x1) / w, y0 / h)
            dims = (r"\ScreenshotLeft", r"\ScreenshotBottom", r"\ScreenshotRight", r"\ScreenshotTop")
            body = r"\begin{figure}[H]\centering\begingroup" + "\n"
            body += r"\sbox{\ScreenshotBox}{\includegraphics{" + path + "}}\n"
            for i, (dim, fraction) in enumerate(zip(dims, fractions)):
                extent = r"\wd\ScreenshotBox" if i in (0, 2) else r"\ht\ScreenshotBox"
                body += r"\setlength{" + dim + "}{" + f"{fraction:.8f}" + extent + "}\n"
            body += (r"\includegraphics[trim={\ScreenshotLeft{} \ScreenshotBottom{} \ScreenshotRight{} \ScreenshotTop{}},clip,"
                     r"width=0.96\linewidth,height=0.70\textheight,keepaspectratio]{" + path + "}\n")
            return body + r"\endgroup\caption{" + tex(caption) + r"}\end{figure}" + "\n"
        return figure(path, caption)
    return (r"\begin{figure}[H]\centering\fbox{\parbox[c][38mm][c]{0.90\linewidth}{\centering "
            + tex("Место для скриншота: " + name + ".png")
            + r"}}\caption{" + tex(caption) + r"}\end{figure}" + "\n")


def lexical_stats():
    """Использовать проверенную сводку; частотные списки нужны только для её пересчёта."""
    cached = ROOT / "results/lexical_stats.json"
    if cached.is_file():
        saved = load("results/lexical_stats.json")
        current = load("results/project_stats.json")
        if (saved["tokens"]["total"] == current["tokens"]
                and saved["tokens"]["unique"] == current["unique_tokens"]
                and saved["stems"]["unique"] == current["unique_stems"]):
            return saved
    stats = {}
    for kind in ("tokens", "stems"):
        unique, characters, weighted, total = 0, 0, 0, 0
        with (ROOT / f"lab05/results/{kind}/freq.txt").open(encoding="utf-8") as f:
            for line in f:
                count, term = line.rstrip("\n").split(" ", 1)
                count = int(count)
                unique += 1
                characters += len(term)
                weighted += len(term) * count
                total += count
        stats[kind] = {"unique": unique, "total": total, "average_unique_length": characters / unique,
                       "average_occurrence_length": weighted / total}
    (ROOT / "results/lexical_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return stats


def suite_counts():
    counts = {
        "linguistics_unit": len(load("tests_lr3_4/results/unit_results.json")["cases"]),
        "linguistics_cli": len(load("tests_lr3_4/results/test_results.json")["cases"]),
        "linguistics_contracts": len(load("tests_lr3_4/results/contracts.json")["cases"]),
        "zipf_cli": load("tests_lr7_13/results/zipf_test_results.json")["cases"],
        "search_cli": load("tests_lr7_13/results/test_results.json")["cases"],
    }
    for name, pattern in (("search_unit", r"RESULT cases=(\d+)"), ("web", r"Ran (\d+) tests"), ("stream", r"Ran (\d+) tests"), ("collection", r"Ran (\d+) tests"), ("extensions", r"Ran (\d+) tests")):
        match = re.search(pattern, (ROOT / "tests/results" / (name + ".txt")).read_text(encoding="utf-8"))
        if not match:
            raise ValueError("Не найдено число тестов: " + name)
        counts[name] = int(match[1])
    (ROOT / "tests/results/counts.json").write_text(json.dumps({"by_group": counts, "total_cases": sum(counts.values())}, indent=2), encoding="utf-8")
    return counts


# Содержимое единого отчёта: измерения → шаблон ЛР1–2 → алгоритмы и результаты следующих работ → report.tex.
def generate():
    stats = load("results/project_stats.json")
    raw, packed = load("results/index_raw.json"), load("results/index_packed.json")
    compression = load("results/compression.json")
    benchmark = load("lab11/results/benchmark.json")
    quality = load("lab13/results/quality.json")
    lexical = lexical_stats()
    audit = load("results/stemming_audit.json")
    tests = load("tests/results/summary.json")
    if any(group["exit_code"] for group in tests):
        raise SystemExit("В отчёт нельзя записывать успешный прогон: есть непройденные группы")
    from tools.project_docs import write_readme as write_current_readme
    write_current_readme(stats, suite_counts())
    historical = ROOT / ".local/report_backups"
    historical.mkdir(parents=True, exist_ok=True)
    for name in ("report.tex", "report.pdf"):
        target = historical / ("before_update_" + name)
        if not target.exists() and (ROOT / name).exists():
            shutil.copy2(ROOT / name, target)
    from tools.report_sections import prefix
    sections = [prefix(stats, table, screenshot, num)]
    sections.append(r"""
\section{Лабораторная работа № 3\\«Токенизация»}
\sub{Задание и метод}
Токенизатор делит UTF-8 текст на слова и обозначения, приводит регистр и сохраняет порядок и повторы. Основной код: \code{lab03/funcs/tokenizer.cpp}. Один документ обрабатывается за раз.

Сохраняются русские и латинские слова, числа и буквенно-цифровые обозначения. Ведущие нули значимы: \code{007} и \code{7} --- разные токены. Дефис внутри обозначения сохраняется, например \code{COVID-19}; тире, пунктуация и эмодзи разделяют токены. Аббревиатуры приводятся к записи из списка. Стоп-слова в основном конвейере не удаляются: они нужны в том числе для точных цитат. Например, «Компьютеры, 007 и COVID-19!» превращается в последовательность «компьютеры 007 и covid-19» с учётом настроенного списка аббревиатур.
\sub{Проверки}
Тесты покрывают пустой ввод, оба алфавита, регистр, числа, ведущие нули, дефисы, длинные слова, повторы, конец файла и неверный UTF-8. Проверяются функции C++ и консольная программа. Ожидания и граничные случаи описаны в \code{tests_lr3_4/TEST_PLAN.md}.
\sub{Измерения}
""")
    sections.append(table("Статистика токенизации и стемминга", ["Показатель", "Значение"], [
        ("Документов", num(stats["documents"])), ("Объём текстов, МБ", num(stats["text_bytes"] / 1000000, 2)),
        ("Вхождений токенов", num(stats["tokens"])), ("Уникальных токенов", num(stats["unique_tokens"])),
        ("Средняя длина токена, символов (с повторами)", num(lexical["tokens"]["average_occurrence_length"], 3)),
        ("Средняя длина уникального токена, символов", num(lexical["tokens"]["average_unique_length"], 3)),
        ("Вхождений основ", num(stats["stems"])), ("Уникальных основ", num(stats["unique_stems"])),
        ("Средняя длина уникальной основы, символов", num(lexical["stems"]["average_unique_length"], 3)),
        ("Сокращение словаря после стемминга, %", num(stats["vocabulary_reduction_percent"], 2)),
        ("Число объединённых элементов словаря (V − S)", num(stats["unique_tokens"] - stats["unique_stems"])),
        ("Отношение размеров словарей V/S", num(stats["unique_tokens"] / stats["unique_stems"], 3)),
        ("Изменённых вхождений после стемминга, %", num(100 * audit["changed_fraction"], 2)),
        ("Реализаций стеммера", "1 (русский Snowball / Porter)"),
    ]))
    with (ROOT / "lab03/results/benchmark_summary.csv").open(encoding="utf-8", newline="") as f:
        tokenizer_bench = list(csv.DictReader(f))
    sections.append(table("Токенизация: медиана пяти повторов без файлового ввода-вывода", ["Текст, МБ", "Секунды", "МБ/с"], [
        (num(float(row["input_bytes"]) / 1000000, 2), num(float(row["median_seconds"]), 4),
         num(float(row["input_bytes"]) / 1000000 / float(row["median_seconds"]), 2)) for row in tokenizer_bench]))
    sections.append(figure("lab03/results/time_vs_size.png", "Время выделения токенов в зависимости от объёма входа"))
    if "preprocessing_seconds" in stats["timings"]:
        elapsed = stats["timings"]["preprocessing_seconds"]
        sections.append("Полный потоковый проход токенизации и стемминга занял " + num(elapsed, 3)
                        + " с, включая чтение и запись двух потоков. Скорость по чистому тексту: "
                        + num(stats["text_bytes"] / 1000 / elapsed, 2) + " КБ/с; "
                        + num(elapsed / (stats["text_bytes"] / 1000), 8) + " с/КБ. "
                        + "Это время двух этапов вместе; отдельный график выше измеряет только токенизатор на подвыборке, без файлового ввода-вывода.\n")
    sections.append(screenshot("lab03_tokenization", "Пример токенизации и её статистика"))
    sections.append(r"""
\sub{Выводы и ограничения}
Правила токенизации сохраняют полезные термины данного корпуса и применяются также к запросам. Полная Unicode-сегментация, распознавание химических формул и морфология латинских слов не реализованы. Это границы выбранной модели токена.

\section{Лабораторная работа № 4\\«Стемминг»}
\sub{Задание и алгоритм}
Стемминг сокращает слово по правилам русского алгоритма Snowball. Это позволяет искать близкие словоформы через общую основу. Результат может не быть словарным словом: «чтение» и «чтения» дают «чтен», «клетка» и «клетки» --- «клетк».

Код \code{lab04/funcs/stemmer.cpp} выполняет правила в заданном порядке: выделение областей слова, обработку окончаний и заключительных суффиксов. Ё приводится к е; латинские и цифровые обозначения не обрезаются русскими суффиксами. Используются собственные буферы без STL. Пакетный режим загружает настройки один раз для всех документов.
\sub{Проверки}
Результаты сверены с официальным русским словарём Snowball: 49 785 входных слов и ожидаемых основ, без расхождений. Эталоны и их происхождение находятся в \code{tests_lr3_4/reference/}. Дополнительно проверены пустые и длинные строки, ё, возвратные окончания, границы областей и смешанные обозначения.
\sub{Статистика и выводы}
Стемминг уменьшает число разных терминов. Число вхождений при этом сохраняется, поскольку стоп-слова не удаляются. Ограничение метода: он может объединить разные слова или не объединить нужные формы. Например, «клеток» не сводится к «клетк» этим алгоритмом.
\sub{Поиск по токенам и основам}
Сравнивается AND-поиск по исходным токенам и основам на двух фиксированных пулах: 16 и 14 документов. В них соответственно 5 и 6 документов отмечены как релевантные. Разметка выполнена ИИ и не проверялась независимым экспертом. Поэтому полнота ниже относится к размеченному пулу, а не ко всему корпусу. Данные эксперимента: \code{lab04/results_snowball/metrics.json}.
""")
    stemming_quality = load("lab04/results_snowball/metrics.json")
    sections.append(screenshot("lab04_stemming", "Примеры словоформ и полученных основ"))
    sections.append(table("AND-поиск: фиксированные размеченные пулы до/после Snowball", ["Запрос / режим", "Найдено", "P", "Полнота пула", "F1"], [
        (row["query"] + (" / основы" if row["mode"] == "stems" else " / токены"), row["retrieved"],
         num(row["precision"], 3), num(row["pool_recall"], 3), num(row["f1"], 3)) for row in stemming_quality["results"]]))
    sections.append(r"""
Для «межпланетных кораблей» полнота составляет 0,4 при поиске по токенам и 1 при поиске по основам; для «человекоподобных роботов» --- 0 и 1 соответственно. У первого запроса точность равна 0,4 и 0,3125 соответственно: поиск по основам находит больше документов, включая нерелевантные упоминания. Результат показывает пользу и ограничение морфологической нормализации на этих примерах.
\section{Лабораторная работа № 5\\«Закон Ципфа»}
\sub{Задание и реализация}
Закон Ципфа описывает частоты слов: небольшое число слов встречается очень часто, а большинство --- редко. График помогает проверить, похоже ли распределение собранного корпуса на распределение естественного текста.

Частоты считает \code{lab05/zipf_count.cpp}: собственная хеш-таблица накапливает все вхождения, сортировка слиянием упорядочивает термины по убыванию частоты. Python запускает счётчик и строит графики. Модель: $f(r)=f(1)/r$, где $r$ --- ранг, начиная с 1. На графике показаны первые 10 000 рангов; подсчёт выполнен для всего словаря. При равной частоте термины упорядочиваются по UTF-8.
""")
    sections.append(figure("lab05/results/tokens/zipf.png", "Распределение токенов и модель Ципфа"))
    sections.append(figure("lab05/results/stems/zipf.png", "Распределение основ и модель Ципфа"))
    sections.append(r"""
\sub{Выводы}
На графиках виден длинный хвост редких терминов. Реальные частоты отклоняются от модели: закон Ципфа описывает общую закономерность, а не точное значение для каждого слова. Тематика корпуса и объединение словоформ влияют на вид распределения.
\section{Лабораторная работа № 7\\«Булев индекс»}
\sub{Задание и структуры данных}
Обратный индекс сопоставляет каждому термину список документов, в которых он встретился. Элемент списка --- постинг: внутренний номер документа и частота термина в нём. Прямой индекс хранит исходный ID, заголовок, URL, длину и норму документа. Благодаря этому поиск не перечитывает весь корпус.

Реализация: \code{src/ir/engine.cpp}; собственные массивы, хеш-таблица, сортировка кучей и слияние серий находятся в \code{src/ir/base.h}. STL-контейнеры и готовые сортировщики в поисковом ядре не используются.

Документы обрабатываются по одному. Накопленные записи сортируются блоками, сохраняются во временные серии и затем сливаются. Параметр \code{--memory-mb 64} ограничивает полезный объём блока примерно 67,11 МБ. Он не задаёт предел всей памяти процесса: словарь, документы и резерв массивов учитываются отдельно.
\sub{Бинарный формат и надёжность}
Бинарный файл состоит из заголовка, прямого индекса, словаря терминов и списков постингов. Заголовок хранит версию, признаки формата и смещения секций. Числа записываются little-endian, строки --- в UTF-8 с длиной. Подробная схема: \code{src/ir/FORMAT.md}.

При чтении проверяются длины и смещения, версия, контрольная сумма, порядок docID и согласованность счётчиков. Готовый индекс публикуется после проверки временного файла.
\sub{План проверок и результаты}
Тестовый корпус содержит неупорядоченные внешние ID, повторения термина, пустой документ, UTF-8 метаданные и пути с пробелами. Проверяется построение в одном процессе и чтение в другом, восстановление tf/df и полного множества документов. Повреждённые и усечённые файлы должны отвергаться.
""")
    sections.append(table("Базовый несжатый индекс со стеммингом", ["Показатель", "Значение"], [
        ("Документов", num(raw["documents"])), ("Терминов-основ", num(raw["terms"])),
        ("Средняя длина уникального термина индекса, символов", num(raw["average_term_chars"], 3)),
        ("Средняя длина токена с учётом повторов, символов", num(lexical["tokens"]["average_occurrence_length"], 3)),
        ("Постингов (пар термин–документ)", num(raw["postings"])),
        ("Размер базового индекса, МБ", num(raw["bytes"] / 1000000, 3)),
        ("Постинги, МБ", num(raw["posting_bytes"] / 1000000, 3)),
        ("Построение с процессом и проверкой, с", num(stats["timings"]["index_seconds"], 3)),
        ("Время на документ, мс", num(1000 * stats["timings"]["index_seconds"] / stats["documents"], 4)),
        ("Время на КБ текста, мс", num(1000 * stats["timings"]["index_seconds"] / (stats["text_bytes"] / 1000), 5)),
    ]))
    sections.append("Средняя длина уникального термина и средняя длина токена с повторами считаются по-разному. Во втором случае частые слова имеют больший вес.\n")

    sections.append(table("Построение на подмножествах реальных документов", ["Документов", "Время, с", "Размер, МБ"], [
        (num(row["documents"]), num(row["seconds"], 3), num(row["bytes"] / 1000000, 3)) for row in benchmark["scaling"]]))
    sections.append("Подкорпусы состоят из первых уникальных документов снимка. Полная точка взята из основного построения. Разная длина статей влияет на время обработки.\n")

    sections.append(r"""
\sub{Выводы и ограничения}
На реальных подмножествах измерены время и размер индекса. При дальнейшем увеличении корпуса растут словарь, число временных серий и объём слияния, поэтому время нельзя надёжно предсказать простым умножением.
\section{Лабораторная работа № 8\\«Булев поиск»}
\sub{Грамматика и исполнение}
CLI читает запросы по строке из stdin и возвращает JSONL в stdout. Пробел и \code{&&} означают И, \code{||} --- ИЛИ, \code{!} --- НЕ, поддержаны скобки и словесные AND/OR/NOT. Приоритет: НЕ, затем И, затем ИЛИ. Отрицание берётся относительно всех документов, включая пустые. Неизвестный термин соответствует пустому множеству. Пустой запрос возвращает пустую выдачу. Ошибка одной строки возвращает JSON с полем \code{error} и не останавливает обработку следующих строк.

Документы и запрос обрабатываются одинаковым нормализатором: в базовом режиме --- стеммером, в расширенном --- словарём лемм. Пересечение, объединение и разность выполняются над отсортированными массивами docID. Без ранжирования результаты упорядочены по внешнему ID как строки; этот порядок не отражает релевантность.
\sub{HTML-интерфейс}
\code{lab08/web.py} использует стандартный HTTP-сервер Python как обвязку C++-поиска. На начальной странице и странице результатов доступны форма и выбор TF-IDF; выводятся заголовки, URL, ID и по 50 результатов. Кнопки следующих/предыдущих 50 передают \code{--offset} ядру. HTML экранируется. Автоматические HTTP-тесты проверяют форму, поиск, страницы 50+6 результатов, отображение ошибок и метаданные. Сервис запускается локально командой \code{python lab08/web.py}, адрес \url{http://127.0.0.1:8080}.
\sub{Проверка правильности и времени}
Независимый эталон строится стандартными множествами в Python только в тестах, не в поисковой системе. Проверены операции, приоритеты, двойное отрицание, 80 воспроизводимо сгенерированных выражений, неизвестные слова, ошибки и продолжение потока. На настоящем корпусе исполняется пакет простых и сложных запросов; полные результаты сравниваются между бинарным и сжатым индексами.
""")
    sections.append(table("Поиск: медиана трёх пакетов запросов", ["Индекс / режим", "Запросов", "мс/запрос"], [
        (row["index"] + (" / TF-IDF" if row["ranked"] else " / булев"), row["queries"],
         num(row["median_seconds_per_query"] * 1000, 3)) for row in benchmark["query_benchmark"]]))
    sections.append(r"""
Приведённое время включает запуск процесса, загрузку индекса, пакет запросов и вывод до 50 результатов. Веб держит C++-процесс открытым: загрузка происходит при первом запросе, затем данные используются повторно. Для проверки сжатия полные выдачи исходного и сжатого индексов сравниваются отдельно по SHA-256.
\section{Лабораторная работа № 11\\«Сжатие индекса»}
\sub{Метод и формат}
В возрастающем списке docID первое число кодируется как $docID+1$, последующие --- как положительные промежутки. TF хранится отдельным целым. Используется Variable Byte: семь младших информационных бит на байт, группы идут от младших к старшим; старший бит последнего байта равен 1. Например, $0\mapsto\texttt{80}$, $127\mapsto\texttt{FF}$, $128\mapsto\texttt{00 81}$ (шестнадцатерично). Словарь, заголовки и URL остаются без сжатия. Несжатая база сравнения содержит пары 32-битных целых, а не заведомо невыгодный текстовый файл.

Метод выбран за простоту декодирования, побайтовое чтение и отсутствие потерь. Отвергаются переполнение, незавершённое число, лишние группы, невозможные интервалы и несогласованные длины. Проверяются границы 127/128, 16 383/16 384 и большие разрывы на отдельном синтетическом корпусе; этот корпус не включён в заявленное число настоящих статей.
""")
    sections.append(table("Сжатие: общий индекс и списки постингов", ["Часть", "Без сжатия, МБ", "Сжато, МБ"], [
        ("Полный индекс", num(raw["bytes"] / 1000000, 3), num(packed["bytes"] / 1000000, 3)),
        ("Постинги", num(raw["posting_bytes"] / 1000000, 3), num(packed["posting_bytes"] / 1000000, 3)),
        ("Остальные секции", num((raw["bytes"] - raw["posting_bytes"]) / 1000000, 3),
         num((packed["bytes"] - packed["posting_bytes"]) / 1000000, 3)),
    ]))
    sections.append("Отношение полного сжатого размера к исходному: " + num(stats["packed_to_raw_ratio"], 4) +
                    ". Время преобразования с проверкой: " + num(stats["timings"]["compression_seconds"], 3) + " с.\n")
    import statistics
    block_rows = []
    for group, label in (("rare", "Редкие, df ≤ 2"), ("medium", "Средние, 3 ≤ df ≤ 100"),
                         ("frequent", "Частые, df > 100"), ("all", "Все блоки")):
        samples = {mode: [sample["groups"][group] for sample in benchmark["posting_passes"][mode]] for mode in ("raw", "packed")}
        block_rows.append((label, num(samples["raw"][0]["bytes"] / 1000000, 3),
                           num(samples["packed"][0]["bytes"] / 1000000, 3),
                           num(statistics.median(x["seconds"] for x in samples["raw"]) * 1000, 3),
                           num(statistics.median(x["seconds"] for x in samples["packed"]) * 1000, 3)))
    sections.append(table("Проход постингов: размеры (МБ) и медиана чтения/декодирования (мс)",
                          ["Группа", "raw, МБ", "VB, МБ", "raw, мс", "VB, мс"], block_rows))
    sections.append(r"""
\sub{Проверки скорости и сохранения качества}
Отдельно измерены чтение и декодирование редких, средних и частых списков. Для выбранных запросов полностью совпали документы, порядок и баллы TF-IDF до и после сжатия. Значит, сжатие сохранило результат поиска. Измерения и контрольные суммы: \code{lab11/results/benchmark.json}.
\sub{Выводы}
Маленькие разности docID и частоты занимают меньше байтов. Общий выигрыш ограничен словарём и метаданными, которые остаются несжатыми. Скорость зависит от затрат на чтение и декодирование; результат приведён в таблице измерений.
\section{Лабораторная работа № 13\\«TF-IDF»}
\sub{Формула и семантика}
Пусть $N$ --- число документов, $df_t$ --- число документов с термином $t$, $tf_{t,d}$ --- число его вхождений. Принят сглаженный вариант IDF и логарифмический TF:
\[
\operatorname{idf}_t=\ln(1+N/df_t),\qquad
w_{t,d}=(1+\ln tf_{t,d})\operatorname{idf}_t,\quad tf_{t,d}>0.
\]
Отсутствующий термин имеет нулевой вес. Норма документа учитывает весь словарь, а не только слова текущего запроса. Запрос взвешивается так же с учётом повторов. Балл --- косинус:
\[
\operatorname{score}(q,d)=\frac{\sum_t w_{t,q}w_{t,d}}
{\sqrt{\sum_t w_{t,q}^2}\sqrt{\sum_t w_{t,d}^2}}.
\]
Сглаживание выбрано, чтобы термин во всех документах не получал нулевой IDF. Логарифм ослабляет влияние повторений, L2-норма --- влияние длины. Это конкретный вариант TF-IDF, а не единственная допустимая формула. Для нулевой нормы балл равен нулю, при равных баллах используется внешний ID.

Простой запрос из слов через пробелы использует OR-кандидатов. Наличие явного булева оператора включает строгое булево условие; затем совпадения ранжируются. Отрицательные ветви ограничивают множество документов и не добавляют положительного веса; двойное отрицание восстанавливает положительный вклад. Поэтому «клетки иммунитет» допускает неполное соответствие, а «клетки \&\& иммунитет» требует оба термина.
\sub{Проверки и оценка качества}
На малом корпусе TF, DF, IDF, полные L2-нормы и итоговые баллы рассчитаны независимыми формулами. Проверены редкие и частые термины, повторы в запросе, ничьи, отрицания и неизвестный термин. Оценка качества использует два запроса и 30 пар запрос--документ с ИИ-разметкой без независимой человеческой проверки. Для булевого и ранжированного режимов явно задаётся одинаковое OR-множество кандидатов. Поиск и веса вычисляются по полному индексу, затем результаты фильтруются до фиксированного размеченного пула каждого запроса. IDF и нормы не пересчитываются по маленькому пулу. Разметка фиксирована до вычисления метрик.

Ниже метрики диагностического пула при $k=5$: $P@k$ использует знаменатель $k$; $DCG=\sum(2^{rel}-1)/\log_2(i+1)$; NDCG делит DCG на идеальное значение; ERR использует бинарную шкалу и вероятность удовлетворения $(2^{rel}-1)/2$. Эти метрики характеризуют фиксированный размеченный пул, а не весь корпус. Для полной коллекции неизвестные оценки сохраняются неизвестными; охват и нижняя граница точности записаны отдельно в \code{lab13/results/quality.json}.
""")
    sections.append(table("Ограниченная оценка на фиксированном размеченном пуле, k=5", ["Запрос / режим", "P@5", "DCG@5", "NDCG@5", "ERR@5"], [
        (row["query"] + (" / TF-IDF" if row["mode"] == "tfidf" else " / ID"),
         num(row["precision"], 3), num(row["dcg"], 3), num(row["ndcg"], 3), num(row["err"], 3))
        for row in quality["metrics"] if row["k"] == 5]))
    for example in quality["full_corpus"]:
        sections.append(table("Пример полного поиска: " + example["query"], ["ID / заголовок", "TF-IDF"],
            [(row["id"] + " / " + row["title"], num(row["score"], 5)) for row in example["results"]["results"]]))
    for qid in sorted({row["query_id"] for row in quality["metrics"]}):
        pair = {row["mode"]: row for row in quality["metrics"] if row["query_id"] == qid and row["k"] == 5}
        sections.append(tex('Запрос «' + pair["tfidf"]["query"] + '»: NDCG@5 по фиксированному пулу составляет '
                            + num(pair["boolean"]["ndcg"], 3) + ' в булевом режиме и '
                            + num(pair["tfidf"]["ndcg"], 3) + ' при ранжировании TF-IDF.') + "\n")
    sections.append(screenshot("lab13_tfidf", "Пример ранжированной выдачи"))
    sections.append(r"""
\sub{Выводы и ограничения}
TF-IDF ставит выше документы с более характерными словами запроса, но не понимает смысл текста. Случайные упоминания и многозначные слова могут дать нерелевантный результат. Метрики рассчитаны на двух пулах с ИИ-разметкой без независимой проверки и не характеризуют весь корпус. Ещё 18 примеров сохранены в \code{lab13/results/examples.json}.
\section{Лабораторная работа № 17\\«Автотесты»}
\sub{Основания тестового плана}
Проверки покрывают отдельные функции C++, запуск консольных программ и HTTP-интерфейс. Ожидания получены из правил токенизации, эталонного словаря Snowball, операций над множествами и ручного расчёта весов TF-IDF. Проверяются обычные, граничные и ошибочные входы. Маленькие тестовые корпуса создаются автоматически; настоящий сетевой сбор не запускается.
\sub{Воспроизводимый прогон}
Команда \code{project.ps1 rebuild} собирает программы, запускает проверки, пересчитывает существующий экспорт корпуса, строит индексы, измеряет показатели и формирует этот отчёт. База для этого не требуется. \code{project.ps1 tests} выполняет только сборку и проверки; ошибки дают ненулевой код и сохраняются по группам. Логи находятся в \code{tests/results/}, подробные случаи --- в \code{tests_lr3_4/results/} и \code{tests_lr7_13/results/}.
""")
    sections.append(table("Покрытие компонентов автоматическими проверками",
        ["Компонента", "Что проверялось", "Уровень"], [
            ("Токенизация", "Регистр, ё, разделители, дефисы, аббревиатуры, ведущие нули, повторы и пустой ввод", "UNIT, CLI"),
            ("Стемминг", "Области и порядок правил, словоформы, смешанные токены, граница буфера, 49 785 эталонных пар", "UNIT, CLI"),
            ("Индекс", "Малый корпус, метаданные, пустые документы, дубли ID и повреждённые файлы", "CLI"),
            ("Булев поиск", "AND/OR/NOT, приоритеты, скобки, неизвестные термины, дополнение относительно всего корпуса", "CLI"),
            ("TF-IDF", "Независимый расчёт весов и норм, DF, повторы, отрицание, порядок результатов", "CLI"),
            ("Сжатие", "Обратимость и границы VB, ошибки кодека, совпадение постингов и полных выдач raw/VB", "UNIT, CLI, полный корпус"),
            ("Веб", "Форма, режимы, пагинация, экранирование HTML, ссылки и ошибки запроса", "HTTP"),
            ("Частоты", "Контрольные CF, порядок при равенстве, чтение строк и рост словаря", "CLI"),
            ("Потоки", "Эквивалентность файловому пути, метаданные, выравнивание токенов и основ", "CLI"),
            ("Робот и конфиг", "Лимиты, извлечение, сетевые ошибки, очередь, восстановление и настройки источников", "Python unittest"),
            ("Лемматизация", "Словарные формы, неизвестные токены, сохранение идентификаторов и числа вхождений", "CLI"),
            ("Цитаты", "Полная последовательность, порядок, повторы, пропуски, неизвестное слово, NOT/OR и независимый последовательный эталон", "CLI"),
            ("Прыжки", "Перезапуск VB на шагах 16/32/64, совпадение ответа с обычным пересечением, повреждение контрольной суммы", "CLI"),
            ("Сниппеты", "Исходные словоформы, подсветка совпадений и экранирование HTML", "CLI, HTML"),
        ], widths=[r"p{0.18\linewidth}", "X", r"p{0.17\linewidth}"]))
    counts = suite_counts()
    sections.append(table("Фактический результат общего запуска", ["Группа", "Сценариев", "Код", "Время, с"], [
        (row["group"], counts[row["group"]], row["exit_code"], num(row["seconds"], 3)) for row in tests]))
    sections.append(r"""
\sub{Ограничения и итог проверки}
Все группы завершились кодом 0. Это подтверждает прохождение приведённого плана проверок. Подробные сценарии: \code{tests_lr3_4/TEST_PLAN.md} и \code{tests_lr7_13/TEST_PLAN.md}. Структура проекта и команды запуска приведены в \code{README.md}.
\section{Итоговые результаты и развитие системы}
Реализован полный путь от сбора статей до веб-поиска: токенизация, стемминг и лемматизация, частотный анализ, бинарный индекс, булевы операции, строгие цитаты, сжатие, прыжки, TF-IDF и сниппеты. Этапы согласованы по ID, числу вхождений и контрольным суммам. Полные выдачи до и после сжатия совпали.

Дальнейшее улучшение --- независимая разметка большего числа запросов, разрешение неоднозначных слов по контексту и более информативный выбор фрагментов. Численные результаты сохранены в JSON/CSV, исходник отчёта --- \code{report.tex}.
\clearpage
\section{Источники и материалы}
\begin{enumerate}
\item Учебные материалы курса «Информационный поиск»: задания и лекции об индексации, сжатии и текстовом ранжировании. Планы проверок приведены в \code{tests_lr3_4/TEST_PLAN.md} и \code{tests_lr7_13/TEST_PLAN.md}.
\item Snowball: описание русского алгоритма и официальный тестовый словарь: \url{https://snowballstem.org/algorithms/russian/stemmer.html}; \url{https://github.com/snowballstem/snowball-data/tree/master/russian}.
\item OpenCorpora: словарные данные русского языка, \url{https://opencorpora.org/}. Происхождение данных записывается при подготовке словаря.
\item Manning, Raghavan, Schütze. Introduction to Information Retrieval: переменные байтовые коды и TF-IDF, главы 5--6: \url{https://nlp.stanford.edu/IR-book/}.
\item Собственный корпус: источники перечислены в таблице текущего снимка; фактические выводы C++-программ, JSON/CSV результатов и журналы автотестов в приложении к проекту.
\end{enumerate}
\sub{Репозиторий проекта}
Исходный код, результаты проверок и отчёт доступны в репозитории:
\begin{center}
\url{https://github.com/Fasko1/MAI-IR}\par\vspace{3mm}
\qrcode[height=23mm]{https://github.com/Fasko1/MAI-IR}
\end{center}
\end{document}
""")
    body = "\n".join(sections)
    for marker, name, caption in (
        (r"\section{Лабораторная работа № 11", "lab08_boolean", "Булева выдача по запросу «компьютер && процессор»"),
        (r"\section{Лабораторная работа № 13", "lab11_compression", "Проверка индекса и статистика сжатия"),
        (r"\section{Итоговые результаты и развитие", "lab17_tests", "Результаты автоматических проверок"),
    ):
        body = body.replace(marker, screenshot(name, caption) + "\n" + marker)
    from tools.advanced_report import add_sections
    body = add_sections(body, table, num, screenshot)
    if (ROOT / "data/advanced/index.search.json").is_file():
        body = body.replace("позиционный поиск и построение информативных сниппетов", "контекстное разрешение морфологической неоднозначности и улучшение выбора фрагментов")
    (ROOT / "report.tex").write_text(body, encoding="utf-8")


# Запускает generate и три прохода XeLaTeX для оглавления/ссылок; при занятом PDF использует резервное имя.
def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--skip-pdf", action="store_true")
    args = p.parse_args()
    generate()
    if not args.skip_pdf:
        xelatex = shutil.which("xelatex")
        if not xelatex:
            raise SystemExit("Обновлён report.tex; для обязательного report.pdf нужен XeLaTeX.")
        logs = ROOT / "build/report"
        logs.mkdir(parents=True, exist_ok=True)
        for repeat in (1, 2, 3):
            result = subprocess.run([xelatex, "-interaction=nonstopmode", "-halt-on-error", "-output-directory=" + str(logs), "report.tex"], cwd=ROOT,
                                    capture_output=True, encoding="utf-8", errors="replace")
            (logs / f"xelatex_{repeat}.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
            if result.returncode:
                raise SystemExit("Ошибка XeLaTeX: подробности в build/report/xelatex_" + str(repeat) + ".txt")
        pdf = ROOT / "report.pdf"
        try:
            shutil.copy2(logs / "report.pdf", pdf)
        except PermissionError:
            pdf = ROOT / "report_unified.pdf"
            shutil.copy2(logs / "report.pdf", pdf)
            print("report.pdf занят просмотрщиком; новая версия сохранена отдельно.")
    print("Отчёт обновлён: report.tex" + (" и " + pdf.name if not args.skip_pdf else ""))


if __name__ == "__main__":
    main()
