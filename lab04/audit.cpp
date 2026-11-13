// Пояснение к файлу: Проверка согласованности токенов и основ в отдельных файлах.
// Как читать: Парное чтение → повторный вызов стеммера → сравнение/счётчики. Для большого корпуса используется audit_stream.cpp.
// Команды запуска и структура проекта: README.md.

// Потоковая проверка выравнивания token/stem файлов и корпусная статистика, без STL.
// audit TOKENS_MANIFEST STEMS_MANIFEST OUTPUT_JSON
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "funcs/stemmer.h"
namespace {
char* line(FILE* f) {
    size_t size = 0, capacity = 128;
    char* p = static_cast<char*>(std::malloc(capacity));
    if (!p) std::exit(2);
    int c;
    while ((c = std::fgetc(f)) != EOF && c != '\n') {
        if (size + 1 >= capacity) {
            capacity *= 2;
            char* grown = static_cast<char*>(std::realloc(p, capacity));
            if (!grown) { std::free(p); std::exit(2); }
            p = grown;
        }
        p[size++] = static_cast<char>(c);
    }
    if (c == EOF && !size) { std::free(p); return nullptr; }
    if (size && p[size - 1] == '\r') --size;
    p[size] = 0;
    return p;
}
struct Record {
    char* text = nullptr;
    char* path = nullptr;
    ~Record() { std::free(text); }
    bool read(FILE* file) {
        std::free(text); text = line(file); path = nullptr;
        if (!text) return false;
        path = std::strchr(text, '\t');
        if (!path || path == text) return true;
        *path++ = 0;
        char* metadata = std::strchr(path, '\t');
        if (metadata) *metadata = 0;
        return true;
    }
    bool valid() const {
        if (!text || !*text || !path || !*path) return false;
        for (const char* p = text; *p; ++p) if (*p < '0' || *p > '9') return false;
        return true;
    }
};
struct Counts {
    unsigned long long documents = 0, tokens = 0, changed = 0, token_chars = 0, stem_chars = 0;
};
bool document(const Record& token, const Record& stem, Counts& counts) {
    FILE* tokens = std::fopen(token.path, "rb");
    FILE* stems = std::fopen(stem.path, "rb");
    if (!tokens || !stems) {
        std::fprintf(stderr, "Cannot read document %s: %s / %s\n", token.text, token.path, stem.path);
        if (tokens) std::fclose(tokens);
        if (stems) std::fclose(stems);
        return false;
    }
    bool ok = true;
    unsigned long long position = 0;
    for (;;) {
        char* word = line(tokens);
        char* base = line(stems);
        if (!word && !base) break;
        ++position;
        if (!word || !base || !*word || !*base) {
            std::fprintf(stderr, "Token/stem counts differ or empty term: doc=%s line=%llu\n", token.text, position);
            ok = false;
        } else {
            ++counts.tokens;
            if (std::strcmp(word, base)) ++counts.changed;
            counts.token_chars += count_unicode_characters(word);
            counts.stem_chars += count_unicode_characters(base);
        }
        std::free(word); std::free(base);
        if (!ok) break;
    }
    if (std::ferror(tokens) || std::ferror(stems)) ok = false;
    if (std::fclose(tokens)) ok = false;
    if (std::fclose(stems)) ok = false;
    if (ok) ++counts.documents;
    return ok;
}
}
int main(int argc, char** argv) {
    if (argc != 4) { std::fputs("Usage: audit TOKENS_MANIFEST STEMS_MANIFEST OUTPUT_JSON\n", stderr); return 1; }
    FILE* tokens = std::fopen(argv[1], "rb");
    FILE* stems = std::fopen(argv[2], "rb");
    if (!tokens || !stems) {
        std::fputs("Cannot read manifests\n", stderr);
        if (tokens) std::fclose(tokens);
        if (stems) std::fclose(stems);
        return 1;
    }
    Counts counts;
    bool ok = true;
    Record token, stem;
    for (;;) {
        const bool has_token = token.read(tokens);
        const bool has_stem = stem.read(stems);
        if (!has_token && !has_stem) break;
        if (!has_token || !has_stem || !token.valid() || !stem.valid() || std::strcmp(token.text, stem.text)) {
            std::fputs("Manifest ids/order differ or record is invalid\n", stderr); ok = false; break;
        }
        if (!document(token, stem, counts)) { ok = false; break; }
    }
    if (std::ferror(tokens) || std::ferror(stems)) ok = false;
    if (std::fclose(tokens)) ok = false;
    if (std::fclose(stems)) ok = false;
    if (!ok) return 1;
    FILE* out = std::fopen(argv[3], "wb");
    if (!out) { std::perror(argv[3]); return 1; }
    const double n = static_cast<double>(counts.tokens);
    std::fprintf(out,
        "{\n  \"documents\": %llu,\n  \"tokens\": %llu,\n  \"stems\": %llu,\n"
        "  \"changed_occurrences\": %llu,\n  \"unchanged_occurrences\": %llu,\n"
        "  \"changed_fraction\": %.12g,\n  \"token_characters\": %llu,\n  \"stem_characters\": %llu,\n"
        "  \"mean_token_length\": %.12g,\n  \"mean_stem_length\": %.12g,\n  \"aligned\": true\n}\n",
        counts.documents, counts.tokens, counts.tokens, counts.changed, counts.tokens - counts.changed,
        n ? counts.changed / n : 0.0, counts.token_chars, counts.stem_chars,
        n ? counts.token_chars / n : 0.0, n ? counts.stem_chars / n : 0.0);
    ok = !std::ferror(out);
    if (std::fclose(out)) ok = false;
    std::printf("Documents: %llu; tokens/stems: %llu; changed: %llu; unchanged: %llu\n",
                counts.documents, counts.tokens, counts.changed, counts.tokens - counts.changed);
    return ok ? 0 : 1;
}
