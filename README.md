# MAI-IR — информационный поиск

Учебный поисковик по русскоязычным статьям о науке и технологиях.
Поисковое ядро написано на **C++17**, сбор корпуса и веб-интерфейс — на **Python**.
Индекс, структуры данных, булев поиск, ранжирование и кодек реализованы в проекте.

**Документов: 992 743 · источников: 21 · 5.25 ГБ чистого текста · 260 автоматических проверок.**

[Отчёт PDF](report.pdf) · [Исходник отчёта](report.tex) · [Формат индекса](src/ir/FORMAT.md)

Репозиторий: [Fasko1/MAI-IR](https://github.com/Fasko1/MAI-IR). Ссылка и QR-код также находятся в конце отчёта.

## Что реализовано

| ЛР | Возможность | Основной код |
|---|---|---|
| 1–2 | Корпус, поисковый робот, PostgreSQL | [lab02/crawler.py](lab02/crawler.py), [tools/collect_parallel.py](tools/collect_parallel.py) |
| 3 | Токенизация русского и латинского текста | [lab03/funcs/tokenizer.cpp](lab03/funcs/tokenizer.cpp) |
| 4 | Стемминг Snowball и словарная лемматизация OpenCorpora | [lab04/funcs/stemmer.cpp](lab04/funcs/stemmer.cpp), [src/ir/extensions.cpp](src/ir/extensions.cpp) |
| 5 | Закон Ципфа: частоты и графики | [lab05/zipf_count.cpp](lab05/zipf_count.cpp) |
| 7–8 | Бинарный индекс и булев поиск | [src/ir/engine.cpp](src/ir/engine.cpp), [lab08/web.py](lab08/web.py) |
| 10 | Строгий цитатный поиск по координатам слов | [src/ir/extensions.cpp](src/ir/extensions.cpp) |
| 11 | Сжатие разностей docID кодом Variable Byte | [src/ir/engine.cpp](src/ir/engine.cpp) |
| 12 | Прыжки по сжатым спискам документов | [src/ir/skips.cpp](src/ir/skips.cpp) |
| 13 | Ранжирование TF-IDF | [src/ir/engine.cpp](src/ir/engine.cpp) |
| 14 | Сниппеты с подсветкой исходного текста | [src/ir/extensions.cpp](src/ir/extensions.cpp), [lab08/web.py](lab08/web.py) |
| 17 | Проверки функций, CLI и HTTP | [tests](tests), [tests_lr3_4](tests_lr3_4), [tests_lr7_13](tests_lr7_13) |

## Запуск

Нужны Python 3.10+, компилятор g++ с C++17 и PowerShell. В Windows сборщик также ищет g++ в `C:/msys64/ucrt64/bin` и `C:/msys64/mingw64/bin`; другой путь можно задать переменной `CXX`.

Из корня проекта:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\project.ps1 tests
```

Тесты собирают программы и создают свои маленькие корпуса. **Полный корпус и PostgreSQL для них не нужны.**

Если готовые данные уже находятся в `data/`, запустить веб:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\project.ps1 web
```

Открыть <http://127.0.0.1:8080>. Первый запрос загружает индекс и словарь; затем используется тот же процесс. Остановка — `Ctrl+C`.

Примеры: `компьютер && процессор`, `компьютер || телефон`, `компьютер && !игра`, `"искусственный интеллект"`.
Без TF-IDF пробел означает И; с TF-IDF простой запрос находит хотя бы одно слово. Явные операторы сохраняют своё условие.

## Данные и воспроизведение

Корпус, индексы и словарь большого размера хранятся локально и **не входят в Git-репозиторий**. Клон содержит код, тестовые данные, измерения, графики и отчёт. Без внешних данных веб-поиск по полной коллекции недоступен.

Для готового расширенного поиска нужны:

```text
data/corpus/texts.tsv
data/morphology/lemmas.tsv
data/advanced/index.ir
data/advanced/positions.ir
data/advanced/skips-32.ir
data/advanced/texts.offsets
data/advanced/index.search.json
```

Для подготовки словаря OpenCorpora один раз выполнить:

```powershell
.\.venv\Scripts\python.exe -m pip install -r tools/requirements-morphology.txt
.\.venv\Scripts\python.exe tools/prepare_lemmas.py --packaged
```

Затем `project.ps1 upgrade` строит индекс лемм, координаты, прыжки и данные сниппетов по готовым `data/corpus/tokens.tsv` и `texts.tsv`. После прерывания готовые этапы используются повторно. Для принудительного пересчёта — `python -m tools.project.upgrade --force`.

Для сбора документов указать свой пароль БД в локальном `.env` строкой `POSTGRES_PASSWORD=ваш_пароль` и настроить [config.yaml](config.yaml). При установке из репозитория `.env` нужно создать самостоятельно. Сбор: `project.ps1 collect -Background`; остановка с сохранением очереди: `project.ps1 collect -Stop`.

Повторная проверка трёх сохранённых статей без обхода: `python lab02/crawler.py config.yaml --update-only --limit 3 --update-interval 0`. Интервал меняется только на этот запуск; YAML не перезаписывается. Режим выполняет реальные HTTP-запросы и обновляет записи БД по результатам проверки.

| Команда `project.ps1` | Назначение |
|---|---|
| `status` | Сохранённые измерения и готовность данных |
| `web` | Локальный поисковый интерфейс |
| `tests` | Сборка и все автономные проверки |
| `rebuild` | Базовая обработка готового снимка и индекс со стеммингом |
| `upgrade` | Леммы, координаты, прыжки и данные сниппетов |
| `export` | Новый экспорт PostgreSQL и базовая обработка |
| `report` | Единый отчёт по сохранённым результатам; для PDF нужен XeLaTeX |
| `collect -Summary` | Состояние сбора корпуса |

## Где что лежит

- `src/ir/` — общее ядро C++ и [описание бинарного формата](src/ir/FORMAT.md).
- `labXX/` — программы, настройки и результаты соответствующих лабораторных работ.
- `tools/` — сбор корпуса, сборка программ, измерения и генерация отчёта.
- `tests*/` — тесты, эталоны и журналы проверок.
- `results/` — сохранённые измерения полного прогона, включая `advanced/` для новых работ.
- `report.tex`, `report.pdf` — единый отчёт.

## Отчёт

Команда `project.ps1 report` собирает отчёт по сохранённым измерениям. Основной генератор — `tools/project/report.py`; шаблон ЛР1–2 — `tools/project/templates/labs01_02.tex`; таблицы корпуса — `tools/report_sections.py`; лемматизация, цитаты, прыжки и сниппеты — `tools/advanced_report.py`. Постоянные текстовые правки вносить в эти файлы: `report.tex` создаётся заново. Сам README формируется из `tools/project_docs.py`.

В PDF оставлены подписанные места для скриншотов. Сохранить PNG в `images/screenshots/` с именем из соответствующей рамки и повторить `project.ps1 report`. Графики Ципфа и токенизации уже включены.

При публикации через Git используются правила `.gitignore`: исходники, тесты, документация и измерения входят в репозиторий; корпус и локальные настройки хранятся отдельно.

Имена лабораторных папок сохранены по нумерации курса. Локальные `data/`, `build/`, `.venv/`, `.local/` и личные памятки `docs/` исключены через `.gitignore`. Справочные материалы и старые журналы хранятся в локальном архиве `.local/`.
