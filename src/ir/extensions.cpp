// Дополнительные работы. Координаты поверхностных токенов задают строгую цитату;
// леммы используются для обычного поиска. TSV корпуса читается потоково.
#include "extensions.h"
#include "skips.h"
#include "arguments.h"
#include <ctime>
namespace ir {
bool use_skips = true;
uint64_t query_comparisons = 0, query_jumps = 0;
namespace {
StringMap forms;
Vec<char *> words, lemmas;
bool lemma_mode = false;
struct DictionaryCleanup {
    ~DictionaryCleanup() {
        for (size_t i = 0; i < words.size; ++i) {
            std::free(words[i]);
            std::free(lemmas[i]);
        }
    }
} cleanup;
// Формат дополнений: целые записываем явно по 8 байт little-endian, независимо от платформы.
void put64(FILE *f, uint64_t n) {
    unsigned char b[8];
    for (unsigned i = 0; i < 8; ++i)
        b[i] = static_cast<unsigned char>(n >> (8 * i));
    if (std::fwrite(b, 1, 8, f) != 8)
        fail("Extension write failed");
}
uint64_t get64(FILE *f) {
    unsigned char b[8];
    if (std::fread(b, 1, 8, f) != 8)
        fail("Truncated extension file");
    uint64_t n = 0;
    for (unsigned i = 0; i < 8; ++i)
        n |= uint64_t(b[i]) << (8 * i);
    return n;
}
void put_text(FILE *f, const char *s) {
    size_t n = std::strlen(s);
    put64(f, n);
    if (std::fwrite(s, 1, n, f) != n)
        fail("Extension write failed");
}
char *get_text(FILE *f) {
    uint64_t n = get64(f);
    if (n > 65536)
        fail("Invalid extension string");
    char *p = static_cast<char *>(std::malloc(size_t(n) + 1));
    if (!p)
        fail("Out of memory");
    if (std::fread(p, 1, size_t(n), f) != n) {
        std::free(p);
        fail("Truncated extension string");
    }
    p[n] = 0;
    if (!valid_utf8(p) || std::strlen(p) != n) {
        std::free(p);
        fail("Invalid extension UTF8");
    }
    return p;
}
// Привязываем координаты и смещения к порядку документов: чужой снимок подключать нельзя.
uint64_t binding(const Index &index) {
    uint64_t h = 14695981039346656037ull;
    for (size_t i = 0; i < index.docs.size; ++i) {
        h = hash_bytes(index.docs[i].id, std::strlen(index.docs[i].id) + 1, h);
    }
    return h;
}
void fields(char *line, char **f) {
    f[0] = line;
    unsigned n = 1;
    for (char *p = line; *p; ++p)
        if (*p == '\t') {
            if (n == 4)
                fail("Too many TSV fields");
            *p = 0;
            f[n++] = p + 1;
        }
    if (n != 4 || !*f[0])
        fail("Expected four TSV fields");
}
char *term_next(char *&p) {
    while (*p == ' ')
        ++p;
    if (!*p)
        return nullptr;
    char *start = p;
    while (*p && *p != ' ')
        ++p;
    if (*p)
        *p++ = 0;
    return start;
}
const char *option(int argc, char **argv, const char *key) {
    for (int i = 2; i + 1 < argc; ++i)
        if (!std::strcmp(argv[i], key))
            return argv[i + 1];
    fail("Required option: %s", key);
}
struct CoordTerm {
    char *text;
    uint64_t offset = 0, count = 0;
};
// В памяти только словарь и границы списков; сами координаты остаются в файле.
struct Coordinates {
    File file;
    Vec<CoordTerm> terms;
    StringMap map;
    uint64_t data = 0;
    ~Coordinates() {
        for (size_t i = 0; i < terms.size; ++i)
            std::free(terms[i].text);
    }
} coordinates;
File texts_file;
Vec<uint64_t> text_offsets;
struct PhraseTokens {
    Vec<char *> words;
    ~PhraseTokens() {
        for (size_t i = 0; i < words.size; ++i)
            std::free(words[i]);
    }
    static void emit(const char *token, void *user) {
        static_cast<PhraseTokens *>(user)->words.push(copy_string(token));
    }
};
// Внешние серии содержат один Record на вхождение, не весь корпус в памяти.
// Запись внешней сортировки: номер термина, внутренний docID, позиция слова от нуля.
struct Record {
    uint32_t term, doc, pos;
};
bool less_record(const Record &a, const Record &b) {
    return a.term < b.term ||
           (a.term == b.term && (a.doc < b.doc || (a.doc == b.doc && a.pos < b.pos)));
}
struct Run {
    char *name;
    FILE *file = nullptr;
    Record head{};
    bool valid = false;
};
struct Runs {
    Vec<Run> items;
    ~Runs() {
        for (size_t i = 0; i < items.size; ++i) {
            if (items[i].file)
                std::fclose(items[i].file);
            remove_file(items[i].name);
            std::free(items[i].name);
        }
    }
    void spill(Vec<Record> &records, const char *output) {
        if (!records.size)
            return;
        sort(records.data, records.size, less_record);
        char suffix[80];
        std::snprintf(suffix, sizeof(suffix), ".run.%zu", items.size);
        size_t n = std::strlen(output) + std::strlen(suffix) + 1;
        char *path = static_cast<char *>(std::malloc(n));
        if (!path)
            fail("Out of memory");
        std::snprintf(path, n, "%s%s", output, suffix);
        items.push({path, nullptr, {}, false});
        File f;
        f.open(path, "wb");
        if (std::fwrite(records.data, sizeof(Record), records.size, f.fp) != records.size)
            fail("Run write failed");
        f.close();
        records.clear();
    }
    void advance(size_t i) {
        Run &r = items[i];
        size_t n = std::fread(&r.head, 1, sizeof(Record), r.file);
        if (std::ferror(r.file) || (n && n != sizeof(Record)))
            fail("Truncated run");
        r.valid = n != 0;
    }
};
// Куча хранит номера серий. Минимальная текущая запись выбирается без загрузки всех серий.
struct Heap {
    Vec<uint32_t> ids;
    Runs &runs;
    explicit Heap(Runs &r) : runs(r) {}
    bool less(uint32_t a, uint32_t b) {
        return less_record(runs.items[a].head, runs.items[b].head);
    }
    void push(uint32_t v) {
        ids.push(v);
        size_t i = ids.size - 1;
        while (i) {
            size_t p = (i - 1) / 2;
            if (!less(ids[i], ids[p]))
                break;
            uint32_t t = ids[i];
            ids[i] = ids[p];
            ids[p] = t;
            i = p;
        }
    }
    uint32_t pop() {
        uint32_t out = ids[0];
        ids[0] = ids[--ids.size];
        size_t i = 0;
        while (i * 2 + 1 < ids.size) {
            size_t c = i * 2 + 1;
            if (c + 1 < ids.size && less(ids[c + 1], ids[c]))
                ++c;
            if (!less(ids[c], ids[i]))
                break;
            uint32_t t = ids[i];
            ids[i] = ids[c];
            ids[c] = t;
            i = c;
        }
        return out;
    }
};
// ЛР10. Читаем токены по документам, сортируем ограниченные блоки и сливаем серии.
// На выходе у каждого исходного слова свой отсортированный список (docID, position).
void build_coordinates(const char *input, const char *base, const char *output) {
    Index index;
    index.load(base);
    File source;
    source.open(input, "rb");
    Vec<char> line;
    Vec<CoordTerm> terms;
    StringMap vocabulary;
    Runs runs;
    Vec<Record> records;
    uint64_t docs = 0, total = 0;
    while (read_line(source.fp, line, 64 * 1048576)) {
        char *f[4];
        fields(line.data, f);
        if (docs >= index.docs.size || std::strcmp(f[0], index.docs[size_t(docs)].id))
            fail("Coordinate corpus/index IDs differ");
        char *p = f[1];
        uint32_t pos = 0;
        while (char *word = term_next(p)) {
            if (!valid_utf8(word))
                fail("Invalid coordinate token");
            uint32_t id;
            if (!vocabulary.find(word, id)) {
                if (terms.size >= UINT32_MAX)
                    fail("Too many surface terms");
                id = uint32_t(terms.size);
                terms.push({copy_string(word), 0, 0});
                vocabulary.insert(terms[id].text, id);
            }
            if (pos == UINT32_MAX)
                fail("Too many positions");
            records.push({id, uint32_t(docs), pos++});
            ++total;
            if (records.size >= 64 * 1048576 / sizeof(Record))
                runs.spill(records, output);
        }
        ++docs;
        if (docs % 25000 == 0)
            std::fprintf(stderr, "Coordinates: %llu documents\n", (unsigned long long)docs);
    }
    if (docs != index.docs.size || total != index.tokens)
        fail("Coordinate count mismatch");
    runs.spill(records, output);
    // Смещения таблицы известны до слияния: её размер зависит только от словаря.
    uint64_t start = 40;
    for (size_t i = 0; i < terms.size; ++i)
        start += 24 + std::strlen(terms[i].text);
    File out;
    out.open(output, "w+b");
    seek(out.fp, start);
    Heap heap(runs);
    for (size_t i = 0; i < runs.items.size; ++i) {
        runs.items[i].file = open_file(runs.items[i].name, "rb");
        if (!runs.items[i].file)
            fail("Cannot open coordinate run");
        runs.advance(i);
        if (runs.items[i].valid)
            heap.push(uint32_t(i));
    }
    uint64_t written = 0;
    uint32_t last = UINT32_MAX;
    while (heap.ids.size) {
        uint32_t i = heap.pop();
        const Record r = runs.items[i].head;
        if (r.term != last) {
            terms[r.term].offset = written;
            last = r.term;
        }
        put64(out.fp, (uint64_t(r.doc) << 32) | r.pos);
        ++terms[r.term].count;
        ++written;
        runs.advance(i);
        if (runs.items[i].valid)
            heap.push(i);
    }
    if (written != total)
        fail("Coordinate merge mismatch");
    seek(out.fp, 0);
    std::fwrite("MAICOOR2", 1, 8, out.fp);
    put64(out.fp, binding(index));
    put64(out.fp, docs);
    put64(out.fp, terms.size);
    put64(out.fp, total);
    for (size_t i = 0; i < terms.size; ++i) {
        put_text(out.fp, terms[i].text);
        put64(out.fp, terms[i].offset);
        put64(out.fp, terms[i].count);
        std::free(terms[i].text);
    }
    out.close();
    std::printf("{\"documents\":%llu,\"occurrences\":%llu,\"surface_terms\":%zu,\"runs\":%zu}\n",
                (unsigned long long)docs, (unsigned long long)total, terms.size, runs.items.size);
}
// ЛР14. Сохраняем начало каждой строки texts.tsv, чтобы читать статью сразу по её docID.
void build_offsets(const char *input, const char *base, const char *output) {
    Index index;
    index.load(base);
    File in, out;
    in.open(input, "rb");
    out.open(output, "wb");
    std::fwrite("MAITEXT2", 1, 8, out.fp);
    put64(out.fp, binding(index));
    put64(out.fp, index.docs.size);
    put64(out.fp, file_size(in.fp));
    Vec<char> line;
    size_t doc = 0;
    while (true) {
        uint64_t offset = tell(in.fp);
        if (!read_line(in.fp, line, 64 * 1048576))
            break;
        char *f[4];
        fields(line.data, f);
        if (doc >= index.docs.size || std::strcmp(f[0], index.docs[doc].id))
            fail("Text/index IDs differ");
        put64(out.fp, offset);
        ++doc;
    }
    if (doc != index.docs.size)
        fail("Text count mismatch");
    out.close();
    std::printf("{\"documents\":%zu}\n", doc);
}
// ЛР4. Меняем только поле токенов TSV; ID, заголовок, URL и число вхождений сохраняются.
void lemmatize_stream(const char *input, const char *output, const char *dictionary) {
    load_lemmas(dictionary);
    File in, out;
    in.open(input, "rb");
    out.open(output, "wb");
    Vec<char> line;
    uint64_t docs = 0, total = 0, changed = 0;
    while (read_line(in.fp, line, 64 * 1048576)) {
        char *f[4];
        fields(line.data, f);
        std::fprintf(out.fp, "%s\t", f[0]);
        char *p = f[1];
        bool first = true;
        while (char *word = term_next(p)) {
            const char *result = lemma(word);
            if (!first)
                std::fputc(' ', out.fp);
            first = false;
            std::fputs(result, out.fp);
            ++total;
            changed += std::strcmp(word, result) != 0;
        }
        std::fprintf(out.fp, "\t%s\t%s\n", f[2], f[3]);
        ++docs;
        if (docs % 25000 == 0)
            std::fprintf(stderr, "Lemmatized: %llu\n", (unsigned long long)docs);
    }
    out.close();
    std::printf("{\"documents\":%llu,\"tokens\":%llu,\"changed\":%llu,\"forms\":%zu}\n",
                (unsigned long long)docs, (unsigned long long)total, (unsigned long long)changed,
                words.size);
}
} // namespace
// ЛР4. Одна загрузка таблицы словоформа → лемма. StringMap — наша хеш-таблица из base.h.
void load_lemmas(const char *path) {
    if (lemma_mode)
        fail("Lemma dictionary already initialized");
    File in;
    in.open(path, "rb");
    Vec<char> line;
    while (read_line(in.fp, line, 65536)) {
        char *p = std::strchr(line.data, '\t');
        if (!p)
            fail("Invalid lemma dictionary row");
        *p++ = 0;
        if (!*line.data || !*p || std::strchr(p, '\t') || !valid_utf8(line.data) || !valid_utf8(p))
            fail("Invalid lemma dictionary value");
        uint32_t old;
        if (forms.find(line.data, old))
            fail("Duplicate lemma form");
        if (words.size >= UINT32_MAX)
            fail("Dictionary too large");
        words.push(copy_string(line.data));
        lemmas.push(copy_string(p));
        forms.insert(words[words.size - 1], uint32_t(words.size - 1));
    }
    if (!words.size)
        fail("Empty lemma dictionary");
    lemma_mode = true;
}
bool has_lemmas() { return lemma_mode; }
// Возвращаем словарную форму без анализа контекста. Для неизвестного слова оставляем исходный токен.
const char *lemma(const char *token) {
    // Сохраняем неизвестные, составные и числовые токены. Ё/е сводятся для словаря.
    if (!lemma_mode)
        return token;
    uint32_t id;
    if (forms.find(token, id))
        return lemmas[id];
    if (!std::strstr(token, "ё"))
        return token;
    Vec<char> key;
    for (const unsigned char *p = (const unsigned char *)token; *p; ++p) {
        if (p[0] == 0xd1 && p[1] == 0x91) {
            key.push(char(0xd0));
            key.push(char(0xb5));
            ++p;
        } else
            key.push(char(*p));
    }
    key.push(0);
    return forms.find(key.data, id) ? lemmas[id] : token;
}
// Диспетчер CLI дополнений. Возврат -1 передаёт обычную команду основной программе main.cpp.
int extension_command(int argc, char **argv) {
    if (argc < 2)
        return -1;
    const char *cmd = argv[1];
    if (!std::strcmp(cmd, "make-skips")) {
        size_t stride = 0;
        for (int i = 2; i + 1 < argc; ++i)
            if (!std::strcmp(argv[i], "--step")) {
                char *end = nullptr;
                unsigned long n = std::strtoul(argv[i + 1], &end, 10);
                if (!*argv[i + 1] || *end || n > 1000000)
                    fail("Invalid skip step");
                stride = size_t(n);
            }
        build_skips(option(argc, argv, "--index"), option(argc, argv, "--output"), stride);
        return 0;
    }
    if (!std::strcmp(cmd, "lemmatize")) {
        lemmatize_stream(option(argc, argv, "--input"), option(argc, argv, "--output"),
                         option(argc, argv, "--dictionary"));
        return 0;
    }
    if (!std::strcmp(cmd, "coordinates")) {
        build_coordinates(option(argc, argv, "--input"), option(argc, argv, "--index"),
                          option(argc, argv, "--output"));
        return 0;
    }
    if (!std::strcmp(cmd, "text-offsets")) {
        build_offsets(option(argc, argv, "--input"), option(argc, argv, "--index"),
                      option(argc, argv, "--output"));
        return 0;
    }
    return -1;
}
// Проверяем заголовок, привязку к документам, размеры и непрерывность списков координат.
void open_coordinates(Index &index, const char *path) {
    coordinates.file.open(path, "rb");
    char magic[8];
    if (std::fread(magic, 1, 8, coordinates.file.fp) != 8 || std::memcmp(magic, "MAICOOR2", 8))
        fail("Invalid coordinate header");
    if (get64(coordinates.file.fp) != binding(index) ||
        get64(coordinates.file.fp) != index.docs.size)
        fail("Coordinates belong to another index");
    uint64_t n = get64(coordinates.file.fp), total = get64(coordinates.file.fp);
    if (n > UINT32_MAX || total != index.tokens)
        fail("Coordinate totals mismatch");
    uint64_t next = 0;
    for (uint64_t i = 0; i < n; ++i) {
        CoordTerm t{};
        t.text = get_text(coordinates.file.fp);
        t.offset = get64(coordinates.file.fp);
        t.count = get64(coordinates.file.fp);
        if (!t.count || t.offset != next || t.count > total - next)
            fail("Invalid coordinate descriptor");
        next += t.count;
        coordinates.terms.push(t);
        coordinates.map.insert(t.text, uint32_t(i));
    }
    coordinates.data = tell(coordinates.file.fp);
    if (next != total || file_size(coordinates.file.fp) != coordinates.data + total * 8)
        fail("Invalid coordinate file length");
}
// Проверяем соответствие таблицы смещений корпусу; произвольный доступ нужен для сниппетов.
void open_texts(Index &index, const char *texts, const char *offsets) {
    File in;
    in.open(offsets, "rb");
    char magic[8];
    if (std::fread(magic, 1, 8, in.fp) != 8 || std::memcmp(magic, "MAITEXT2", 8))
        fail("Invalid text offsets");
    if (get64(in.fp) != binding(index) || get64(in.fp) != index.docs.size)
        fail("Text offsets belong to another index");
    texts_file.open(texts, "rb");
    uint64_t corpus_bytes = file_size(texts_file.fp);
    if (get64(in.fp) != corpus_bytes)
        fail("Text corpus size changed");
    uint64_t previous = 0;
    for (size_t i = 0; i < index.docs.size; ++i) {
        uint64_t p = get64(in.fp);
        if ((i && p <= previous) || p >= corpus_bytes)
            fail("Invalid text offset");
        text_offsets.push(p);
        previous = p;
    }
    if (file_size(in.fp) != 32 + index.docs.size * 8)
        fail("Invalid offsets length");
}
// ЛР10. Токенизируем цитату БЕЗ лемматизации. Для k-го слова вычитаем k из позиции.
// Пересечение сдвинутых списков оставляет только соседние слова в одном документе.
void exact_phrase(Index &index, const char *phrase, Vec<uint32_t> &out) {
    if (!coordinates.file.fp)
        fail("Phrase search requires --positions");
    PhraseTokens tokens;
    char error[768] = {};
    if (!ir_tokenize(phrase, PhraseTokens::emit, &tokens, error, sizeof(error)))
        fail("%s", error);
    if (!tokens.words.size || tokens.words.size > 64)
        fail("Phrase must contain 1..64 tokens");
    Vec<uint64_t> matches;
    for (size_t k = 0; k < tokens.words.size; ++k) {
        uint32_t id;
        if (!coordinates.map.find(tokens.words[k], id))
            return;
        const CoordTerm &term = coordinates.terms[id];
        seek(coordinates.file.fp, coordinates.data + term.offset * 8);
        Vec<uint64_t> values;
        values.reserve(size_t(term.count));
        uint64_t previous = 0;
        for (uint64_t j = 0; j < term.count; ++j) {
            uint64_t c = get64(coordinates.file.fp);
            if ((j && c <= previous) || (c >> 32) >= index.docs.size ||
                uint32_t(c) >= index.docs[size_t(c >> 32)].length)
                fail("Invalid coordinate");
            previous = c;
            if (uint32_t(c) >= k)
                values.push(c - k);
        }
        if (k == 0)
            matches.swap(values);
        else {
            Vec<uint64_t> intersection;
            size_t a = 0, b = 0;
            while (a < matches.size && b < values.size) {
                ++query_comparisons;
                if (matches[a] == values[b]) {
                    intersection.push(matches[a]);
                    ++a;
                    ++b;
                } else if (matches[a] < values[b])
                    ++a;
                else
                    ++b;
            }
            matches.swap(intersection);
        }
        if (!matches.size)
            return;
    }
    for (size_t i = 0; i < matches.size; ++i) {
        uint32_t doc = uint32_t(matches[i] >> 32);
        if (!out.size || out[out.size - 1] != doc)
            out.push(doc);
    }
}
// ЛР14. Читаем одну исходную статью, отмечаем совпадения, выбираем окно и выдаём JSON.
// Окно ограничено 36 токенами и 280 символами; HTML-подсветку безопасно добавляет web.py.
void snippet_json(Index &index, uint32_t doc, const Vec<uint32_t> &query, FILE *output) {
    if (!texts_file.fp)
        return;
    seek(texts_file.fp, text_offsets[doc]);
    Vec<char> line;
    if (!read_line(texts_file.fp, line, 64 * 1048576))
        fail("Truncated snippet corpus");
    char *f[4];
    fields(line.data, f);
    if (std::strcmp(f[0], index.docs[doc].id))
        fail("Snippet ID mismatch");
    const char *text = f[1];
    size_t n = std::strlen(text);
    struct Span {
        size_t start, end;
        bool hit;
    };
    Vec<Span> spans;
    // Границы берём из исходного текста: подсветка не восстанавливается из лемм.
    for (size_t i = 0; i < n;) {
        unsigned char c = (unsigned char)text[i];
        bool letter = (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                      ((c == 0xd0 || c == 0xd1) && i + 1 < n);
        if (!letter) {
            ++i;
            continue;
        }
        size_t start = i;
        while (i < n) {
            unsigned char a = (unsigned char)text[i];
            if (a < 128 && ((a >= '0' && a <= '9') || (a >= 'A' && a <= 'Z') ||
                            (a >= 'a' && a <= 'z') || a == '-'))
                ++i;
            else if ((a == 0xd0 || a == 0xd1) && i + 1 < n)
                i += 2;
            else
                break;
        }
        Vec<char> word;
        for (size_t j = start; j < i; ++j)
            word.push(text[j]);
        word.push(0);
        struct Match {
            Index *index;
            const Vec<uint32_t> *query;
            bool hit = false;
            static void emit(const char *word, void *user) {
                auto &m = *static_cast<Match *>(user);
                uint32_t id;
                if (m.index->vocabulary.find(word, id))
                    for (size_t k = 0; k < m.query->size; ++k)
                        if ((*m.query)[k] == id) {
                            m.hit = true;
                            break;
                        }
            }
        };
        Match m{&index, &query, false};
        char error[768] = {};
        if (!ir_normalize(word.data, Match::emit, &m, error, sizeof(error)))
            fail("%s", error);
        spans.push({start, i, m.hit});
    }
    size_t best = 0, best_hits = 0, hits = 0;
    const size_t width = 36;
    for (size_t i = 0; i < spans.size; ++i) {
        hits += spans[i].hit;
        if (i >= width)
            hits -= spans[i - width].hit;
        if (hits > best_hits) {
            best_hits = hits;
            best = i + 1 >= width ? i + 1 - width : 0;
        }
    }
    size_t begin = spans.size ? spans[best].start : 0,
           last = best + width < spans.size ? best + width : spans.size,
           end = last ? spans[last - 1].end : (n < 300 ? n : 300);
    while (end < n && ((unsigned char)text[end] & 0xc0) == 0x80)
        ++end;
    // Если совпадение далеко от начала окна, приближаем контекст к нему.
    for (size_t i = best; i < last; ++i)
        if (spans[i].hit) {
            if (spans[i].start - begin > 160) {
                size_t context = i > best + 3 ? i - 3 : best;
                begin = spans[context].start;
                if (spans[i].start - begin > 160)
                    begin = spans[i].start;
            }
            break;
        }
    size_t cap = begin, characters = 0;
    while (cap < end) {
        if ((static_cast<unsigned char>(text[cap]) & 0xc0) != 0x80) {
            if (characters == 280)
                break;
            ++characters;
        }
        ++cap;
    }
    end = cap;
    std::fputs(",\"snippet_parts\":[", output);
    bool comma = false;
    auto part = [&](size_t a, size_t b, bool mark) {
        if (a < begin)
            a = begin;
        if (b > end)
            b = end;
        if (b <= a)
            return;
        Vec<char> piece;
        for (size_t j = a; j < b; ++j)
            piece.push(text[j]);
        piece.push(0);
        if (comma)
            std::fputc(',', output);
        comma = true;
        std::fputs("{\"text\":", output);
        json_string(output, piece.data);
        std::fprintf(output, ",\"match\":%s}", mark ? "true" : "false");
    };
    size_t cursor = begin;
    for (size_t i = best; i < last; ++i)
        if (spans[i].hit) {
            part(cursor, spans[i].start, false);
            part(spans[i].start, spans[i].end, true);
            cursor = spans[i].end;
        }
    part(cursor, end, false);
    std::fprintf(output, "],\"snippet_prefix\":%s,\"snippet_suffix\":%s", begin ? "true" : "false",
                 end < n ? "true" : "false");
}
} // namespace ir
