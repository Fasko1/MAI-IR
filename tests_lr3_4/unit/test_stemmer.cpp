// Пояснение к файлу: Независимые примеры и эталонные пары для русского стеммера.
// Как читать: expect_stem проверяет результат и границу буфера; далее семейства правил; official_reference сверяет 49 785 пар; run_stemmer_tests регистрирует случаи.
// Команды запуска и структура проекта: README.md.

// Прямые вызовы собственного extract_stem, без STL.
#include "unit_check.h"
#include "../../lab04/funcs/stemmer.h"

namespace {
// Копируем вход, вызываем реальную функцию и проверяем и ответ, и сторожевой байт за исходной строкой.
void expect_stem(UnitContext& t, const char* word, const char* expected) {
    char buffer[512];
    const size_t bytes = std::strlen(word);
    t.expect(bytes + 2 <= sizeof(buffer), "Размер тестового буфера");
    if (bytes + 2 > sizeof(buffer)) return;
    std::memcpy(buffer, word, bytes + 1);
    buffer[bytes + 1] = '!';
    extract_stem(buffer);
    t.expect_text(buffer, expected, word);
    t.expect(buffer[bytes + 1] == '!', "Не записываем за концом входного буфера");
}
// Граничные входы: пустая строка и nullptr должны обрабатываться без аварии.
void empty(UnitContext& t) { expect_stem(t, "", ""); extract_stem(nullptr); t.expect(true, "nullptr допускается"); }
// Короткие слова показывают защиту области RV: совпавшие буквы ещё не означают допустимое окончание.
void short_words(UnitContext& t) {
    expect_stem(t, "я", "я"); expect_stem(t, "он", "он"); expect_stem(t, "дом", "дом");
    expect_stem(t, "ся", "ся"); expect_stem(t, "сь", "сь"); expect_stem(t, "мг", "мг");
}
// Словоформы чтение/чтения/чтению объединяются в чтен; читать относится к другой основе и не обязано совпадать.
void reading_family(UnitContext& t) {
    // Snowball: классы окончаний проверяются по порядку в RV; затем удаляется и.
    expect_stem(t, "чтение", "чтен"); expect_stem(t, "чтения", "чтен");
    expect_stem(t, "чтению", "чтен"); expect_stem(t, "чтением", "чтен");
    // Алгоритм не обещает объединять словообразование и чередование основ.
    expect_stem(t, "читать", "чита"); expect_stem(t, "читает", "чита");
}
// Проверка одинаковой основы для падежных форм длинного существительного.
void programming_family(UnitContext& t) {
    expect_stem(t, "программирование", "программирован");
    expect_stem(t, "программирования", "программирован");
    expect_stem(t, "программированием", "программирован");
}
// Падежные формы существительных; ожидается алгоритмическая основа, которая может не быть словарным словом.
void noun_forms(UnitContext& t) {
    expect_stem(t, "станция", "станц"); expect_stem(t, "станции", "станц");
    expect_stem(t, "клетки", "клетк"); expect_stem(t, "клетка", "клетк");
    expect_stem(t, "нейроны", "нейрон"); expect_stem(t, "нейронов", "нейрон");
}
// Разные окончания прилагательных должны давать одинаковую основу внутри семейства.
void adjective_forms(UnitContext& t) {
    expect_stem(t, "космический", "космическ"); expect_stem(t, "космические", "космическ");
    expect_stem(t, "красивыми", "красив"); expect_stem(t, "красивый", "красив");
}
// Сочетание возвратности и условных окончаний проверяет порядок классов правил.
void conditional_and_reflexive(UnitContext& t) {
    expect_stem(t, "бегавшая", "бега"); expect_stem(t, "прочитав", "прочита");
    expect_stem(t, "умывался", "умыва"); expect_stem(t, "умывать", "умыва");
}
// Суффикс ость удаляется только в R2: справедливость и новость специально дают разные случаи.
void derivational_region(UnitContext& t) {
    // Ость удаляется только целиком внутри R2; в новости это условие не выполнено.
    expect_stem(t, "справедливость", "справедлив");
    expect_stem(t, "новость", "новост"); expect_stem(t, "новости", "новост");
}
// Последняя стадия Snowball: превосходная степень и удвоенное н после предыдущих сокращений.
void tidy_up(UnitContext& t) {
    expect_stem(t, "длинный", "длин"); expect_stem(t, "красивейший", "красив");
    expect_stem(t, "сильнейший", "сильн");
}
// Регистр и ё/е нормализуются до удаления окончаний.
void normalization(UnitContext& t) {
    expect_stem(t, "ЧТЕНИЕ", "чтен"); expect_stem(t, "ЁЛКА", "елк"); expect_stem(t, "ёлки", "елк");
}
// Числа, латиница и составные идентификаторы должны пережить стемминг без потери содержимого.
void non_russian(UnitContext& t) {
    expect_stem(t, "007", "007"); expect_stem(t, "10000", "10000"); expect_stem(t, "GPU", "GPU");
    expect_stem(t, "ВИЧ-1", "ВИЧ-1"); expect_stem(t, "G-белок", "G-белок"); expect_stem(t, "слово123", "слово123");
}
// Фиксируем ограничения алгоритма: он не разбирает чередования и не добавляет словарные исключения.
void limits(UnitContext& t) {
    expect_stem(t, "клеток", "клеток"); expect_stem(t, "стали", "стал");
    // Не существует словарных исключений для произвольно выбранных глаголов.
    expect_stem(t, "быть", "быт"); expect_stem(t, "знать", "знат"); expect_stem(t, "дать", "дат");
}
// Проверяем число Unicode-символов отдельно от длины UTF-8 в байтах.
void count_chars(UnitContext& t) {
    t.expect_size(count_unicode_characters(""), 0, "Пустая строка");
    t.expect_size(count_unicode_characters("ёж"), 2, "Две русских буквы");
    t.expect_size(count_unicode_characters("GPU"), 3, "ASCII");
}
// Независимый эталон: пары из официального набора Snowball, а не ответы, полученные тем же кодом.
void official_reference(UnitContext& t) {
    FILE* vocabulary = std::fopen("tests_lr3_4/reference/snowball_voc.txt", "rb");
    FILE* expected = std::fopen("tests_lr3_4/reference/snowball_expected.txt", "rb");
    t.expect(vocabulary && expected, "Официальные контрольные файлы Snowball доступны");
    if (!vocabulary || !expected) { if (vocabulary) std::fclose(vocabulary); if (expected) std::fclose(expected); return; }
    char word[1024], stem[1024], original[1024];
    size_t count = 0;
    while (std::fgets(word, sizeof(word), vocabulary)) {
        if (!std::fgets(stem, sizeof(stem), expected)) { t.expect(false, "Эталон короче входа"); break; }
        word[std::strcspn(word, "\r\n")] = 0;
        stem[std::strcspn(stem, "\r\n")] = 0;
        std::strcpy(original, word);
        extract_stem(word);
        t.expect_text(word, stem, original);
        ++count;
    }
    t.expect_size(count, 49785, "Все 49 785 независимых эталонных пар проверены");
    t.expect(!std::fgets(stem, sizeof(stem), expected), "Эталон не длиннее входа");
    t.expect(!std::ferror(vocabulary) && !std::ferror(expected), "Нет ошибок чтения эталона");
    std::fclose(vocabulary); std::fclose(expected);
}
}
// Регистрация случаев с ID/пояснением; флаг demo оставляет самые наглядные случаи для короткого показа.
void run_stemmer_tests(UnitRunner& r) {
    r.run("U_S01", "Пустая строка и nullptr", empty);
    r.run("U_S02", "Область RV защищает короткие слова и отдельное ся", short_words, true);
    r.run("U_S03", "Семейство чтение и граница возможностей стемминга", reading_family, true);
    r.run("U_S04", "Словоформы программирования", programming_family);
    r.run("U_S05", "Существительные и научные термины", noun_forms, true);
    r.run("U_S06", "Длинное окончание прилагательного", adjective_forms);
    r.run("U_S07", "Условные окончания и возвратность", conditional_and_reflexive);
    r.run("U_S08", "Ограничение деривационного суффикса областью R2", derivational_region);
    r.run("U_S09", "Превосходная степень и двойная н", tidy_up);
    r.run("U_S10", "Регистр и нормализация ё", normalization);
    r.run("U_S11", "Сохранение чисел и научных идентификаторов", non_russian);
    r.run("U_S12", "Чередование основы, омонимия и отсутствие словарных исключений", limits, true);
    r.run("U_S13", "Подсчёт символов UTF-8", count_chars);
    r.run("U_S14", "Весь официальный контрольный словарь Snowball", official_reference);
}
