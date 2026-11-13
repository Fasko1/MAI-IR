// Пояснение к файлу: Интерфейс токенизатора для CLI, индекса и тестов.
// Как читать: Объявления функций и типов; реализация правил находится в tokenizer.cpp. Словарь whitelist задаёт каноническую запись аббревиатур.
// Команды запуска и структура проекта: README.md.

#ifndef TOKENIZER_H
#define TOKENIZER_H
#include <string>
#include <vector>
// А–Я, а–я, Ё, ё; соседние кириллические буквы других языков не принимаются.
bool valid_cyrillic(unsigned char first, unsigned char second);
bool has_triple_repeat(const std::string& text); // диагностика, не фильтр
std::string to_lowercase(const std::string& input);
bool qualifies_as_russian(const std::string& word);
// Непустая последовательность ASCII-цифр, включая ведущие нули и длинные идентификаторы.
bool is_plain_number(const std::string& text);
size_t utf8_length(const std::string& text);
bool allowed_char(unsigned char current, unsigned char next = 0);
// Русские/ASCII латинские буквы и цифры, внутренние дефисы. Порядок и повторы сохраняются.
// whitelist задаёт каноническое написание терминов; остальные слова также принимаются.
std::vector<std::string> split_into_tokens(const std::string& content,
                                         const std::vector<std::string>& whitelist = {});
#endif
