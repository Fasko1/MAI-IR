// Пояснение к файлу: CLI-проверки контрактов токенизации и стемминга.
// Как читать: Создаёт входные файлы → запускает реальные программы → проверяет выход, ошибки и согласованность. Отличается от прямых unit-вызовов.
// Команды запуска и структура проекта: README.md.

// Проверка файлового контракта и ошибок настоящих CLI, без STL.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cerrno>
#ifdef _WIN32
#include <direct.h>
#include <windows.h>
#else
#include <sys/stat.h>
#endif
static FILE* report;
static FILE* json;
static int passed = 0, failed = 0;
static void check(const char* id, bool ok, const char* reason) {
    std::printf("[%s] %s: %s\n", ok ? "PASS" : "FAIL", id, reason);
    std::fprintf(report, "[%s] %s: %s\n", ok ? "PASS" : "FAIL", id, reason);
    if (passed + failed) std::fputs(",\n", json);
    std::fprintf(json, "{\"id\":\"%s\",\"status\":\"%s\"}", id, ok ? "PASS" : "FAIL");
    if (ok) ++passed; else ++failed;
}
static bool write(const char* path, const char* text) {
    FILE* f = std::fopen(path, "wb");
    if (!f) return false;
    const bool ok = std::fputs(text, f) >= 0;
    return std::fclose(f) == 0 && ok;
}
static bool equals(const char* path, const char* expected) {
    FILE* f = std::fopen(path, "rb");
    if (!f) return false;
    size_t i = 0;
    int c;
    bool same = true;
    while ((c = std::fgetc(f)) != EOF) {
        if (!expected[i] || c != static_cast<unsigned char>(expected[i])) { same = false; break; }
        ++i;
    }
    if (expected[i] || std::ferror(f)) same = false;
    return std::fclose(f) == 0 && same;
}
static bool contains(const char* path, const char* fragment) {
    FILE* f = std::fopen(path, "rb");
    if (!f) return false;
    char buffer[2048];
    const size_t count = std::fread(buffer, 1, sizeof(buffer) - 1, f);
    buffer[count] = 0;
    const bool ok = !std::ferror(f) && std::strstr(buffer, fragment);
    return std::fclose(f) == 0 && ok;
}
static int run(const char* command) {
#ifdef _WIN32
    char native[4096];
    if (std::strlen(command) >= sizeof(native)) return -1;
    std::strcpy(native, command);
    for (size_t i = 0; native[i] && native[i] != ' '; ++i) if (native[i] == '/') native[i] = '\\';
    return std::system(native);
#else
    return std::system(command);
#endif
}
static void directory(const char* path) {
#ifdef _WIN32
    int result = _mkdir(path);
#else
    int result = mkdir(path, 0755);
#endif
    if (result && errno != EEXIST) { std::perror(path); std::exit(2); }
}
int main() {
#ifdef _WIN32
    SetConsoleOutputCP(CP_UTF8);
#endif
    directory("build/tests_lr3_4/work");
    directory("build/tests_lr3_4/work/contracts");
    directory("build/tests_lr3_4/work/contracts/texts");
    directory("build/tests_lr3_4/work/contracts/tokens");
    report = std::fopen("tests_lr3_4/results/contracts.txt", "wb");
    json = std::fopen("tests_lr3_4/results/contracts.json", "wb");
    if (!report || !json) return 2;
    std::fputs("{\"runner\":\"C++ CLI contracts\",\"cases\":[\n", json);
    check("C01", run("build/tests_lr3_4/stemming.exe > build/tests_lr3_4/work/contracts/log.txt 2>&1") != 0,
          "Отсутствующие аргументы завершаются ошибкой");
    check("C02", run("build/tests_lr3_4/stemming.exe build/tests_lr3_4/work/contracts/absent.tokens build/tests_lr3_4/work/contracts/out.stems > build/tests_lr3_4/work/contracts/log.txt 2>&1") != 0,
          "Отсутствующий вход стеммера завершается ошибкой");
    check("C03", run("build/tests_lr3_4/tokenization.exe build/tests_lr3_4/work/contracts/texts build/tests_lr3_4/work/contracts/tokens build/tests_lr3_4/work/contracts/absent.list > build/tests_lr3_4/work/contracts/log.txt 2>&1") != 0,
          "Отсутствующий канонический словарь не игнорируется");
    bool setup = write("build/tests_lr3_4/work/contracts/texts/1.txt", "") &&
                 write("build/tests_lr3_4/work/contracts/tokens/1.tokens", "old-data\n");
    check("C04", setup && run("build/tests_lr3_4/tokenization.exe build/tests_lr3_4/work/contracts/texts build/tests_lr3_4/work/contracts/tokens lab03/abbrevs.txt > build/tests_lr3_4/work/contracts/log.txt 2>&1") == 0 &&
          equals("build/tests_lr3_4/work/contracts/tokens/1.tokens", ""), "Пустой документ удаляет устаревшие токены предыдущего запуска");
    setup = write("build/tests_lr3_4/work/contracts/one.tokens", "чтение\nчтения\n007\nи\n") &&
            write("build/tests_lr3_4/work/contracts/two.tokens", "") &&
            write("build/tests_lr3_4/work/contracts/manifest.tsv", "101\tbuild/tests_lr3_4/work/contracts/one.tokens\tTitle\thttps://example.test/\n102\tbuild/tests_lr3_4/work/contracts/two.tokens\tEmpty\t\n");
    check("C05", setup && run("build/tests_lr3_4/stemming.exe --batch build/tests_lr3_4/work/contracts/manifest.tsv build/tests_lr3_4/work/contracts/batch > build/tests_lr3_4/work/contracts/log.txt 2>&1") == 0 &&
          equals("build/tests_lr3_4/work/contracts/batch/101.stems", "чтен\nчтен\n007\nи\n") &&
          equals("build/tests_lr3_4/work/contracts/batch/102.stems", ""), "Batch обрабатывает два документа, пустой файл и метаданные в manifest");
    check("C06", run("build/tests_lr3_4/stemming.exe --batch build/tests_lr3_4/work/contracts/manifest.tsv build/tests_lr3_4/work/contracts/batch lab04/stop_words_ru.txt > build/tests_lr3_4/work/contracts/log.txt 2>&1") == 0 &&
          equals("build/tests_lr3_4/work/contracts/batch/101.stems", "чтен\nчтен\n007\n"), "Batch применяет тот же опциональный STOPLIST");
    setup = write("build/tests_lr3_4/work/contracts/bad.tsv", "../escape\tbuild/tests_lr3_4/work/contracts/one.tokens\n");
    check("C07", setup && run("build/tests_lr3_4/stemming.exe --batch build/tests_lr3_4/work/contracts/bad.tsv build/tests_lr3_4/work/contracts/batch > build/tests_lr3_4/work/contracts/log.txt 2>&1") != 0,
          "Manifest отклоняет нецифровой id до записи файла");
    check("C08", run("build/tests_lr3_4/stemming.exe build/tests_lr3_4/work/contracts/one.tokens build/tests_lr3_4/work/contracts/out.stems build/tests_lr3_4/work/contracts/absent.list > build/tests_lr3_4/work/contracts/log.txt 2>&1") != 0,
          "Отсутствующий STOPLIST завершается ошибкой");
    // Большая строка действительно вынуждает read_line несколько раз увеличить буфер.
    char* long_word = static_cast<char*>(std::malloc(16385));
    setup = long_word != nullptr;
    if (long_word) {
        for (size_t i = 0; i < 16384; i += 4) std::memcpy(long_word + i, "аб", 4);
        long_word[16384] = 0;
        setup = write("build/tests_lr3_4/work/contracts/long.tokens", long_word);
        char* expected = static_cast<char*>(std::malloc(16386));
        if (!expected) setup = false;
        else {
            std::memcpy(expected, long_word, 16384); expected[16384] = '\n'; expected[16385] = 0;
            check("C09", setup && run("build/tests_lr3_4/stemming.exe build/tests_lr3_4/work/contracts/long.tokens build/tests_lr3_4/work/contracts/long.stems > build/tests_lr3_4/work/contracts/log.txt 2>&1") == 0 &&
                  equals("build/tests_lr3_4/work/contracts/long.stems", expected), "Строка 16 КиБ не усекается фиксированным буфером");
            std::free(expected);
        }
        std::free(long_word);
    }
    if (!setup) check("C_SETUP", false, "Подготовка длинного входа");
    setup = write("build/tests_lr3_4/work/contracts/tokens.tsv",
                  "101\tbuild/tests_lr3_4/work/contracts/one.tokens\n102\tbuild/tests_lr3_4/work/contracts/two.tokens\n") &&
            write("build/tests_lr3_4/work/contracts/stems.tsv",
                  "101\tbuild/tests_lr3_4/work/contracts/batch/101.stems\n102\tbuild/tests_lr3_4/work/contracts/batch/102.stems\n");
    check("C10", setup && run("build/tests_lr3_4/audit.exe build/tests_lr3_4/work/contracts/tokens.tsv build/tests_lr3_4/work/contracts/stems.tsv build/tests_lr3_4/work/contracts/audit.json > build/tests_lr3_4/work/contracts/log.txt 2>&1") != 0,
          "Audit выявляет потерянный токен при несовпадении политики STOPLIST");
    check("C11", run("build/tests_lr3_4/stemming.exe --batch build/tests_lr3_4/work/contracts/manifest.tsv build/tests_lr3_4/work/contracts/batch > build/tests_lr3_4/work/contracts/log.txt 2>&1") == 0 &&
          run("build/tests_lr3_4/audit.exe build/tests_lr3_4/work/contracts/tokens.tsv build/tests_lr3_4/work/contracts/stems.tsv build/tests_lr3_4/work/contracts/audit.json > build/tests_lr3_4/work/contracts/log.txt 2>&1") == 0 &&
          contains("build/tests_lr3_4/work/contracts/audit.json", "\"documents\": 2") &&
          contains("build/tests_lr3_4/work/contracts/audit.json", "\"tokens\": 4") &&
          contains("build/tests_lr3_4/work/contracts/audit.json", "\"changed_occurrences\": 2") &&
          contains("build/tests_lr3_4/work/contracts/audit.json", "\"mean_token_length\": 4") &&
          contains("build/tests_lr3_4/work/contracts/audit.json", "\"mean_stem_length\": 3"),
          "Audit точно считает изменённые вхождения и длины Unicode");
    setup = write("build/tests_lr3_4/work/contracts/wrong_ids.tsv", "103\tbuild/tests_lr3_4/work/contracts/batch/101.stems\n");
    check("C12", setup && run("build/tests_lr3_4/audit.exe build/tests_lr3_4/work/contracts/tokens.tsv build/tests_lr3_4/work/contracts/wrong_ids.tsv build/tests_lr3_4/work/contracts/audit.json > build/tests_lr3_4/work/contracts/log.txt 2>&1") != 0,
          "Audit отклоняет разные id или порядок документов в manifest");
    std::fprintf(json, "\n],\"summary\":{\"PASS\":%d,\"FAIL\":%d}}\n", passed, failed);
    std::fprintf(report, "Итог: PASS=%d FAIL=%d\n", passed, failed);
    bool io_error = std::ferror(report) || std::ferror(json);
    if (std::fclose(report)) io_error = true;
    if (std::fclose(json)) io_error = true;
    return failed || io_error ? 1 : 0;
}
