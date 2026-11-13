// Пояснение к файлу: Самостоятельный русский Snowball/Porter без STL.
// Как читать: Проверка русского слова и регистра → RV/R1/R2 → окончания по классам → и → ость в R2 → завершающая очистка. Основа не равна лемме.
// Команды запуска и структура проекта: README.md.

#include "stemmer.h"
#include <cstring>

// Самостоятельная C++ реализация правил Russian Snowball (Porter), без STL.
// https://snowballstem.org/algorithms/russian/stemmer.html
// Русские UTF-8 буквы занимают два байта; границы — смещения в байтах.
namespace {
bool russian(unsigned char a, unsigned char b) {
    return (a == 0xD0 && (b == 0x81 || (b >= 0x90 && b <= 0xBF))) ||
           (a == 0xD1 && (b == 0x91 || (b >= 0x80 && b <= 0x8F)));
}
bool vowel(const char* p) {
    static const char letters[] = "аеиоуыэюя";
    for (size_t i = 0; i < sizeof(letters) - 1; i += 2)
        if (p[0] == letters[i] && p[1] == letters[i + 1]) return true;
    return false;
}
// R1/R2 начинаются после первой пары гласная-согласная при поиске от заданной позиции; смещения измеряются в байтах.
size_t region_after_vowel_consonant(const char* word, size_t begin, size_t size) {
    for (size_t i = begin; i + 3 < size; i += 2)
        if (vowel(word + i) && !vowel(word + i + 2)) return i + 4;
    return size;
}
struct Ending { const char* text; bool after_a_ya; };
// Сначала самое длинное совпадение в классе, затем условие предшествующей буквы.
template<size_t N>
bool remove_ending(char* word, size_t& size, size_t region, const Ending (&endings)[N]) {
    size_t best = 0;
    bool conditional = false;
    for (const Ending& ending : endings) {
        const size_t length = std::strlen(ending.text);
        if (length > best && size >= length && size - length >= region &&
            std::memcmp(word + size - length, ending.text, length) == 0) {
            best = length; conditional = ending.after_a_ya;
        }
    }
    if (!best) return false;
    const size_t cut = size - best;
    if (conditional && (cut < region + 2 ||
        (std::memcmp(word + cut - 2, "а", 2) != 0 && std::memcmp(word + cut - 2, "я", 2) != 0))) return false;
    size = cut; word[size] = '\0';
    return true;
}
}
size_t count_unicode_characters(const char* word) {
    size_t count = 0;
    if (word) for (size_t i = 0; word[i]; ++i)
        if ((static_cast<unsigned char>(word[i]) & 0xC0) != 0x80) ++count;
    return count;
}
// Изменение на месте: результат не длиннее входа. Чисто русские слова проходят правила, идентификаторы и смешанные токены сохраняются.
void extract_stem(char* word) {
    if (!word) return;
    size_t size = std::strlen(word);
    // Идентификаторы, составные и смешанные слова сохраняются целиком.
    if (size % 2) return;
    for (size_t i = 0; i < size; i += 2)
        if (!russian(static_cast<unsigned char>(word[i]), static_cast<unsigned char>(word[i + 1]))) return;
    for (size_t i = 0; i < size; i += 2) {
        const auto a = static_cast<unsigned char>(word[i]);
        const auto b = static_cast<unsigned char>(word[i + 1]);
        if (a == 0xD0 && b >= 0x90 && b <= 0x9F) word[i + 1] = static_cast<char>(b + 0x20);
        else if (a == 0xD0 && b >= 0xA0 && b <= 0xAF) { word[i] = '\xD1'; word[i + 1] = static_cast<char>(b - 0x20); }
        if ((a == 0xD0 && b == 0x81) || (a == 0xD1 && b == 0x91)) { word[i] = '\xD0'; word[i + 1] = '\xB5'; }
    }
    // RV — часть после первой гласной; R2 — часть после второй пары гласная-согласная. Срез не может пересечь границу нужной области.
    size_t rv = size;
    for (size_t i = 0; i < size; i += 2) if (vowel(word + i)) { rv = i + 2; break; }
    const size_t r1 = region_after_vowel_consonant(word, 0, size);
    const size_t r2 = region_after_vowel_consonant(word, r1, size);
    // Таблицы классов окончаний Snowball; after_a_ya задаёт дополнительное условие предшествующей а/я для части правил.
    static const Ending perfective[] = {
        {"в",true},{"вши",true},{"вшись",true},{"ив",false},{"ивши",false},
        {"ившись",false},{"ыв",false},{"ывши",false},{"ывшись",false}
    };
    static const Ending reflexive[] = {{"ся",false},{"сь",false}};
    static const Ending adjective[] = {
        {"ее",false},{"ие",false},{"ые",false},{"ое",false},{"ими",false},{"ыми",false},
        {"ей",false},{"ий",false},{"ый",false},{"ой",false},{"ем",false},{"им",false},
        {"ым",false},{"ом",false},{"его",false},{"ого",false},{"ему",false},{"ому",false},
        {"их",false},{"ых",false},{"ую",false},{"юю",false},{"ая",false},{"яя",false},{"ою",false},{"ею",false}
    };
    static const Ending participle[] = {
        {"ем",true},{"нн",true},{"вш",true},{"ющ",true},{"щ",true},{"ивш",false},{"ывш",false},{"ующ",false}
    };
    static const Ending verb[] = {
        {"ла",true},{"на",true},{"ете",true},{"йте",true},{"ли",true},{"й",true},{"л",true},
        {"ем",true},{"н",true},{"ло",true},{"но",true},{"ет",true},{"ют",true},{"ны",true},
        {"ть",true},{"ешь",true},{"нно",true},
        {"ила",false},{"ыла",false},{"ена",false},{"ейте",false},{"уйте",false},{"ите",false},
        {"или",false},{"ыли",false},{"ей",false},{"уй",false},{"ил",false},{"ыл",false},
        {"им",false},{"ым",false},{"ен",false},{"ило",false},{"ыло",false},{"ено",false},
        {"ят",false},{"ует",false},{"уют",false},{"ит",false},{"ыт",false},{"ены",false},
        {"ить",false},{"ыть",false},{"ишь",false},{"ую",false},{"ю",false}
    };
    static const Ending noun[] = {
        {"а",false},{"ев",false},{"ов",false},{"ие",false},{"ье",false},{"е",false},
        {"иями",false},{"ями",false},{"ами",false},{"еи",false},{"ии",false},{"и",false},
        {"ией",false},{"ей",false},{"ой",false},{"ий",false},{"й",false},{"иям",false},
        {"ям",false},{"ием",false},{"ем",false},{"ам",false},{"ом",false},{"о",false},
        {"у",false},{"ах",false},{"иях",false},{"ях",false},{"ы",false},{"ь",false},
        {"ию",false},{"ью",false},{"ю",false},{"ия",false},{"ья",false},{"я",false}
    };
    // Шаг 1: совершенный вид ИЛИ возвратность, затем прилагательное/причастие,
    // иначе глагол, иначе существительное. Порядок классов меняет результат.
    if (!remove_ending(word, size, rv, perfective)) {
        remove_ending(word, size, rv, reflexive);
        if (remove_ending(word, size, rv, adjective)) remove_ending(word, size, rv, participle);
        else if (!remove_ending(word, size, rv, verb)) remove_ending(word, size, rv, noun);
    }
    // Шаг 2: после основного окончания удаляется оставшееся и только внутри RV.
    static const Ending i_ending[] = {{"и",false}};
    remove_ending(word, size, rv, i_ending);
    // Шаг 3: словообразовательный суффикс ост/ость допускается только в R2.
    static const Ending derivational[] = {{"ост",false},{"ость",false}};
    remove_ending(word, size, r2, derivational);
    // Шаг 4: нн → н, либо превосходная степень с повторной проверкой нн, либо мягкий знак.
    static const Ending superlative[] = {{"ейш",false},{"ейше",false}};
    static const Ending soft_sign[] = {{"ь",false}};
    if (size >= rv + 4 && std::memcmp(word + size - 4, "нн", 4) == 0) {
        size -= 2; word[size] = '\0';
    } else if (remove_ending(word, size, rv, superlative)) {
        if (size >= rv + 4 && std::memcmp(word + size - 4, "нн", 4) == 0) { size -= 2; word[size] = '\0'; }
    } else remove_ending(word, size, rv, soft_sign);
}
