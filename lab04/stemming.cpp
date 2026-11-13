// Пояснение к файлу: CLI-оболочка стеммера для одного файла и пакетного манифеста.
// Как читать: Чтение токенов → необязательный стоп-лист → extract_stem → запись основ. На основном пути проекта стоп-фильтр выключен.
// Команды запуска и структура проекта: README.md.

// Потоковый стемминг UTF-8 токенов; файловая обвязка без STL.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cerrno>
#ifdef _WIN32
#include <direct.h>
#else
#include <sys/stat.h>
#endif
#include "funcs/stemmer.h"

static void* allocate(void* old, size_t size) {
    void* p = std::realloc(old, size);
    if (!p) { std::fputs("Out of memory\n", stderr); std::exit(1); }
    return p;
}
static char* read_line(FILE* f) {
    size_t cap = 128, n = 0;
    char* p = static_cast<char*>(allocate(nullptr, cap));
    int c;
    while ((c = std::fgetc(f)) != EOF && c != '\n') {
        if (n + 1 >= cap) { cap *= 2; p = static_cast<char*>(allocate(p, cap)); }
        p[n++] = static_cast<char>(c);
    }
    if (c == EOF && n == 0) { std::free(p); return nullptr; }
    if (n && p[n - 1] == '\r') --n;
    p[n] = 0;
    return p;
}
static int compare_words(const void* left, const void* right) {
    return std::strcmp(*static_cast<char* const*>(left), *static_cast<char* const*>(right));
}
struct StopList {
    char** words = nullptr;
    size_t count = 0;
    ~StopList() { for (size_t i = 0; i < count; ++i) std::free(words[i]); std::free(words); }
    bool load(const char* path) {
        FILE* f = std::fopen(path, "rb");
        if (!f) { std::perror(path); return false; }
        char* word;
        while ((word = read_line(f))) {
            if (!*word) { std::free(word); continue; }
            words = static_cast<char**>(allocate(words, (count + 1) * sizeof(char*)));
            words[count++] = word;
        }
        const bool ok = !std::ferror(f);
        const bool closed = std::fclose(f) == 0;
        if (count > 1) std::qsort(words, count, sizeof(char*), compare_words);
        return ok && closed;
    }
    bool contains(const char* word) const {
        size_t low = 0, high = count;
        while (low < high) {
            const size_t middle = low + (high - low) / 2;
            const int cmp = std::strcmp(word, words[middle]);
            if (!cmp) return true;
            if (cmp < 0) high = middle; else low = middle + 1;
        }
        return false;
    }
};
static bool process(const char* input, const char* output, const StopList& stop, size_t& tokens) {
    if (!std::strcmp(input, output)) { std::fputs("Input and output must differ\n", stderr); return false; }
    FILE* in = std::fopen(input, "rb");
    if (!in) { std::perror(input); return false; }
    FILE* out = std::fopen(output, "wb");
    if (!out) { std::perror(output); std::fclose(in); return false; }
    char* word;
    while ((word = read_line(in))) {
        if (*word && !stop.contains(word)) {
            extract_stem(word);
            if (*word) { std::fprintf(out, "%s\n", word); ++tokens; }
        }
        std::free(word);
    }
    bool ok = !std::ferror(in) && !std::ferror(out);
    if (std::fclose(in)) ok = false;
    if (std::fclose(out)) ok = false;
    if (!ok) std::fprintf(stderr, "I/O error: %s -> %s\n", input, output);
    return ok;
}
static bool numeric_id(const char* text) {
    if (!*text) return false;
    for (; *text; ++text) if (*text < '0' || *text > '9') return false;
    return true;
}
static int batch(const char* manifest, const char* directory, const StopList& stop) {
#ifdef _WIN32
    const int created = _mkdir(directory);
#else
    const int created = mkdir(directory, 0755);
#endif
    if (created && errno != EEXIST) { std::perror(directory); return 1; }
    FILE* list = std::fopen(manifest, "rb");
    if (!list) { std::perror(manifest); return 1; }
    size_t documents = 0, tokens = 0, line_number = 0;
    char* line;
    bool ok = true;
    while ((line = read_line(list))) {
        ++line_number;
        if (!*line) { std::free(line); continue; }
        char* separator = std::strchr(line, '\t');
        if (separator) *separator++ = 0;
        // Дополнительные колонки title/url в поисковом manifest здесь не нужны.
        if (separator) { char* metadata = std::strchr(separator, '\t'); if (metadata) *metadata = 0; }
        if (!separator || !*separator || !numeric_id(line)) {
            std::fprintf(stderr, "Invalid manifest line %zu: expected numeric id TAB path\n", line_number);
            std::free(line); ok = false; break;
        }
        const size_t capacity = std::strlen(directory) + std::strlen(line) + 10;
        char* output = static_cast<char*>(allocate(nullptr, capacity));
        std::snprintf(output, capacity, "%s/%s.stems", directory, line);
        ok = process(separator, output, stop, tokens);
        std::free(output); std::free(line);
        if (!ok) break;
        ++documents;
    }
    if (std::ferror(list)) ok = false;
    if (std::fclose(list)) ok = false;
    std::printf("Documents: %zu; stems: %zu\n", documents, tokens);
    return ok ? 0 : 1;
}
int main(int argc, char** argv) {
    const bool is_batch = argc > 1 && !std::strcmp(argv[1], "--batch");
    if ((!is_batch && argc != 3 && argc != 4) || (is_batch && argc != 4 && argc != 5)) {
        std::fputs("Usage: stemming INPUT OUTPUT [STOPLIST]\n       stemming --batch MANIFEST OUTPUT_DIRECTORY [STOPLIST]\n", stderr);
        return 1;
    }
    StopList stop;
    if (argc == (is_batch ? 5 : 4) && !stop.load(argv[argc - 1])) return 1;
    if (is_batch) return batch(argv[2], argv[3], stop);
    size_t tokens = 0;
    return process(argv[1], argv[2], stop, tokens) ? 0 : 1;
}
