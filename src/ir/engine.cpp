// Пояснение к файлу: Реализация ЛР7, 8, 11 и 13: индекс, булев поиск, VB и TF-IDF.
// Как читать: Порядок чтения: Reader/Writer → внешняя сортировка → кодек и Index → build/compress → Parser → evaluate/search.
// Команды запуска и структура проекта: README.md.

#include "engine.h"
#include "extensions.h"
#include "skips.h"
#include <ctime>
namespace ir {
namespace {
const uint32_t VERSION = 1, HEADER_SIZE = 96;
const char MAGIC[8] = {'M', 'A', 'I', 'I', 'R', 'I', 'D', 'X'};
double seconds() {
#ifdef _WIN32
    LARGE_INTEGER frequency, counter;
    QueryPerformanceFrequency(&frequency);
    QueryPerformanceCounter(&counter);
    return static_cast<double>(counter.QuadPart) / static_cast<double>(frequency.QuadPart);
#else
    timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
#endif
}
// Бинарная запись little-endian: секции получают одну контрольную сумму FNV; структуры C++ напрямую в файл не копируются.
struct Writer {
    FILE *file;
    uint64_t checksum = 14695981039346656037ull;
    bool hashing = false;
    explicit Writer(FILE *f) : file(f) {}
    void put(const void *p, size_t n) {
        if (n && std::fwrite(p, 1, n, file) != n)
            fail("Index write failed");
        if (hashing)
            checksum = hash_bytes(p, n, checksum);
    }
    void u32(uint32_t v) {
        unsigned char b[4];
        for (unsigned i = 0; i < 4; ++i)
            b[i] = static_cast<unsigned char>(v >> (8 * i));
        put(b, 4);
    }
    void u64(uint64_t v) {
        unsigned char b[8];
        for (unsigned i = 0; i < 8; ++i)
            b[i] = static_cast<unsigned char>(v >> (8 * i));
        put(b, 8);
    }
    void real(double v) {
        static_assert(sizeof(double) == 8, "IEEE-754 binary64 required");
        uint64_t u;
        std::memcpy(&u, &v, 8);
        u64(u);
    }
    void text(const char *s) {
        size_t n = std::strlen(s);
        if (n > UINT32_MAX)
            fail("String too long");
        u32(static_cast<uint32_t>(n));
        put(s, n);
    }
};
// Симметричное чтение с границами секции: ошибочная длина строки не должна вывести чтение за пределы индекса.
struct Reader {
    FILE *file;
    uint64_t end;
    Reader(FILE *f, uint64_t e) : file(f), end(e) {}
    void get(void *p, size_t n) {
        if (end != UINT64_MAX) {
            uint64_t pos = tell(file);
            if (pos > end || n > end - pos)
                fail("Truncated index section");
        }
        if (n && std::fread(p, 1, n, file) != n)
            fail("Index read failed");
    }
    uint32_t u32() {
        unsigned char b[4];
        get(b, 4);
        uint32_t u = 0;
        for (unsigned i = 0; i < 4; ++i)
            u |= uint32_t(b[i]) << (8 * i);
        return u;
    }
    uint64_t u64() {
        unsigned char b[8];
        get(b, 8);
        uint64_t u = 0;
        for (unsigned i = 0; i < 8; ++i)
            u |= uint64_t(b[i]) << (8 * i);
        return u;
    }
    double real() {
        uint64_t u = u64();
        double v;
        std::memcpy(&v, &u, 8);
        return v;
    }
    char *text(size_t limit = 1048576) {
        uint32_t n = u32();
        uint64_t pos = tell(file);
        if (n > limit || pos > end || n > end - pos)
            fail("Invalid string length");
        char *s = static_cast<char *>(std::malloc(size_t(n) + 1));
        if (!s)
            fail("Out of memory");
        try {
            get(s, n);
            s[n] = 0;
            if (std::strlen(s) != n || !valid_utf8(s))
                fail("Invalid UTF-8/NUL in index string");
        } catch (...) {
            std::free(s);
            throw;
        }
        return s;
    }
};
char *suffixed(const char *base, const char *suffix) {
    size_t a = std::strlen(base), b = std::strlen(suffix);
    char *p = static_cast<char *>(std::malloc(a + b + 1));
    if (!p)
        fail("Out of memory");
    std::memcpy(p, base, a);
    std::memcpy(p + a, suffix, b + 1);
    return p;
}
// Временный файл принадлежит объекту и удаляется при выходе, включая исключения; публикация выполняется после verify.
struct TempFile {
    char *path;
    File file;
    TempFile(const char *target, const char *suffix) : path(suffixed(target, suffix)) {
        try {
            file.open(path, "w+b");
        } catch (...) {
            std::free(path);
            throw;
        }
    }
    ~TempFile() {
        if (file.fp) {
            std::fclose(file.fp);
            file.fp = nullptr;
        }
        remove_file(path);
        std::free(path);
    }
};
void header(Writer &out, bool packed, uint64_t docs, uint64_t terms, uint64_t postings,
            uint64_t tokens, uint64_t term_offset, uint64_t post_offset, uint64_t size,
            uint64_t checksum) {
    out.put(MAGIC, 8);
    out.u32(VERSION);
    out.u32(packed ? 1 : 0);
    out.u32(HEADER_SIZE);
    out.u32(0);
    out.u64(docs);
    out.u64(terms);
    out.u64(postings);
    out.u64(tokens);
    out.u64(HEADER_SIZE);
    out.u64(term_offset);
    out.u64(post_offset);
    out.u64(size);
    out.u64(checksum);
}
void write_index(const char *target, const Vec<Document> &docs, const Vec<Term> &terms,
                 FILE *posting_file, bool packed, uint64_t postings, uint64_t tokens) {
    TempFile tmp(target, ".building");
    Writer out(tmp.file.fp);
    header(out, packed, docs.size, terms.size, postings, tokens, 0, 0, 0, 0);
    out.hashing = true;
    for (size_t i = 0; i < docs.size; ++i) {
        const auto &d = docs[i];
        out.text(d.id);
        out.text(d.title);
        out.text(d.url);
        out.u64(d.length);
        out.real(d.norm);
    }
    uint64_t term_offset = tell(out.file);
    for (size_t i = 0; i < terms.size; ++i) {
        const auto &t = terms[i];
        out.text(t.text);
        out.u32(t.df);
        out.u64(t.offset);
        out.u64(t.bytes);
    }
    uint64_t post_offset = tell(out.file);
    seek(posting_file, 0);
    unsigned char buffer[65536];
    size_t n;
    while ((n = std::fread(buffer, 1, sizeof(buffer), posting_file)))
        out.put(buffer, n);
    if (std::ferror(posting_file))
        fail("Temporary postings read failed");
    uint64_t size = tell(out.file), checksum = out.checksum;
    out.hashing = false;
    seek(out.file, 0);
    header(out, packed, docs.size, terms.size, postings, tokens, term_offset, post_offset, size,
           checksum);
    tmp.file.close();
    // Validate the finished temporary file before replacing an existing index.
    // Closing the reader first is necessary for atomic replacement on Windows.
    {
        Index candidate;
        candidate.load(tmp.path);
        candidate.verify();
    }
    replace_file(tmp.path, target);
}
struct Record {
    uint32_t term, doc, tf;
};
bool record_less(const Record &a, const Record &b) {
    return a.term < b.term || (a.term == b.term && a.doc < b.doc);
}
struct Run {
    char *path;
    FILE *file;
    Record current;
    bool valid;
    uint64_t remaining;
};
// Буфер записей сортируется по (term, doc), затем сбрасывается в серию на диск: весь список постингов не хранится в RAM.
struct Runs {
    Vec<Run> runs;
    const char *base;
    size_t peak = 0;
    explicit Runs(const char *p) : base(p) {}
    ~Runs() {
        for (size_t i = 0; i < runs.size; ++i) {
            if (runs[i].file)
                std::fclose(runs[i].file);
            remove_file(runs[i].path);
            std::free(runs[i].path);
        }
    }
    void spill(Vec<Record> &buffer) {
        if (!buffer.size)
            return;
        if (buffer.size > peak)
            peak = buffer.size;
        sort(buffer.data, buffer.size, record_less);
        char suffix[80];
        std::snprintf(suffix, sizeof(suffix), ".run.%zu.tmp", runs.size);
        Run r{};
        r.path = suffixed(base, suffix);
        runs.push(r);
        File f;
        f.open(r.path, "wb");
        Writer w(f.fp);
        w.u64(buffer.size);
        for (size_t i = 0; i < buffer.size; ++i) {
            w.u32(buffer[i].term);
            w.u32(buffer[i].doc);
            w.u32(buffer[i].tf);
        }
        f.close();
        buffer.clear();
    }
    void advance(size_t i) {
        Run &r = runs[i];
        r.valid = r.remaining > 0;
        if (r.valid) {
            Reader in(r.file, UINT64_MAX);
            r.current.term = in.u32();
            r.current.doc = in.u32();
            r.current.tf = in.u32();
            --r.remaining;
        }
    }
    void open() {
        for (size_t i = 0; i < runs.size; ++i) {
            Run &r = runs[i];
            r.file = open_file(r.path, "rb");
            if (!r.file)
                fail("Cannot open sorted run");
            Reader in(r.file, UINT64_MAX);
            r.remaining = in.u64();
            advance(i);
        }
    }
};
// Мини-куча хранит текущую запись каждой серии; k-путевое слияние выбирает минимальную за O(log k).
struct RunHeap {
    Vec<uint32_t> ids;
    Runs &runs;
    explicit RunHeap(Runs &r) : runs(r) {}
    bool less(uint32_t a, uint32_t b) {
        return record_less(runs.runs[a].current, runs.runs[b].current);
    }
    void push(uint32_t id) {
        ids.push(id);
        size_t at = ids.size - 1;
        while (at) {
            size_t parent = (at - 1) / 2;
            if (!less(ids[at], ids[parent]))
                break;
            uint32_t tmp = ids[at];
            ids[at] = ids[parent];
            ids[parent] = tmp;
            at = parent;
        }
    }
    uint32_t pop() {
        uint32_t id = ids[0];
        ids[0] = ids[--ids.size];
        size_t at = 0;
        while (at * 2 + 1 < ids.size) {
            size_t child = at * 2 + 1;
            if (child + 1 < ids.size && less(ids[child + 1], ids[child]))
                ++child;
            if (!less(ids[child], ids[at]))
                break;
            uint32_t tmp = ids[at];
            ids[at] = ids[child];
            ids[child] = tmp;
            at = child;
        }
        return id;
    }
};
// raw пишет doc/tf по 32 бита; packed кодирует первый doc+1, затем положительные разности ID и TF.
void write_posting(Writer &out, bool packed, uint32_t doc, uint32_t tf, uint32_t &previous,
                   bool first) {
    if (!tf || (!first && doc <= previous))
        fail("Unsorted or invalid posting");
    if (packed) {
        Vec<unsigned char> encoded;
        vb_encode(first ? doc + 1 : doc - previous, encoded);
        vb_encode(tf, encoded);
        out.put(encoded.data, encoded.size);
    } else {
        out.u32(doc);
        out.u32(tf);
    }
    previous = doc;
}
} // namespace
// VB: младшие 7 бит идут первыми, старший бит 1 означает последний байт; это соглашение должен повторять декодер.
void vb_encode(uint32_t value, Vec<unsigned char> &out) {
    while (value >= 128) {
        out.push(static_cast<unsigned char>(value & 127));
        value >>= 7;
    }
    out.push(static_cast<unsigned char>(value | 128));
}
// Декодер отклоняет обрыв, переполнение uint32 и неоднозначную запись с лишними нулевыми группами.
uint32_t vb_decode(const unsigned char *bytes, size_t count, size_t &position) {
    uint32_t value = 0;
    for (unsigned i = 0; i < 5; ++i) {
        if (position >= count)
            fail("Truncated variable-byte integer");
        unsigned b = bytes[position++];
        if (i == 4 && (b & 127) > 15)
            fail("Variable-byte integer overflow");
        value |= uint32_t(b & 127) << (7 * i);
        if (b & 128) {
            if (i && !(b & 127))
                fail("Noncanonical variable-byte integer");
            return value;
        }
    }
    fail("Variable-byte integer exceeds five bytes");
}
Index::~Index() {
    for (size_t i = 0; i < docs.size; ++i) {
        std::free(docs[i].id);
        std::free(docs[i].title);
        std::free(docs[i].url);
    }
    for (size_t i = 0; i < terms.size; ++i)
        std::free(terms[i].text);
}
// Загружаются словарь и метаданные; сами постинги остаются в файле и читаются по offset/bytes нужного термина.
void Index::load(const char *path) {
    if (file.fp || docs.size || terms.size)
        fail("Index already loaded");
    file.open(path, "rb");
    bytes = file_size(file.fp);
    if (bytes < HEADER_SIZE)
        fail("Truncated index header");
    Reader in(file.fp, bytes);
    char magic[8];
    in.get(magic, 8);
    if (std::memcmp(magic, MAGIC, 8))
        fail("Wrong index magic");
    if (in.u32() != VERSION)
        fail("Unsupported index version");
    uint32_t flags = in.u32();
    if (flags > 1)
        fail("Unsupported index flags");
    compressed = flags == 1;
    if (in.u32() != HEADER_SIZE || in.u32() != 0)
        fail("Invalid header size/reserved field");
    uint64_t nd = in.u64(), nt = in.u64();
    postings = in.u64();
    tokens = in.u64();
    uint64_t doc_offset = in.u64(), term_offset = in.u64();
    posting_offset = in.u64();
    uint64_t expected = in.u64(), checksum = in.u64();
    if (expected != bytes || doc_offset != HEADER_SIZE || term_offset < doc_offset ||
        posting_offset < term_offset || posting_offset > bytes)
        fail("Invalid index section offsets/size");
    document_bytes = term_offset - doc_offset;
    dictionary_bytes = posting_offset - term_offset;
    if (nd >= UINT32_MAX || nt >= UINT32_MAX || nd > (term_offset - doc_offset) / 28 ||
        nt > (posting_offset - term_offset) / 24)
        fail("Invalid dictionary/document counts");
    uint64_t hash = 14695981039346656037ull;
    unsigned char buffer[65536];
    size_t n;
    while ((n = std::fread(buffer, 1, sizeof(buffer), file.fp)))
        hash = hash_bytes(buffer, n, hash);
    if (std::ferror(file.fp) || hash != checksum)
        fail("Index checksum mismatch");
    seek(file.fp, doc_offset);
    in.end = term_offset;
    docs.reserve(static_cast<size_t>(nd));
    StringMap ids;
    uint64_t total_length = 0;
    for (uint64_t i = 0; i < nd; ++i) {
        Document d{};
        docs.push(d);
        Document &item = docs[docs.size - 1];
        item.id = in.text(4096);
        item.title = in.text();
        item.url = in.text();
        item.length = in.u64();
        item.norm = in.real();
        if (!*item.id || !std::isfinite(item.norm) || item.norm < 0 ||
            UINT64_MAX - total_length < item.length)
            fail("Invalid document metadata");
        total_length += item.length;
        uint32_t old;
        if (ids.find(item.id, old))
            fail("Duplicate document id in index");
        ids.insert(item.id, static_cast<uint32_t>(i));
    }
    if (tell(file.fp) != term_offset || total_length != tokens)
        fail("Invalid document table/token total");
    in.end = posting_offset;
    terms.reserve(static_cast<size_t>(nt));
    uint64_t next_offset = 0, total_postings = 0;
    for (uint64_t i = 0; i < nt; ++i) {
        Term t{};
        terms.push(t);
        Term &item = terms[terms.size - 1];
        item.text = in.text(65536);
        item.df = in.u32();
        item.offset = in.u64();
        item.bytes = in.u64();
        if (!*item.text || !item.df || item.df > nd || item.offset != next_offset ||
            item.bytes > bytes - posting_offset - next_offset)
            fail("Invalid posting descriptor");
        if ((!compressed && item.bytes != uint64_t(item.df) * 8) ||
            (compressed &&
             (item.bytes < uint64_t(item.df) * 2 || item.bytes > uint64_t(item.df) * 10)))
            fail("Invalid posting length");
        next_offset += item.bytes;
        total_postings += item.df;
        uint32_t old;
        if (vocabulary.find(item.text, old))
            fail("Duplicate vocabulary term");
        vocabulary.insert(item.text, static_cast<uint32_t>(i));
    }
    if (tell(file.fp) != posting_offset || next_offset != bytes - posting_offset ||
        total_postings != postings)
        fail("Invalid vocabulary/posting totals");
}
void Index::read_postings(uint32_t id, Vec<Posting> &out) {
    if (id >= terms.size)
        fail("Term id outside dictionary");
    const Term &term = terms[id];
    seek(file.fp, posting_offset + term.offset);
    out.clear();
    out.reserve(term.df);
    if (!compressed) {
        Vec<unsigned char> data;
        data.resize(static_cast<size_t>(term.bytes));
        Reader in(file.fp, posting_offset + term.offset + term.bytes);
        in.get(data.data, data.size);
        for (uint32_t i = 0; i < term.df; ++i) {
            Posting p{0, 0};
            const unsigned char *b = data.data + size_t(i) * 8;
            for (unsigned j = 0; j < 4; ++j) {
                p.doc |= uint32_t(b[j]) << (8 * j);
                p.tf |= uint32_t(b[j + 4]) << (8 * j);
            }
            if (p.doc >= docs.size || !p.tf || (i && p.doc <= out[i - 1].doc) ||
                p.tf > docs[p.doc].length)
                fail("Invalid raw posting");
            out.push(p);
        }
    } else {
        Vec<unsigned char> data;
        data.resize(static_cast<size_t>(term.bytes));
        Reader in(file.fp, posting_offset + term.offset + term.bytes);
        in.get(data.data, data.size);
        size_t pos = 0;
        uint32_t previous = 0;
        for (uint32_t i = 0; i < term.df; ++i) {
            uint32_t delta = vb_decode(data.data, data.size, pos),
                     tf = vb_decode(data.data, data.size, pos);
            if (!delta)
                fail("Zero document gap");
            uint64_t doc = i ? uint64_t(previous) + delta : uint64_t(delta) - 1;
            if (doc >= docs.size || !tf || tf > docs[static_cast<size_t>(doc)].length)
                fail("Invalid compressed posting");
            out.push({static_cast<uint32_t>(doc), tf});
            previous = static_cast<uint32_t>(doc);
        }
        if (pos != data.size)
            fail("Trailing bytes in compressed posting");
    }
}
// Полный проход по постингам проверяет порядок ID, TF, число вхождений и сохранённые нормы документов.
void Index::verify() {
    Vec<uint64_t> lengths;
    lengths.resize(docs.size);
    Vec<double> norms;
    norms.resize(docs.size);
    Vec<Posting> list;
    for (size_t i = 0; i < terms.size; ++i) {
        read_postings(static_cast<uint32_t>(i), list);
        double idf = std::log1p(double(docs.size) / terms[i].df);
        for (size_t j = 0; j < list.size; ++j) {
            Posting p = list[j];
            if (UINT64_MAX - lengths[p.doc] < p.tf)
                fail("Token count overflow");
            lengths[p.doc] += p.tf;
            double w = (1 + std::log(double(p.tf))) * idf;
            norms[p.doc] += w * w;
        }
    }
    for (size_t i = 0; i < docs.size; ++i) {
        double norm = std::sqrt(norms[i]);
        if (lengths[i] != docs[i].length || std::fabs(norm - docs[i].norm) > 1e-10 * (1 + norm))
            fail("Posting/document metadata disagreement");
    }
}
void Index::stats(FILE *out) const {
    uint64_t chars = 0;
    for (size_t i = 0; i < terms.size; ++i)
        for (const unsigned char *p = reinterpret_cast<const unsigned char *>(terms[i].text); *p;
             ++p)
            if ((*p & 0xc0) != 0x80)
                ++chars;
    std::fprintf(
        out,
        "{\"format_version\":%u,\"compressed\":%s,\"documents\":%zu,\"terms\":%zu,\"postings\":%"
        "llu,\"tokens\":%llu,\"bytes\":%llu,\"document_bytes\":%llu,\"dictionary_bytes\":%llu,"
        "\"posting_bytes\":%llu,\"average_term_chars\":%.8f,\"verified\":true}\n",
        VERSION, compressed ? "true" : "false", docs.size, terms.size,
        static_cast<unsigned long long>(postings), static_cast<unsigned long long>(tokens),
        static_cast<unsigned long long>(bytes), static_cast<unsigned long long>(document_bytes),
        static_cast<unsigned long long>(dictionary_bytes),
        static_cast<unsigned long long>(bytes - posting_offset),
        terms.size ? double(chars) / terms.size : 0);
}
void Index::benchmark(FILE *out) {
    struct Group {
        uint64_t terms = 0, postings = 0, bytes = 0, checksum = 14695981039346656037ull;
        double seconds = 0;
    };
    Group groups[4];
    Vec<Posting> list;
    for (size_t i = 0; i < terms.size; ++i) {
        const Term &term = terms[i];
        size_t group = term.df <= 2 ? 0 : term.df <= 100 ? 1 : 2;
        double start = seconds();
        read_postings(static_cast<uint32_t>(i), list);
        double elapsed = seconds() - start;
        // Hash canonical little-endian decoded pairs, independent of encoding.
        for (size_t g = 0; g < 4; ++g)
            if (g == group || g == 3) {
                Group &item = groups[g];
                ++item.terms;
                item.postings += list.size;
                item.bytes += term.bytes;
                item.seconds += elapsed;
                for (size_t j = 0; j < list.size; ++j) {
                    unsigned char pair[8];
                    for (unsigned k = 0; k < 4; ++k) {
                        pair[k] = static_cast<unsigned char>(list[j].doc >> (8 * k));
                        pair[k + 4] = static_cast<unsigned char>(list[j].tf >> (8 * k));
                    }
                    item.checksum = hash_bytes(pair, 8, item.checksum);
                }
            }
    }
    const char *names[] = {"rare", "medium", "frequent", "all"};
    std::fprintf(out, "{\"compressed\":%s,\"groups\":{", compressed ? "true" : "false");
    for (size_t g = 0; g < 4; ++g) {
        const Group &item = groups[g];
        if (g)
            std::fputc(',', out);
        std::fprintf(out,
                     "\"%s\":{\"terms\":%llu,\"postings\":%llu,\"bytes\":%llu,\"seconds\":%.9f,"
                     "\"checksum\":\"%016llx\"}",
                     names[g], static_cast<unsigned long long>(item.terms),
                     static_cast<unsigned long long>(item.postings),
                     static_cast<unsigned long long>(item.bytes), item.seconds,
                     static_cast<unsigned long long>(item.checksum));
    }
    std::fputs("}}\n", out);
}
// ЛР7: документ → term-ID/TF → ограниченный буфер серий → внешнее слияние → индекс; словарь и метаданные остаются в RAM.
void build(const BuildOptions &options, FILE *stats) {
    double started = seconds();
    if (!options.memory_mb || options.memory_mb > 4096)
        fail("memory-mb must be between 1 and 4096");
    Index index;
    index.compressed = options.compressed;
    StringMap ids;
    File manifest;
    manifest.open(options.manifest, "rb");
    Runs runs(options.index);
    Vec<Record> records;
    Vec<uint32_t> words;
    Vec<char> line;
    size_t max_records = options.memory_mb * 1048576 / sizeof(Record);
    while (read_line(manifest.fp, line, options.inline_terms ? 64 * 1048576 : 1048576)) {
        char *row = line.data;
        if (index.docs.size == 0 && std::strncmp(row, "\xef\xbb\xbf", 3) == 0)
            row += 3;
        if (!*row)
            continue;
        char *fields[4] = {row, nullptr, nullptr, nullptr};
        size_t count = 1;
        for (char *p = row; *p; ++p)
            if (*p == '\t') {
                if (count == 4)
                    fail("Manifest has more than four columns");
                *p = 0;
                fields[count++] = p + 1;
            }
        if (count < 2 || !*fields[0] || (!options.inline_terms && !*fields[1]) ||
            !valid_utf8(fields[0]) || std::strlen(fields[0]) > 4096)
            fail("Manifest requires UTF-8 id<TAB>path[<TAB>title<TAB>url]");
        uint32_t old;
        if (ids.find(fields[0], old))
            fail("Duplicate manifest document id: %s", fields[0]);
        if (index.docs.size >= UINT32_MAX - 1)
            fail("Too many documents");
        index.docs.push({nullptr, nullptr, nullptr, 0, 0});
        Document &doc = index.docs[index.docs.size - 1];
        doc.id = copy_string(fields[0]);
        doc.title = copy_string(count > 2 ? fields[2] : fields[0]);
        doc.url = copy_string(count > 3 ? fields[3] : "");
        if (!valid_utf8(doc.title) || !valid_utf8(doc.url))
            fail("Invalid UTF-8 manifest metadata");
        uint32_t docid = static_cast<uint32_t>(index.docs.size - 1);
        ids.insert(doc.id, docid);
        words.clear();
        auto add_word = [&](const char *text) {
            if (!valid_utf8(text) || std::strlen(text) > 65536)
                fail("Invalid UTF-8 token or excessive length");
            for (const char *p = text; *p; ++p)
                if (static_cast<unsigned char>(*p) <= 32)
                    fail("Invalid whitespace in token");
            uint32_t id;
            if (!index.vocabulary.find(text, id)) {
                if (index.terms.size >= UINT32_MAX - 1)
                    fail("Too many terms");
                id = static_cast<uint32_t>(index.terms.size);
                index.terms.push({copy_string(text), 0, 0, 0});
                index.vocabulary.insert(index.terms[id].text, id);
            }
            words.push(id);
        };
        if (options.inline_terms) {
            char *p = fields[1];
            while (*p) {
                char *start = p;
                while (*p && *p != ' ')
                    ++p;
                if (*p)
                    *p++ = 0;
                if (*start)
                    add_word(start);
            }
        } else {
            File source;
            source.open(fields[1], "rb");
            Vec<char> token;
            while (read_line(source.fp, token, 65536))
                if (*token.data)
                    add_word(token.data);
        }
        doc.length = words.size;
        if (UINT64_MAX - index.tokens < words.size)
            fail("Token total overflow");
        index.tokens += words.size;
        sort(words.data, words.size, [](uint32_t a, uint32_t b) { return a < b; });
        for (size_t i = 0; i < words.size;) {
            size_t j = i + 1;
            while (j < words.size && words[j] == words[i])
                ++j;
            if (j - i > UINT32_MAX)
                fail("Term frequency overflow");
            records.push({words[i], docid, static_cast<uint32_t>(j - i)});
            ++index.terms[words[i]].df;
            ++index.postings;
            i = j;
            if (records.size >= max_records)
                runs.spill(records);
        }
        if (index.docs.size % 25000 == 0)
            std::fprintf(stderr, "Indexed %zu documents, %zu terms\n", index.docs.size,
                         index.terms.size);
    }
    // Все серии готовы. Слияние одновременно пишет постинги и накапливает квадрат нормы TF-IDF каждого документа.
    runs.spill(records);
    manifest.close();
    TempFile posting(options.index, ".postings.tmp");
    Writer out(posting.file.fp);
    runs.open();
    RunHeap heap(runs);
    for (size_t i = 0; i < runs.runs.size; ++i)
        if (runs.runs[i].valid)
            heap.push(static_cast<uint32_t>(i));
    uint32_t current = UINT32_MAX, previous = 0;
    uint64_t emitted = 0;
    bool first = true;
    while (heap.ids.size) {
        uint32_t runid = heap.pop();
        Record r = runs.runs[runid].current;
        if (r.term != current) {
            if (current != UINT32_MAX)
                index.terms[current].bytes = tell(out.file) - index.terms[current].offset;
            current = r.term;
            index.terms[current].offset = tell(out.file);
            first = true;
        }
        write_posting(out, index.compressed, r.doc, r.tf, previous, first);
        first = false;
        double idf = std::log1p(double(index.docs.size) / index.terms[r.term].df);
        double weight = (1 + std::log(double(r.tf))) * idf;
        index.docs[r.doc].norm += weight * weight;
        ++emitted;
        runs.advance(runid);
        if (runs.runs[runid].valid)
            heap.push(runid);
    }
    if (current != UINT32_MAX)
        index.terms[current].bytes = tell(out.file) - index.terms[current].offset;
    if (emitted != index.postings)
        fail("Merge posting count mismatch");
    for (size_t i = 0; i < index.docs.size; ++i)
        index.docs[i].norm = std::sqrt(index.docs[i].norm);
    write_index(options.index, index.docs, index.terms, posting.file.fp, index.compressed,
                index.postings, index.tokens);
    Index checked;
    checked.load(options.index);
    std::fprintf(stats,
                 "{\"documents\":%zu,\"terms\":%zu,\"postings\":%llu,\"tokens\":%llu,\"bytes\":%"
                 "llu,\"posting_bytes\":%llu,\"compressed\":%s,\"runs\":%zu,\"peak_record_bytes\":%"
                 "zu,\"seconds\":%.6f,\"verified\":true}\n",
                 checked.docs.size, checked.terms.size,
                 static_cast<unsigned long long>(checked.postings),
                 static_cast<unsigned long long>(checked.tokens),
                 static_cast<unsigned long long>(checked.bytes),
                 static_cast<unsigned long long>(checked.bytes - checked.posting_offset),
                 checked.compressed ? "true" : "false", runs.runs.size, runs.peak * sizeof(Record),
                 seconds() - started);
}
// ЛР11: читаем те же списки и переписываем только представление постингов; словарь, метаданные, TF и нормы сохраняются.
void compress(const char *input, const char *output, FILE *stats) {
    if (std::strcmp(input, output) == 0)
        fail("Input and output index paths must differ");
    double started = seconds();
    Index index;
    index.load(input);
    index.verify();
    uint64_t original = index.bytes, original_post = index.bytes - index.posting_offset;
    TempFile posting(output, ".postings.tmp");
    Writer out(posting.file.fp);
    Vec<Posting> list;
    for (size_t i = 0; i < index.terms.size; ++i) {
        index.read_postings(static_cast<uint32_t>(i), list);
        Term &term = index.terms[i];
        uint64_t offset = tell(out.file);
        uint32_t previous = 0;
        for (size_t j = 0; j < list.size; ++j)
            write_posting(out, true, list[j].doc, list[j].tf, previous, j == 0);
        term.offset = offset;
        term.bytes = tell(out.file) - offset;
    }
    write_index(output, index.docs, index.terms, posting.file.fp, true, index.postings,
                index.tokens);
    Index checked;
    checked.load(output);
    std::fprintf(
        stats,
        "{\"raw_bytes\":%llu,\"compressed_bytes\":%llu,\"raw_posting_bytes\":%llu,\"compressed_"
        "posting_bytes\":%llu,\"ratio\":%.8f,\"seconds\":%.6f,\"verified\":true}\n",
        static_cast<unsigned long long>(original), static_cast<unsigned long long>(checked.bytes),
        static_cast<unsigned long long>(original_post),
        static_cast<unsigned long long>(checked.bytes - checked.posting_offset),
        original ? double(checked.bytes) / original : 0, seconds() - started);
}

namespace {
enum Kind { END, WORD, AND, OR, NOT, LEFT, RIGHT, EMPTY, PHRASE };
struct Node {
    Kind kind;
    uint32_t term;
    int left, right;
};
struct Lexeme {
    Kind kind;
    uint32_t term;
};
bool equals_keyword(const char *s, const char *expected) {
    while (*s && *expected) {
        char c = *s++;
        if (c >= 'a' && c <= 'z')
            c = char(c - 'a' + 'A');
        if (c != *expected++)
            return false;
    }
    return !*s && !*expected;
}
// Рекурсивный спуск: unary (!/скобки/термин) → conjunction (&&) → expression (||). В ranked без операторов пробел означает OR.
struct Parser {
    Index &index;
    Vec<Lexeme> lexemes;
    Vec<Node> nodes;
    size_t position = 0;
    bool implicit_or = false, operators = false;
    Vec<char *> phrases;
    explicit Parser(Index &i) : index(i) {}
    ~Parser() {
        for (size_t i = 0; i < phrases.size; ++i)
            std::free(phrases[i]);
    }
    static void token(const char *text, void *context) {
        Parser &self = *static_cast<Parser *>(context);
        uint32_t id;
        if (!self.index.vocabulary.find(text, id))
            id = UINT32_MAX;
        self.lexemes.push({WORD, id});
    }
    void lex(const char *query, bool ranked) {
        if (std::strlen(query) > 65536)
            fail("Query exceeds 65536 bytes");
        if (!valid_utf8(query))
            fail("Query must be UTF-8");
        const char *p = query;
        Vec<char> word;
        while (*p) {
            if (static_cast<unsigned char>(*p) <= 32) {
                ++p;
                continue;
            }
            if (*p == '"') {
                ++p;
                word.clear();
                while (*p && *p != '"')
                    word.push(*p++);
                if (!*p)
                    fail("Unclosed phrase quote");
                ++p;
                word.push(0);
                phrases.push(copy_string(word.data));
                lexemes.push({PHRASE, uint32_t(phrases.size - 1)});
                operators = true;
                if (lexemes.size > 4096)
                    fail("Too many lexemes");
                continue;
            }
            Kind kind = END;
            if (*p == '(') {
                kind = LEFT;
                ++p;
            } else if (*p == ')') {
                kind = RIGHT;
                ++p;
            } else if (*p == '!') {
                kind = NOT;
                ++p;
            } else if (*p == '&' || *p == '|') {
                char c = *p++;
                if (*p != c)
                    fail("Use && or ||, not a single operator character");
                ++p;
                kind = c == '&' ? AND : OR;
            }
            if (kind != END) {
                operators = true;
                lexemes.push({kind, 0});
                if (lexemes.size > 4096)
                    fail("Query exceeds 4096 lexemes");
                continue;
            }
            word.clear();
            while (*p && static_cast<unsigned char>(*p) > 32 && *p != '(' && *p != ')' &&
                   *p != '!' && *p != '&' && *p != '|' && *p != '"')
                word.push(*p++);
            word.push(0);
            if (equals_keyword(word.data, "AND"))
                kind = AND;
            else if (equals_keyword(word.data, "OR"))
                kind = OR;
            else if (equals_keyword(word.data, "NOT"))
                kind = NOT;
            if (kind != END) {
                operators = true;
                lexemes.push({kind, 0});
            } else {
                size_t before = lexemes.size;
                char error[768] = {};
                if (!ir_normalize(word.data, token, this, error, sizeof(error)))
                    fail("Query normalization: %s", error);
                if (before == lexemes.size)
                    lexemes.push({EMPTY, 0});
            }
            if (lexemes.size > 4096)
                fail("Query exceeds 4096 lexemes");
        }
        lexemes.push({END, 0});
        implicit_or = ranked && !operators;
    }
    Kind peek() const { return lexemes[position].kind; }
    int add(Kind kind, uint32_t term = 0, int left = -1, int right = -1) {
        nodes.push({kind, term, left, right});
        return static_cast<int>(nodes.size - 1);
    }
    bool starts(Kind k) { return k == WORD || k == PHRASE || k == EMPTY || k == NOT || k == LEFT; }
    int unary(unsigned depth) {
        if (depth > 128)
            fail("Query nesting exceeds 128 levels");
        Kind k = peek();
        if (k == NOT) {
            ++position;
            int child = unary(depth + 1);
            return add(NOT, 0, child);
        }
        if (k == LEFT) {
            ++position;
            int child = expression(depth + 1);
            if (peek() != RIGHT)
                fail("Missing closing parenthesis");
            ++position;
            return child;
        }
        if (k == WORD || k == PHRASE || k == EMPTY) {
            Lexeme l = lexemes[position++];
            return add(k, l.term);
        }
        fail("Expected term, NOT or opening parenthesis");
    }
    int conjunction(unsigned depth) {
        int root = unary(depth);
        while (peek() == AND || (!implicit_or && starts(peek()))) {
            if (peek() == AND)
                ++position;
            int child = unary(depth);
            root = add(AND, 0, root, child);
        }
        return root;
    }
    int expression(unsigned depth) {
        int root = conjunction(depth);
        while (peek() == OR || (implicit_or && starts(peek()))) {
            if (peek() == OR)
                ++position;
            int child = conjunction(depth);
            root = add(OR, 0, root, child);
        }
        return root;
    }
    int parse() {
        if (peek() == END)
            return add(EMPTY);
        int root = expression(0);
        if (peek() != END)
            fail("Unexpected closing parenthesis or operator");
        return root;
    }
};
// Вычисление дерева даёт отсортированные doc-ID: AND — пересечение, OR — объединение, NOT — дополнение относительно всех документов.
void evaluate(Parser &parser, int root, Vec<uint32_t> &output, unsigned depth = 0) {
    // Long flat expressions produce left-deep trees. Limit independent from
    // parentheses, keeping the C++ call stack bounded for adversarial input.
    if (depth > 2048)
        fail("Query expression too complex");
    const Node &n = parser.nodes[static_cast<size_t>(root)];
    if (n.kind == EMPTY || (n.kind == WORD && n.term == UINT32_MAX))
        return;
    if (n.kind == PHRASE) {
        exact_phrase(parser.index, parser.phrases[n.term], output);
        return;
    }
    if (n.kind == WORD) {
        Vec<Posting> list;
        parser.index.read_postings(n.term, list);
        output.reserve(list.size);
        for (size_t i = 0; i < list.size; ++i)
            output.push(list[i].doc);
        return;
    }
    if (n.kind == AND) {
        const Node &a = parser.nodes[size_t(n.left)];
        const Node &b = parser.nodes[size_t(n.right)];
        if (a.kind == WORD && b.kind == WORD && a.term != UINT32_MAX && b.term != UINT32_MAX &&
            intersect_skips(parser.index, a.term, b.term, output))
            return;
    }
    Vec<uint32_t> left;
    evaluate(parser, n.left, left, depth + 1);
    if (n.kind == NOT) {
        size_t j = 0;
        output.reserve(parser.index.docs.size - left.size);
        for (size_t i = 0; i < parser.index.docs.size; ++i) {
            if (j < left.size && left[j] == i)
                ++j;
            else
                output.push(static_cast<uint32_t>(i));
        }
        return;
    }
    Vec<uint32_t> right;
    evaluate(parser, n.right, right, depth + 1);
    size_t i = 0, j = 0;
    output.reserve(n.kind == AND ? (left.size < right.size ? left.size : right.size)
                                 : left.size + right.size);
    // Статические прыжки с шагом sqrt длины: пропускаем целые блоки,
    // только когда их последний ID меньше искомого. OR пропускать нельзя.
    size_t left_step = size_t(std::sqrt(double(left.size))),
           right_step = size_t(std::sqrt(double(right.size)));
    while (i < left.size || j < right.size) {
        if (n.kind == AND) {
            if (i >= left.size || j >= right.size)
                break;
            if (use_skips && left_step > 1 && left[i] < right[j])
                while (i + left_step < left.size && left[i + left_step] <= right[j]) {
                    i += left_step;
                    ++query_jumps;
                }
            if (use_skips && right_step > 1 && right[j] < left[i])
                while (j + right_step < right.size && right[j + right_step] <= left[i]) {
                    j += right_step;
                    ++query_jumps;
                }
        }
        ++query_comparisons;
        if (i < left.size && j < right.size && left[i] == right[j]) {
            output.push(left[i]);
            ++i;
            ++j;
        } else if (j == right.size || (i < left.size && left[i] < right[j])) {
            if (n.kind == OR)
                output.push(left[i]);
            ++i;
        } else {
            if (n.kind == OR)
                output.push(right[j]);
            ++j;
        }
    }
}
// Отрицательные ветви фильтруют выдачу, но не увеличивают вес запроса; двойное NOT снова делает полярность положительной.
void positive_terms(const Parser &p, int root, bool negative, Vec<uint32_t> &ids,
                    unsigned depth = 0) {
    if (depth > 2048)
        fail("Query expression too complex");
    const Node &n = p.nodes[static_cast<size_t>(root)];
    if (n.kind == PHRASE) {
        if (!negative) {
            struct Context {
                const Parser *p;
                Vec<uint32_t> *ids;
                static void token(const char *text, void *data) {
                    auto &c = *static_cast<Context *>(data);
                    uint32_t id;
                    if (c.p->index.vocabulary.find(text, id))
                        c.ids->push(id);
                }
            };
            Context c{&p, &ids};
            char error[768] = {};
            if (!ir_normalize(p.phrases[n.term], Context::token, &c, error, sizeof(error)))
                fail("%s", error);
        }
        return;
    }
    if (n.kind == WORD) {
        if (!negative && n.term != UINT32_MAX)
            ids.push(n.term);
        return;
    }
    if (n.kind == EMPTY)
        return;
    positive_terms(p, n.left, n.kind == NOT ? !negative : negative, ids, depth + 1);
    if (n.kind == AND || n.kind == OR)
        positive_terms(p, n.right, negative, ids, depth + 1);
}
struct Hit {
    uint32_t doc;
    double score;
};
} // namespace
// Сначала булевы кандидаты, затем при ranked — cosine TF-IDF, сортировка и только потом срез страницы; count относится ко всей выдаче.
void search(Index &index, const char *query, const SearchOptions &options, FILE *output) {
    query_comparisons = 0;
    query_jumps = 0;
    double query_started = seconds();
    Parser parser(index);
    parser.lex(query, options.ranked);
    int root = parser.parse();
    Vec<uint32_t> matches;
    evaluate(parser, root, matches);
    Vec<Hit> hits;
    hits.reserve(matches.size);
    // Вес = (1+ln TF)*ln(1+N/DF); норма документа заранее включает все его термины, норма запроса считается здесь.
    Vec<double> scores;
    if (options.ranked) {
        scores.resize(index.docs.size);
        Vec<uint32_t> ids;
        positive_terms(parser, root, false, ids);
        sort(ids.data, ids.size, [](uint32_t a, uint32_t b) { return a < b; });
        double query_norm = 0;
        Vec<Posting> postings;
        for (size_t i = 0; i < ids.size;) {
            size_t end = i + 1;
            while (end < ids.size && ids[end] == ids[i])
                ++end;
            uint32_t term = ids[i];
            double idf = std::log1p(double(index.docs.size) / index.terms[term].df);
            double qw = (1 + std::log(double(end - i))) * idf;
            query_norm += qw * qw;
            index.read_postings(term, postings);
            for (size_t j = 0; j < postings.size; ++j) {
                const Posting &p = postings[j];
                scores[p.doc] += qw * (1 + std::log(double(p.tf))) * idf;
            }
            i = end;
        }
        query_norm = std::sqrt(query_norm);
        for (size_t i = 0; i < matches.size; ++i) {
            uint32_t id = matches[i];
            double score = query_norm > 0 && index.docs[id].norm > 0
                               ? scores[id] / (query_norm * index.docs[id].norm)
                               : 0;
            if (score > 1 && score < 1 + 1e-12)
                score = 1;
            hits.push({id, score});
        }
    } else
        for (size_t i = 0; i < matches.size; ++i)
            hits.push({matches[i], 0});
    sort(hits.data, hits.size, [&](const Hit &a, const Hit &b) {
        if (options.ranked && a.score != b.score)
            return a.score > b.score;
        return std::strcmp(index.docs[a.doc].id, index.docs[b.doc].id) < 0;
    });
    Vec<uint32_t> snippet_terms;
    positive_terms(parser, root, false, snippet_terms);
    std::fputs("{\"query\":", output);
    json_string(output, query);
    std::fprintf(output, ",\"count\":%zu,\"total\":%zu,\"offset\":%zu,\"results\":[", hits.size,
                 hits.size, options.offset);
    size_t begin = options.offset < hits.size ? options.offset : hits.size;
    size_t count = hits.size - begin;
    if (count > options.limit)
        count = options.limit;
    for (size_t i = 0; i < count; ++i) {
        const Hit &h = hits[begin + i];
        const Document &d = index.docs[h.doc];
        if (i)
            std::fputc(',', output);
        std::fputs("{\"id\":", output);
        json_string(output, d.id);
        std::fputs(",\"title\":", output);
        json_string(output, d.title);
        std::fputs(",\"url\":", output);
        json_string(output, d.url);
        std::fprintf(output, ",\"score\":%.17g", h.score);
        snippet_json(index, h.doc, snippet_terms, output);
        std::fputc('}', output);
    }
    if (options.profile) {
        std::fprintf(output, "],\"comparisons\":%llu,\"jumps\":%llu,\"search_seconds\":%.9f}\n",
                     (unsigned long long)query_comparisons, (unsigned long long)query_jumps,
                     seconds() - query_started);
    } else
        std::fputs("]}\n", output);
}
} // namespace ir
