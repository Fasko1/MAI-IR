// Пояснение к файлу: C++ подсчёт частот терминов и сортировка для закона Ципфа.
// Как читать: Чтение файлов или inline TSV → собственная хеш-таблица CF → merge_sort → freq.txt/stats.json. CF считает вхождения, а не документы.
// Команды запуска и структура проекта: README.md.

// ЛР5: частоты, ранжирование и статистика на C++ без STL.
// Собственная хеш-таблица с цепочками и собственная сортировка слиянием.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#ifdef _WIN32
#include <windows.h>
#endif

static void fail(const char* message) { std::fprintf(stderr, "Ошибка: %s\n", message); std::exit(1); }
static void* allocate(size_t bytes) { void* p = std::malloc(bytes); if (!p) fail("Недостаточно памяти"); return p; }
#ifdef _WIN32
static wchar_t* wide(const char* s) {
    int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s, -1, nullptr, 0);
    if (!n) fail("Неверный UTF-8 пути");
    wchar_t* p = static_cast<wchar_t*>(allocate(static_cast<size_t>(n) * sizeof(wchar_t)));
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s, -1, p, n)) fail("Преобразование пути");
    return p;
}
#endif
static FILE* open_file(const char* path, const char* mode) {
#ifdef _WIN32
    wchar_t* p = wide(path); wchar_t* m = wide(mode);
    FILE* f = _wfopen(p, m); std::free(p); std::free(m);
#else
    FILE* f = std::fopen(path, mode);
#endif
    if (!f) { std::fprintf(stderr, "Не удалось открыть: %s\n", path); std::exit(1); }
    return f;
}
struct Buffer {
    char* text = nullptr; size_t capacity = 0;
    ~Buffer() { std::free(text); }
    // Строка любой длины, включая последнюю без LF. Текст не загружается целиком.
    bool line(FILE* f) {
        size_t n = 0; int c;
        while ((c = std::fgetc(f)) != EOF && c != '\n' && c != '\r') {
            if (!c) fail("NUL во входном файле");
            if (n + 2 > capacity) {
                size_t next = capacity ? capacity * 2 : 256;
                if (next <= capacity) fail("Слишком длинная строка");
                void* p = std::realloc(text, next); if (!p) fail("Недостаточно памяти");
                text = static_cast<char*>(p); capacity = next;
            }
            text[n++] = static_cast<char>(c);
        }
        if (c == '\r') {
            int following = std::fgetc(f);
            if (following != '\n' && following != EOF) std::ungetc(following, f);
        }
        if (std::ferror(f)) fail("Ошибка чтения");
        if (c == EOF && n == 0) return false;
        if (!capacity) { capacity = 1; text = static_cast<char*>(allocate(1)); }
        text[n] = 0; return true;
    }
};
// Декодирование для проверки UTF-8 и удаления тех же пробелов, что str.strip Python.
static uint32_t next_codepoint(const unsigned char*& p) {
    unsigned char c = *p++; if (c < 128) return c;
    int count; uint32_t value, minimum;
    if (c >= 0xC2 && c <= 0xDF) { count = 1; value = c & 31; minimum = 0x80; }
    else if (c >= 0xE0 && c <= 0xEF) { count = 2; value = c & 15; minimum = 0x800; }
    else if (c >= 0xF0 && c <= 0xF4) { count = 3; value = c & 7; minimum = 0x10000; }
    else { fail("Неверный UTF-8"); return 0; }
    for (int i = 0; i < count; ++i) {
        if ((*p & 0xC0) != 0x80) fail("Неверный UTF-8");
        value = (value << 6) | (*p++ & 63);
    }
    if (value < minimum || value > 0x10FFFF || (value >= 0xD800 && value <= 0xDFFF)) fail("Неверный UTF-8");
    return value;
}
static bool whitespace(uint32_t c) {
    return (c >= 9 && c <= 13) || (c >= 0x1C && c <= 0x20) || c == 0x85 || c == 0xA0 ||
           c == 0x1680 || (c >= 0x2000 && c <= 0x200A) || c == 0x2028 || c == 0x2029 ||
           c == 0x202F || c == 0x205F || c == 0x3000;
}
static char* trim_term(char* text) {
    const unsigned char* p = reinterpret_cast<const unsigned char*>(text);
    const unsigned char* first = nullptr; const unsigned char* end = p;
    while (*p) {
        const unsigned char* start = p;
        if (!whitespace(next_codepoint(p))) { if (!first) first = start; end = p; }
    }
    if (!first) { text[0] = 0; return text; }
    *const_cast<unsigned char*>(end) = 0;
    return reinterpret_cast<char*>(const_cast<unsigned char*>(first));
}
struct Entry { char* term; uint64_t count; Entry* next; };
static uint64_t hash_term(const char* word) {
    uint64_t h = UINT64_C(14695981039346656037);
    for (const unsigned char* p = reinterpret_cast<const unsigned char*>(word); *p; ++p) {
        h ^= *p; h *= UINT64_C(1099511628211);
    }
    return h;
}
// Собственная хеш-таблица накапливает CF каждого уникального термина; тексты документов после чтения не сохраняются.
class Frequencies {
    Entry** buckets_; size_t capacity_ = 1024;
    void grow() {
        if (capacity_ > SIZE_MAX / 2 / sizeof(Entry*)) fail("Слишком большая таблица");
        size_t n = capacity_ * 2;
        Entry** table = static_cast<Entry**>(std::calloc(n, sizeof(Entry*)));
        if (!table) fail("Недостаточно памяти");
        for (size_t i = 0; i < capacity_; ++i) {
            Entry* e = buckets_[i];
            while (e) { Entry* next = e->next; size_t slot = hash_term(e->term) % n;
                e->next = table[slot]; table[slot] = e; e = next; }
        }
        std::free(buckets_); buckets_ = table; capacity_ = n;
    }
public:
    size_t unique = 0; uint64_t total = 0;
    Frequencies() {
        buckets_ = static_cast<Entry**>(std::calloc(capacity_, sizeof(Entry*)));
        if (!buckets_) fail("Недостаточно памяти");
    }
    Frequencies(const Frequencies&) = delete;
    Frequencies& operator=(const Frequencies&) = delete;
    ~Frequencies() {
        for (size_t i = 0; i < capacity_; ++i) { Entry* e = buckets_[i];
            while (e) { Entry* next = e->next; std::free(e->term); std::free(e); e = next; } }
        std::free(buckets_);
    }
    // Каждая непустая строка — ещё одно вхождение, повторы учитываются.
    void add(const char* term) {
        if (total == UINT64_MAX) fail("Переполнение счётчика");
        ++total; size_t slot = hash_term(term) % capacity_;
        for (Entry* e = buckets_[slot]; e; e = e->next) {
            if (std::strcmp(e->term, term) == 0) { ++e->count; return; }
        }
        if (unique >= capacity_ - capacity_ / 4) { grow(); slot = hash_term(term) % capacity_; }
        Entry* e = static_cast<Entry*>(allocate(sizeof(Entry)));
        e->term = static_cast<char*>(allocate(std::strlen(term) + 1)); std::strcpy(e->term, term);
        e->count = 1; e->next = buckets_[slot]; buckets_[slot] = e; ++unique;
    }
    Entry** entries() const {
        if (unique > SIZE_MAX / sizeof(Entry*)) fail("Слишком много терминов");
        Entry** array = static_cast<Entry**>(allocate(unique * sizeof(Entry*))); size_t n = 0;
        for (size_t i = 0; i < capacity_; ++i) for (Entry* e = buckets_[i]; e; e = e->next) array[n++] = e;
        return array;
    }
};
// Частота по убыванию; при равной частоте — термин по возрастанию.
// Для корректного UTF-8 побайтовый порядок совпадает с порядком кодовых точек.
static bool before(const Entry* a, const Entry* b) {
    return a->count != b->count ? a->count > b->count : std::strcmp(a->term, b->term) < 0;
}
// Сортировка указателей по убыванию частоты; лексикографическое сравнение разрешает равные частоты воспроизводимо.
static void merge_sort(Entry** a, Entry** tmp, size_t lo, size_t hi) {
    if (hi - lo < 2) return;
    size_t mid = lo + (hi - lo) / 2;
    merge_sort(a, tmp, lo, mid); merge_sort(a, tmp, mid, hi);
    size_t i = lo, j = mid, k = lo;
    while (i < mid && j < hi) tmp[k++] = before(a[j], a[i]) ? a[j++] : a[i++];
    while (i < mid) tmp[k++] = a[i++];
    while (j < hi) tmp[k++] = a[j++];
    for (k = lo; k < hi; ++k) a[k] = tmp[k];
}
static void json_string(FILE* f, const char* s) {
    std::fputc('"', f);
    for (const unsigned char* p = reinterpret_cast<const unsigned char*>(s); *p; ++p) {
        if (*p == '"' || *p == '\\') { std::fputc('\\', f); std::fputc(*p, f); }
        else if (*p < 32) std::fprintf(f, "\\u%04x", *p);
        else std::fputc(*p, f);
    }
    std::fputc('"', f);
}
static void close_output(FILE* f) { bool bad = std::ferror(f); if (std::fclose(f)) bad = true; if (bad) fail("Ошибка записи результата"); }
// Точка общего пути CLI: выбирает формат входа, считает частоты и публикует упорядоченный словарь со статистикой.
static int count_corpus(int argc, char** argv) {
    const bool inline_terms = argc == 5 && !std::strcmp(argv[1], "--inline");
    if (inline_terms) { --argc; ++argv; }
    if (argc != 4) { std::fprintf(stderr, "Usage: zipf_count INPUT_LIST FREQ_OUTPUT STATS_OUTPUT\n"); return 2; }
    Frequencies counter; uint64_t documents = 0;
    FILE* list = open_file(argv[1], "rb"); Buffer path, term;
    while (path.line(list)) {
        size_t n = std::strlen(path.text); if (n && path.text[n-1] == '\r') path.text[n-1] = 0;
        if (!*path.text) continue;
        if (inline_terms) {
            char* terms = std::strchr(path.text, '\t');
            if (!terms) fail("Нет поля терминов TSV");
            ++terms; char* end = std::strchr(terms, '\t');
            if (!end) fail("Нет метаданных TSV");
            *end = 0; ++documents;
            while (*terms) {
                char* start = terms;
                while (*terms && *terms != ' ') ++terms;
                if (*terms) *terms++ = 0;
                if (*start) counter.add(trim_term(start));
            }
            continue;
        }
        FILE* input = open_file(path.text, "rb"); ++documents;
        while (term.line(input)) { char* word = trim_term(term.text); if (*word) counter.add(word); }
        if (std::fclose(input)) fail("Ошибка закрытия входа");
    }
    if (std::fclose(list)) fail("Ошибка закрытия списка");
    if (!counter.unique) fail("Нет входных терминов");
    Entry** ranked = counter.entries();
    Entry** tmp = static_cast<Entry**>(allocate(counter.unique * sizeof(Entry*)));
    merge_sort(ranked, tmp, 0, counter.unique); std::free(tmp);
    FILE* freq = open_file(argv[2], "wb");
    for (size_t i = 0; i < counter.unique; ++i) std::fprintf(freq, "%llu %s\n", static_cast<unsigned long long>(ranked[i]->count), ranked[i]->term);
    close_output(freq);
    FILE* stats = open_file(argv[3], "wb");
    std::fprintf(stats, "{\n  \"documents\": %llu,\n  \"total_terms\": %llu,\n  \"unique_terms\": %zu,\n  \"top20\": [",
                 static_cast<unsigned long long>(documents), static_cast<unsigned long long>(counter.total), counter.unique);
    size_t top = counter.unique < 20 ? counter.unique : 20;
    for (size_t i = 0; i < top; ++i) {
        std::fputs(i ? ",\n    [" : "\n    [", stats); json_string(stats, ranked[i]->term);
        std::fprintf(stats, ", %llu]", static_cast<unsigned long long>(ranked[i]->count));
    }
    std::fprintf(stats, "\n  ],\n  \"plotted_ranks\": %zu\n}\n", counter.unique < 10000 ? counter.unique : size_t(10000));
    close_output(stats); std::free(ranked);
    std::printf("Документов: %llu; вхождений: %llu; уникальных: %zu\n", static_cast<unsigned long long>(documents), static_cast<unsigned long long>(counter.total), counter.unique);
    return 0;
}
#ifdef _WIN32
// Wide argv + UTF-8 file list support paths with Cyrillic on Windows.
int wmain(int argc, wchar_t** input) {
    SetConsoleOutputCP(CP_UTF8);
    char** argv = static_cast<char**>(allocate(static_cast<size_t>(argc) * sizeof(char*)));
    for (int i = 0; i < argc; ++i) {
        int n = WideCharToMultiByte(CP_UTF8, 0, input[i], -1, nullptr, 0, nullptr, nullptr);
        if (!n) fail("Преобразование аргументов");
        argv[i] = static_cast<char*>(allocate(static_cast<size_t>(n)));
        if (!WideCharToMultiByte(CP_UTF8, 0, input[i], -1, argv[i], n, nullptr, nullptr)) fail("Преобразование аргументов");
    }
    int rc = count_corpus(argc, argv);
    for (int i = 0; i < argc; ++i) std::free(argv[i]);
    std::free(argv);
    return rc;
}
#else
int main(int argc, char** argv) { return count_corpus(argc, argv); }
#endif
