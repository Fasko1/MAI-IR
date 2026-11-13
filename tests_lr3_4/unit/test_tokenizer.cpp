// Пояснение к файлу: Прямые проверки правил токенизатора на граничных входах.
// Как читать: Вспомогательное сравнение списков → сценарии букв/чисел/дефисов/аббревиатур → регистрация случаев в run_tokenizer_tests.
// Команды запуска и структура проекта: README.md.

#include "unit_check.h"
#include "../../lab03/funcs/tokenizer.h"
#include <initializer_list>
#include <fstream>
#include <iterator>

namespace {
// Один вызов рабочего токенизатора: сравниваем число токенов, их содержимое и исходный порядок.
void expect_tokens(UnitContext& t, const char* input, std::initializer_list<const char*> expected,
                   const std::vector<std::string>& whitelist = {}) {
    const auto actual = split_into_tokens(input, whitelist);
    t.expect_size(actual.size(), expected.size(), "Количество токенов");
    size_t i = 0;
    for (const char* word : expected) {
        if (i < actual.size()) t.expect_text(actual[i].c_str(), word, "Токен по порядку");
        ++i;
    }
}
// Пустота и одни разделители не должны порождать пустые токены.
void empty(UnitContext& t) { expect_tokens(t, "", {}); expect_tokens(t, " ,.!\t\n--", {}); }
// Проверяем таблицу регистра русских и ASCII-букв, включая отдельный случай Ё.
void lowercase(UnitContext& t) {
    expect_tokens(t, "Мама, мыла! DNA", {"мама", "мыла", "dna"});
    t.expect_text(to_lowercase("АБВГДЕЖЗИЙКЛМНОП РСТУФХЦЧШЩЪЫЬЭЮЯ Ё ABC").c_str(),
                  "абвгдежзийклмноп рстуфхцчшщъыьэюя ё abc", "Регистр русского и ASCII алфавитов");
}
// Токенизатор сохраняет ё; переход к е относится к стеммеру. Длина здесь в символах, не байтах.
void yo(UnitContext& t) {
    expect_tokens(t, "ЁЛКА ёж", {"ёлка", "ёж"});
    t.expect_size(utf8_length("ёлка"), 4, "Символы UTF-8");
    t.expect(valid_cyrillic(0xD0, 0x81) && valid_cyrillic(0xD1, 0x91), "Обе буквы Ё/ё допустимы");
    t.expect(!valid_cyrillic(0xD1, 0x94), "Украинская є не входит в русский алфавит");
}
// Пробелы, перевод строки, emoji и неразрывный пробел завершают слово; последний токен сохраняется без LF.
void separators(UnitContext& t) {
    expect_tokens(t, "мама\tмыла\nраму\r\n", {"мама", "мыла", "раму"});
    expect_tokens(t, "раму", {"раму"});
    expect_tokens(t, "мама🙂мыла\xC2\xA0раму", {"мама", "мыла", "раму"});
}
// Повторы и стоп-слова сохраняются: их частоты понадобятся для TF и статистики.
void repetitions(UnitContext& t) { expect_tokens(t, "и мама мама", {"и", "мама", "мама"}); }
// Внутренние дефисы соединяют компоненты, тире и двойной дефис служат разделителями.
void hyphens(UnitContext& t) {
    expect_tokens(t, "по-русски я-то научно-научно-научный --мама--",
                  {"по-русски", "я-то", "научно-научно-научный", "мама"});
    expect_tokens(t, "слово--слово слово—слово слово–слово", {"слово", "слово", "слово", "слово", "слово", "слово"});
    expect_tokens(t, "COVID‐19 COVID‑19", {"covid-19", "covid-19"});
}
// Цифровая запись остаётся строкой: ведущие нули и длина идентификатора не теряются.
void identifiers(UnitContext& t) {
    expect_tokens(t, "Агент 007 0 00 7 10000 123456789012345678901234567890",
                  {"агент", "007", "0", "00", "7", "10000", "123456789012345678901234567890"});
    t.expect(is_plain_number("007") && is_plain_number("10000"), "Идентификаторы не преобразуются в int");
    t.expect(!is_plain_number("") && !is_plain_number("-1") && !is_plain_number("7a"), "Проверка класса ASCII цифр");
    const auto result = split_into_tokens("007 7");
    t.expect(result.size() == 2 && result[0] != result[1], "007 и 7 различаются для поиска");
}
void decimal_policy(UnitContext& t) {
    expect_tokens(t, "3.14 -12 12.1.007-76", {"3", "14", "12", "12", "1", "007-76"});
}
void scientific_terms(UnitContext& t) {
    expect_tokens(t, "высокопроизводительный дезоксирибонуклеиновой урааа касса",
                  {"высокопроизводительный", "дезоксирибонуклеиновой", "урааа", "касса"});
    expect_tokens(t, "DNA p53 IL-6 ВИЧ-1 G-белок", {"dna", "p53", "il-6", "вич-1", "g-белок"});
    t.expect(qualifies_as_russian("высокопроизводительный"), "Нет фильтра длины научных слов");
}
void canonical(UnitContext& t) {
    expect_tokens(t, "crispr covid-19 gpu unknown", {"CRISPR", "COVID-19", "GPU", "unknown"},
                  {"CRISPR", "COVID-19", "GPU"});
    expect_tokens(t, "CRISPR COVID-19 GPU", {"crispr", "covid-19", "gpu"});
}
void malformed(UnitContext& t) {
    const std::string input = std::string("мама") + char(0xD0) + " мыла " + char(0xFF) + "раму";
    const auto result = split_into_tokens(input);
    t.expect(result == std::vector<std::string>({"мама", "мыла", "раму"}), "Невалидные байты разделяют, не склеивают соседние слова");
}
void properties(UnitContext& t) {
    const std::vector<std::string> whitelist = {"GPU", "COVID-19"};
    const char* inputs[] = {"Мама", "--мама--", "я-то", "2026", "007", "GPU", "урааа", "12000–14000", "p53", "COVID‑19"};
    for (const char* input : inputs) {
        const auto first = split_into_tokens(input, whitelist);
        t.expect(first == split_into_tokens(std::string(input) + "\n", whitelist), "Конец текста равносилен разделителю");
        for (const auto& token : first) {
            const auto again = split_into_tokens(token, whitelist);
            t.expect(again.size() == 1 && again[0] == token, "Повторная токенизация не меняет результат");
        }
    }
}
void corpus_regressions(UnitContext& t) {
    struct Sample { const char* path; const char* excerpt; const char* token; };
    const Sample samples[] = {
        {"data/lab03_texts/1688.txt", "статус агента 007", "007"},
        {"data/lab03_texts/2740.txt", "ГОСТ 12.1.007-76", "007-76"},
        {"data/lab03_texts/1.txt", "Геном человека содержит 12000–14000 генов", "12000"},
        {"data/lab03_texts/1.txt", "высокопроизводительный скрининг", "высокопроизводительный"}
    };
    for (const auto& sample : samples) {
        std::ifstream f(sample.path, std::ios::binary);
        // В архиве с исходниками полного корпуса нет: для автономных тестов
        // сохранены короткие выдержки с зафиксированным происхождением.
        if (!f) {
            const char* name = sample.path;
            for (const char* p = sample.path; *p; ++p) if (*p == '/' || *p == '\\') name = p + 1;
            f.clear();
            f.open(std::string("tests_lr3_4/reference/corpus/") + name + ".excerpt", std::ios::binary);
        }
        t.expect(bool(f), "Источник корпусной регрессии существует");
        if (!f) continue;
        const std::string document(std::istreambuf_iterator<char>(f), {});
        t.expect(document.find(sample.excerpt) != std::string::npos, "Пример действительно встречается в собственном корпусе");
        const auto tokens = split_into_tokens(sample.excerpt);
        bool found = false;
        for (const auto& token : tokens) if (token == sample.token) found = true;
        t.expect(found, "Значимый токен из корпуса сохранён");
    }
}
}
void run_tokenizer_tests(UnitRunner& r) {
    r.run("U_T01", "Пустой ввод", empty);
    r.run("U_T02", "Регистровая нормализация", lowercase);
    r.run("U_T03", "Русский алфавит и ё", yo);
    r.run("U_T04", "Разделители, UTF-8 и конец текста", separators);
    r.run("U_T05", "Порядок, повторы и стоп-слова", repetitions);
    r.run("U_T06", "Внутренний дефис и типографское тире", hyphens);
    r.run("U_T07", "Агент 007 и числовые идентификаторы", identifiers, true);
    r.run("U_T08", "Документированное разбиение десятичной записи", decimal_policy);
    r.run("U_T09", "Научные слова, латиница и обозначения", scientific_terms, true);
    r.run("U_T10", "Каноническое написание по словарю", canonical, true);
    r.run("U_T11", "Повреждённые UTF-8 байты", malformed);
    r.run("U_T12", "Свойства повторной токенизации и конца текста", properties);
    r.run("U_T13", "Регрессии с подтверждением по своему корпусу", corpus_regressions, true);
}
