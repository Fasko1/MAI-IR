// Пояснение к файлу: Минимальный интерфейс собственного unit-раннера.
// Как читать: Контекст хранит проверки текущего случая; runner запускает функции и собирает PASS/FAIL/XFAIL/XPASS. XFAIL не является успешной проверкой.
// Команды запуска и структура проекта: README.md.

#pragma once
// Небольшой собственный каркас. Нет STL, регистрации макросами и внешних библиотек.
#include <cstdio>
#include <cstring>

struct UnitContext {
    FILE* report;
    int checks = 0;
    int failed = 0;
    // Только для известного дефекта: наблюдается именно зафиксированный ответ.
    bool known_observed = false;

    void expect(bool condition, const char* description) {
        ++checks;
        if (!condition) {
            ++failed;
            std::printf("    Несовпадение: %s\n", description);
            std::fprintf(report, "    Несовпадение: %s\n", description);
        }
    }
    void expect_size(size_t actual, size_t expected, const char* description) {
        ++checks;
        if (actual != expected) {
            ++failed;
            std::printf("    Несовпадение: %s; получено=%zu, ожидалось=%zu\n", description, actual, expected);
            std::fprintf(report, "    Несовпадение: %s; получено=%zu, ожидалось=%zu\n", description, actual, expected);
        }
    }
    void expect_text(const char* actual, const char* expected, const char* description) {
        ++checks;
        if (std::strcmp(actual, expected) != 0) {
            ++failed;
            std::printf("    Несовпадение: %s; получено=«%s», ожидалось=«%s»\n", description, actual, expected);
            std::fprintf(report, "    Несовпадение: %s; получено=«%s», ожидалось=«%s»\n", description, actual, expected);
        }
    }
};

class UnitRunner {
    FILE* report_;
    FILE* json_;
    bool demo_;
    bool first_ = true;
    int pass_ = 0, fail_ = 0, xfail_ = 0, xpass_ = 0;
    int checks_ = 0;
public:
    UnitRunner(FILE* report, FILE* json, bool demo) : report_(report), json_(json), demo_(demo) {}
    void run(const char* id, const char* name, void (*test)(UnitContext&),
             bool demo = false, bool known = false);
    int finish();
};

void run_tokenizer_tests(UnitRunner& runner);
void run_stemmer_tests(UnitRunner& runner);
