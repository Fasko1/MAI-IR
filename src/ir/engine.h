// Пояснение к файлу: Общий интерфейс бинарного индекса и поиска.
// Как читать: Document/Term/Posting описывают данные; Index читает индекс; build/compress/search выполняют операции; C API связывает нормализатор.
// Команды запуска и структура проекта: README.md.

#pragma once
#include "base.h"
namespace ir {
// Один элемент обратного списка: внутренний doc-ID и число вхождений термина в документ.
struct Posting {
    uint32_t doc, tf;
};
// Прямой индекс: внешний ID, заголовок, URL, длина в терминах и L2-норма для TF-IDF.
struct Document {
    char *id;
    char *title;
    char *url;
    uint64_t length;
    double norm;
};
// Словарь: термин, число документов DF и расположение его обратного списка в секции постингов.
struct Term {
    char *text;
    uint32_t df;
    uint64_t offset, bytes;
};
struct BuildOptions {
    const char *manifest;
    const char *index;
    bool compressed = false;
    size_t memory_mb = 64;
    bool inline_terms = false;
};
struct SearchOptions {
    bool ranked = false;
    size_t limit = 50;
    size_t offset = 0;
    bool profile = false;
};
// Index владеет метаданными и файлом; копирование запрещено, чтобы не освободить одни и те же ресурсы дважды.
struct Index {
    File file;
    Vec<Document> docs;
    Vec<Term> terms;
    StringMap vocabulary;
    bool compressed = false;
    uint64_t postings = 0, tokens = 0, posting_offset = 0, bytes = 0, document_bytes = 0,
             dictionary_bytes = 0;
    Index() = default;
    Index(const Index &) = delete;
    Index &operator=(const Index &) = delete;
    ~Index();
    void load(const char *path);
    void read_postings(uint32_t term, Vec<Posting> &out);
    void verify();
    void stats(FILE *out) const;
    void benchmark(FILE *out);
};
void build(const BuildOptions &options, FILE *stats);
void compress(const char *input, const char *output, FILE *stats);
void search(Index &index, const char *query, const SearchOptions &options, FILE *output);
// VB uses 7-bit little-endian groups; bit 7 marks the final byte.
void vb_encode(uint32_t value, Vec<unsigned char> &out);
uint32_t vb_decode(const unsigned char *bytes, size_t count, size_t &position);
} // namespace ir
// This translation-unit boundary isolates the permitted STL tokenizer adapter.
extern "C" {
typedef void (*ir_token_callback)(const char *token, void *user);
bool ir_normalizer_init(const char *abbreviations, const char *stopwords, char *error,
                        size_t error_size);
bool ir_normalize(const char *text, ir_token_callback callback, void *user, char *error,
                  size_t error_size);
bool ir_tokenize(const char *text, ir_token_callback callback, void *user, char *error,
                 size_t error_size);
}
