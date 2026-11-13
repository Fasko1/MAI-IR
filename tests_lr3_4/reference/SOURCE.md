# Источник независимого эталона

Алгоритм описан в [Russian stemming algorithm, Snowball](https://snowballstem.org/algorithms/russian/stemmer.html).
Реализация `lab04/funcs/stemmer.cpp` написана в этом проекте по опубликованным правилам, без подключения libstemmer и без копирования реализации другой студенческой работы.

Два файла ниже — открытые контрольные **данные**, а не исходный код. Они получены 23.10.2026 из репозитория Snowball:

- `snowball_voc.txt`: [russian/voc.txt](https://github.com/snowballstem/snowball-data/blob/6387ae1ae578792d87989d33a1ec61bd33e8b164/russian/voc.txt).
- `snowball_expected.txt`: [russian/output.txt](https://github.com/snowballstem/snowball-data/blob/6387ae1ae578792d87989d33a1ec61bd33e8b164/russian/output.txt).
- Лицензионные условия сохранены в `COPYING` (источник: корневой файл COPYING репозитория snowball-data).

Всего 49 785 строк в каждом файле; строки с одинаковыми номерами образуют пару «слово — ожидаемая основа». Контрольные суммы SHA-256 проверяет скрипт `run_tests_3_4.ps1` до сборки и запуска:

```text
snowball_voc.txt      6E4CD2ED5C908FE3D4CABAA74A088D9043CB626417C1EADC2C157CAFBF9772A0
snowball_expected.txt C6265F9071E41B24590D567EA69181F5781790930907C77B28044F1D71B1BAB3
```

Тест не генерирует ожидаемый ответ из проверяемого алгоритма: он читает заранее опубликованный независимый эталон. Полное совпадение показывает соответствие выбранным формальным правилам. Оно не означает, что для всех запросов максимальна релевантность: омонимия и чередование основ требуют отдельной оценки поиска.
