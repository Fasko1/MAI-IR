// Пояснение к файлу: Исполнитель прямых C++ unit-тестов ЛР3–4.
// Как читать: UnitContext считает проверки, UnitRunner объединяет случаи; main запускает обе группы и пишет отчёты. --demo выбирает короткую демонстрацию.
// Команды запуска и структура проекта: README.md.

#include "unit_check.h"
#include <cerrno>
#ifdef _WIN32
#include <windows.h>
#include <direct.h>
#else
#include <sys/stat.h>
#endif

static void quoted(FILE* f, const char* text) {
    std::fputc('"', f);
    for (const unsigned char* p = reinterpret_cast<const unsigned char*>(text); *p; ++p) {
        if (*p == '"' || *p == '\\') { std::fputc('\\', f); std::fputc(*p, f); }
        else if (*p < 32) std::fprintf(f, "\\u%04x", *p);
        else std::fputc(*p, f);
    }
    std::fputc('"', f);
}

void UnitRunner::run(const char* id, const char* name, void (*test)(UnitContext&),
                     bool demo, bool known) {
    if (demo_ && !demo) return;
    std::printf("[%s] %s\n", id, name);
    std::fprintf(report_, "[%s] %s\n", id, name);
    UnitContext context{report_};
    // ПРЯМОЙ ВЫЗОВ тестовой функции в текущем процессе. Нет system и файлов входа.
    test(context);
    checks_ += context.checks;
    const char* status;
    if (context.checks == 0) { status = "FAIL"; ++fail_; }
    else if (context.failed == 0) {
        if (known) { status = "XPASS"; ++xpass_; }
        else { status = "PASS"; ++pass_; }
    } else if (known && context.known_observed && context.failed == 1) {
        status = "XFAIL"; ++xfail_;
    } else { status = "FAIL"; ++fail_; }
    std::printf("    %s; проверок внутри случая: %d\n\n", status, context.checks);
    std::fprintf(report_, "    %s; проверок внутри случая: %d\n\n", status, context.checks);
    if (!first_) std::fputs(",\n", json_);
    first_ = false;
    std::fputs("{\"id\":", json_); quoted(json_, id);
    std::fputs(",\"name\":", json_); quoted(json_, name);
    std::fputs(",\"status\":", json_); quoted(json_, status);
    std::fprintf(json_, ",\"checks\":%d,\"failed_checks\":%d}", context.checks, context.failed);
}

int UnitRunner::finish() {
    std::printf("UNIT итог: PASS=%d, FAIL=%d, XFAIL=%d, XPASS=%d; проверок=%d\n",
                pass_, fail_, xfail_, xpass_, checks_);
    std::fprintf(report_, "UNIT итог: PASS=%d, FAIL=%d, XFAIL=%d, XPASS=%d; проверок=%d\n",
                 pass_, fail_, xfail_, xpass_, checks_);
    std::fprintf(json_, "\n],\"summary\":{\"PASS\":%d,\"FAIL\":%d,\"XFAIL\":%d,\"XPASS\":%d,\"checks\":%d}}\n",
                 pass_, fail_, xfail_, xpass_, checks_);
    return fail_ || xpass_ ? 1 : 0;
}

int main(int argc, char** argv) {
#ifdef _WIN32
    SetConsoleOutputCP(CP_UTF8);
#endif
    bool demo = argc == 2 && std::strcmp(argv[1], "--demo") == 0;
    if (argc != 1 && !demo) {
        std::fprintf(stderr, "Usage: unit_tests.exe [--demo]\n"); return 2;
    }
#ifdef _WIN32
    int made = _mkdir("tests_lr3_4/results");
#else
    int made = mkdir("tests_lr3_4/results", 0755);
#endif
    if (made && errno != EEXIST) { std::perror("results"); return 2; }
    const char* txt = demo ? "tests_lr3_4/results/unit_demo.txt" : "tests_lr3_4/results/unit_results.txt";
    const char* js = demo ? "tests_lr3_4/results/unit_demo.json" : "tests_lr3_4/results/unit_results.json";
    FILE* report = std::fopen(txt, "wb");
    FILE* json = std::fopen(js, "wb");
    if (!report || !json) {
        if (report) std::fclose(report);
        if (json) std::fclose(json);
        std::fprintf(stderr, "Не удалось открыть отчёты. Запускай из корня MAI-IR.\n"); return 2;
    }
    std::printf("UNIT: прямые вызовы функций проекта, C++\n\n");
    std::fprintf(report, "UNIT: прямые вызовы функций проекта, C++; compiler=%s\n\n", __VERSION__);
    std::fputs("{\"runner\":\"C++ direct unit tests\",\"compiler\":", json);
    quoted(json, __VERSION__); std::fputs(",\"cases\":[\n", json);
    UnitRunner runner(report, json, demo);
    run_tokenizer_tests(runner);
    run_stemmer_tests(runner);
    int result = runner.finish();
    bool bad = std::ferror(report) || std::ferror(json);
    if (std::fclose(report)) bad = true;
    if (std::fclose(json)) bad = true;
    if (bad) { std::fprintf(stderr, "Ошибка записи отчётов\n"); return 2; }
    std::printf("Отчёт: %s\nXFAIL — известный дефект, не успешная проверка.\n", txt);
    return result;
}
