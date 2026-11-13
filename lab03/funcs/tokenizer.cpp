// Пояснение к файлу: Правила выделения токенов из русского/ASCII-текста.
// Как читать: Распознавание UTF-8 букв → регистр → дефисы → накопление токена и flush. 007 сохраняется; повтор букв сам по себе не удаляет слово.
// Команды запуска и структура проекта: README.md.

#include "tokenizer.h"
#include <unordered_map>

// Проверяем ровно русские буквы в двухбайтовом UTF-8; это не обещание поддержки всех Unicode-алфавитов.
bool valid_cyrillic(unsigned char a, unsigned char b) {
    return (a == 0xD0 && (b == 0x81 || (b >= 0x90 && b <= 0xBF))) ||
           (a == 0xD1 && (b == 0x91 || (b >= 0x80 && b <= 0x8F)));
}

// Диагностика повторов, не основание удалять термин.
bool has_triple_repeat(const std::string& text) {
    std::string previous;
    size_t repeats = 0;
    for (size_t i = 0; i < text.size();) {
        size_t width = i + 1 < text.size() && valid_cyrillic(static_cast<unsigned char>(text[i]),
                         static_cast<unsigned char>(text[i + 1])) ? 2 : 1;
        const auto current = text.substr(i, width);
        repeats = current == previous ? repeats + 1 : 1;
        if (repeats >= 3) return true;
        previous = current; i += width;
    }
    return false;
}
size_t utf8_length(const std::string& text) {
    size_t count = 0;
    for (unsigned char c : text) if ((c & 0xC0) != 0x80) ++count;
    return count;
}
bool is_plain_number(const std::string& text) {
    if (text.empty()) return false;
    for (unsigned char c : text) if (c < '0' || c > '9') return false;
    return true;
}
bool qualifies_as_russian(const std::string& word) {
    if (word.empty()) return false;
    bool letter = false;
    for (size_t i = 0; i < word.size();) {
        if (word[i] == '-') {
            if (!letter || i + 1 == word.size() || word[i + 1] == '-') return false;
            letter = false; ++i;
        } else if (i + 1 < word.size() && valid_cyrillic(static_cast<unsigned char>(word[i]),
                                                       static_cast<unsigned char>(word[i + 1]))) {
            letter = true; i += 2;
        } else return false;
    }
    return letter;
}
// Понижаем регистр ASCII и русских букв вручную по байтам UTF-8; ё сохраняется до отдельной нормализации стеммера.
std::string to_lowercase(const std::string& input) {
    std::string output;
    output.reserve(input.size());
    for (size_t i = 0; i < input.size(); ++i) {
        const auto a = static_cast<unsigned char>(input[i]);
        if (a >= 'A' && a <= 'Z') output += static_cast<char>(a + ('a' - 'A'));
        else if (a == 0xD0 && i + 1 < input.size()) {
            const auto b = static_cast<unsigned char>(input[i + 1]);
            if (b >= 0x90 && b <= 0x9F) { output += '\xD0'; output += static_cast<char>(b + 0x20); ++i; }
            else if (b >= 0xA0 && b <= 0xAF) { output += '\xD1'; output += static_cast<char>(b - 0x20); ++i; }
            else if (b == 0x81) { output += "ё"; ++i; }
            else output += input[i];
        } else output += input[i];
    }
    return output;
}
bool allowed_char(unsigned char c, unsigned char next) {
    return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || valid_cyrillic(c, next);
}
static size_t character_width(const std::string& text, size_t i) {
    if (i >= text.size()) return 0;
    const auto c = static_cast<unsigned char>(text[i]);
    const auto next = i + 1 < text.size() ? static_cast<unsigned char>(text[i + 1]) : 0;
    return allowed_char(c, next) ? (c < 128 ? 1 : 2) : 0;
}
// U+002D, U+2010, U+2011 соединяют компоненты; тире разделяет слова.
static size_t hyphen_width(const std::string& text, size_t i) {
    if (text[i] == '-') return 1;
    if (i + 2 < text.size() && static_cast<unsigned char>(text[i]) == 0xE2 &&
        static_cast<unsigned char>(text[i + 1]) == 0x80 &&
        (static_cast<unsigned char>(text[i + 2]) == 0x90 || static_cast<unsigned char>(text[i + 2]) == 0x91)) return 3;
    return 0;
}
// Буфер накапливает разрешённые символы; разделитель вызывает flush. Дефис соединяет только непустые компоненты.
std::vector<std::string> split_into_tokens(const std::string& content, const std::vector<std::string>& whitelist) {
    std::unordered_map<std::string, std::string> canonical;
    for (const auto& term : whitelist) if (!term.empty()) canonical.emplace(to_lowercase(term), term);
    std::vector<std::string> result;
    std::string buffer;
    // Завершение токена: регистр → каноническая аббревиатура из whitelist → результат. Числовые обозначения не отбрасываются.
    const auto flush = [&]() {
        if (buffer.empty()) return;
        const auto lower = to_lowercase(buffer);
        const auto found = canonical.find(lower);
        result.push_back(found == canonical.end() ? lower : found->second);
        buffer.clear();
    };
    for (size_t i = 0; i < content.size();) {
        const size_t width = character_width(content, i);
        if (width) { buffer.append(content, i, width); i += width; continue; }
        const size_t hyphen = hyphen_width(content, i);
        if (hyphen && !buffer.empty() && character_width(content, i + hyphen)) { buffer += '-'; i += hyphen; }
        else { flush(); i += hyphen ? hyphen : 1; }
    }
    flush();
    return result;
}
