// Прыжок хранит doc-ID, предыдущий ID, порядковый номер и байтовое смещение.
// Поэтому можно не декодировать пропущенную часть сжатого списка.
#include "skips.h"
#include "extensions.h"
namespace ir {
namespace {
void put(FILE *f, uint64_t v) {
    unsigned char b[8];
    for (unsigned i = 0; i < 8; ++i)
        b[i] = static_cast<unsigned char>(v >> (8 * i));
    if (std::fwrite(b, 1, 8, f) != 8)
        fail("Skip write failed");
}
uint64_t get(FILE *f) {
    unsigned char b[8];
    if (std::fread(b, 1, 8, f) != 8)
        fail("Truncated skip index");
    uint64_t v = 0;
    for (unsigned i = 0; i < 8; ++i)
        v |= uint64_t(b[i]) << (8 * i);
    return v;
}
// Контрольная сумма основного индекса связывает таблицу прыжков с конкретным файлом.
uint64_t fingerprint(Index &index) {
    seek(index.file.fp, 88);
    return get(index.file.fp);
}
// Для термина храним начало и количество его контрольных точек в отдельном файле.
struct Descriptor {
    uint64_t offset, count;
};
// doc — цель точки, previous — база для следующей разности, ordinal — номер постинга, byte — позиция декодера.
struct Point {
    uint64_t doc, previous, ordinal, byte;
};
File skip_file;
Vec<Descriptor> descriptors;
uint64_t payload = 0;
// До 16 таблиц, суммарно около 67,11 МБ: не перечитываем метаинформацию
// при каждом запросе веба. Сами постинги всё ещё читаются по запросу.
struct PointCache {
    uint32_t term = 0;
    bool valid = false;
    Vec<Point> points;
};
PointCache point_cache[16];
size_t next_cache = 0;
// Читаем и проверяем монотонность точек; ошибочная точка не должна давать неверную выдачу.
void read_points(Index &index, uint32_t term, Vec<Point> &points) {
    const Descriptor &desc = descriptors[term];
    points.clear();
    points.reserve(size_t(desc.count));
    seek(skip_file.fp, payload + desc.offset * 32);
    uint64_t pd = 0, po = 0, pb = 0;
    for (uint64_t i = 0; i < desc.count; ++i) {
        Point p{get(skip_file.fp), get(skip_file.fp), get(skip_file.fp), get(skip_file.fp)};
        if (p.doc >= index.docs.size || p.ordinal >= index.terms[term].df ||
            p.byte >= index.terms[term].bytes ||
            (i && (p.doc <= pd || p.ordinal <= po || p.byte <= pb)) ||
            (p.ordinal && p.previous >= p.doc))
            fail("Invalid skip point");
        pd = p.doc;
        po = p.ordinal;
        pb = p.byte;
        points.push(p);
    }
}
// Повторные запросы используют небольшой кеш метаданных; большие таблицы читаются во временный массив.
const Vec<Point> &cached_points(Index &index, uint32_t term, Vec<Point> &temporary) {
    for (const auto &entry : point_cache)
        if (entry.valid && entry.term == term)
            return entry.points;
    if (descriptors[term].count > 4 * 1048576 / sizeof(Point)) {
        read_points(index, term, temporary);
        return temporary;
    }
    PointCache &entry = point_cache[next_cache++ % 16];
    entry.valid = false;
    read_points(index, term, entry.points);
    entry.term = term;
    entry.valid = true;
    return entry.points;
}
// Курсор декодирует список из буфера. Точка прыжка восстанавливает byte, ordinal и previous.
struct Cursor {
    Index &index;
    const Term &term;
    uint64_t ordinal = 0, previous = 0, byte = 0;
    uint32_t doc = 0;
    bool ready = false;
    Vec<unsigned char> data;
    Cursor(Index &idx, uint32_t id) : index(idx), term(idx.terms[id]) {
        if (term.bytes > SIZE_MAX)
            fail("Posting list too large");
        data.resize(size_t(term.bytes));
        seek(index.file.fp, index.posting_offset + term.offset);
        if (std::fread(data.data, 1, data.size, index.file.fp) != data.size)
            fail("Truncated skip posting block");
    }
    bool next() {
        if (ordinal >= term.df) {
            ready = false;
            return false;
        }
        if (index.compressed) {
            size_t pos = size_t(byte);
            uint32_t delta = vb_decode(data.data, data.size, pos),
                     tf = vb_decode(data.data, data.size, pos);
            byte = pos;
            if (!delta || !tf)
                fail("Invalid skipped posting");
            uint64_t id = ordinal ? previous + delta : uint64_t(delta) - 1;
            if (id >= index.docs.size || tf > index.docs[size_t(id)].length)
                fail("Skip doc out of range");
            doc = uint32_t(id);
        } else {
            if (byte > data.size || data.size - byte < 8)
                fail("Truncated raw skip posting");
            uint32_t id = 0;
            for (unsigned i = 0; i < 4; ++i)
                id |= uint32_t(data[size_t(byte) + i]) << (8 * i);
            byte += 8;
            doc = id;
        }
        if (ordinal && doc <= previous)
            fail("Unsorted skipped posting");
        previous = doc;
        ++ordinal;
        ready = true;
        return true;
    }
};
} // namespace
// ЛР12. Для длинных списков (df >= 256) сохраняем каждую step-ю позицию декодера.
// При stride=0 шаг равен округлённому вверх корню из df (не меньше 2).
void build_skips(const char *input, const char *output, size_t stride) {
    Index index;
    index.load(input);
    index.verify();
    File out;
    out.open(output, "w+b");
    std::fwrite("MAISKIP2", 1, 8, out.fp);
    put(out.fp, fingerprint(index));
    put(out.fp, index.bytes);
    put(out.fp, index.terms.size);
    put(out.fp, stride);
    uint64_t start = 40 + index.terms.size * 16;
    seek(out.fp, start);
    Vec<Descriptor> table;
    uint64_t points = 0;
    for (size_t t = 0; t < index.terms.size; ++t) {
        const Term &term = index.terms[t];
        Descriptor d{points, 0};
        size_t step = stride ? stride : size_t(std::ceil(std::sqrt(double(term.df))));
        if (step < 2)
            step = 2;
        if (term.df >= 256) {
            seek(index.file.fp, index.posting_offset + term.offset);
            Cursor cursor(index, uint32_t(t));
            for (uint32_t i = 0; i < term.df; ++i) {
                uint64_t byte = cursor.byte, previous = cursor.previous;
                cursor.next();
                if (i % step == 0) {
                    put(out.fp, cursor.doc);
                    put(out.fp, previous);
                    put(out.fp, i);
                    put(out.fp, byte);
                    ++d.count;
                    ++points;
                }
            }
        }
        table.push(d);
    }
    seek(out.fp, 40);
    for (size_t i = 0; i < table.size; ++i) {
        put(out.fp, table[i].offset);
        put(out.fp, table[i].count);
    }
    uint64_t length = file_size(out.fp), remaining = length, checksum = 14695981039346656037ull;
    unsigned char buffer[65536];
    while (remaining) {
        size_t n = remaining < sizeof(buffer) ? size_t(remaining) : sizeof(buffer);
        if (std::fread(buffer, 1, n, out.fp) != n)
            fail("Skip checksum read failed");
        checksum = hash_bytes(buffer, n, checksum);
        remaining -= n;
    }
    seek(out.fp, length);
    put(out.fp, checksum);
    out.close();
    std::printf("{\"terms\":%zu,\"points\":%llu,\"stride\":%zu,\"bytes\":%llu}\n", table.size,
                (unsigned long long)points, stride, (unsigned long long)(start + points * 32 + 8));
}
// Проверяем принадлежность таблицы индексу, длины секций и контрольную сумму файла.
void open_skips(Index &index, const char *path) {
    skip_file.open(path, "rb");
    char magic[8];
    if (std::fread(magic, 1, 8, skip_file.fp) != 8 || std::memcmp(magic, "MAISKIP2", 8))
        fail("Invalid skip header");
    if (get(skip_file.fp) != fingerprint(index) || get(skip_file.fp) != index.bytes ||
        get(skip_file.fp) != index.terms.size)
        fail("Skip index binding mismatch");
    get(skip_file.fp);
    uint64_t next = 0;
    for (size_t i = 0; i < index.terms.size; ++i) {
        Descriptor d{get(skip_file.fp), get(skip_file.fp)};
        if (d.offset != next || d.count > index.terms[i].df)
            fail("Invalid skip descriptor");
        next += d.count;
        descriptors.push(d);
    }
    payload = tell(skip_file.fp);
    uint64_t length = file_size(skip_file.fp);
    if (length != payload + next * 32 + 8)
        fail("Invalid skip length");
    uint64_t remaining = length - 8, checksum = 14695981039346656037ull;
    unsigned char buffer[65536];
    while (remaining) {
        size_t n = remaining < sizeof(buffer) ? size_t(remaining) : sizeof(buffer);
        if (std::fread(buffer, 1, n, skip_file.fp) != n)
            fail("Skip checksum read failed");
        checksum = hash_bytes(buffer, n, checksum);
        remaining -= n;
    }
    if (get(skip_file.fp) != checksum)
        fail("Skip checksum mismatch");
}
// Пересекаем короткий список с длинным: для очередной цели пропускаем заведомо меньшие docID.
// false означает, что прыжки неприменимы: движок выполнит обычное пересечение.
// Прыжок пропускает декодирование в уже прочитанном буфере, а не обращение к диску.
bool intersect_skips(Index &index, uint32_t a, uint32_t b, Vec<uint32_t> &out) {
    if (!skip_file.fp || !use_skips)
        return false;
    if (index.terms[a].df > index.terms[b].df) {
        uint32_t t = a;
        a = b;
        b = t;
    }
    const Descriptor &desc = descriptors[b];
    if (!desc.count)
        return false;
    Vec<Posting> short_list;
    index.read_postings(a, short_list);
    Vec<Point> temporary;
    const Vec<Point> &points = cached_points(index, b, temporary);
    Cursor cursor(index, b);
    seek(index.file.fp, index.posting_offset + index.terms[b].offset);
    size_t checkpoint = 0;
    for (size_t i = 0; i < short_list.size; ++i) {
        uint32_t target = short_list[i].doc;
        if (cursor.ready && cursor.doc >= target) {
            ++query_comparisons;
            if (cursor.doc == target)
                out.push(target);
            continue;
        }
        // Целевые ID монотонны: каждую контрольную точку рассматриваем
        // не больше одного раза, без log(число точек) для каждого документа.
        while (checkpoint + 1 < points.size && points[checkpoint + 1].doc <= target) {
            ++checkpoint;
            ++query_comparisons;
        }
        const Point &p = points[checkpoint];
        if (p.doc <= target && p.ordinal >= cursor.ordinal && p.ordinal > 0) {
            cursor.ordinal = p.ordinal;
            cursor.previous = p.previous;
            cursor.byte = p.byte;
            cursor.ready = false;
            ++query_jumps;
        }

        while ((!cursor.ready || cursor.doc < target) && cursor.next()) {
            ++query_comparisons;
        }
        if (!cursor.ready)
            break;
        if (cursor.doc == target)
            out.push(target);
    }
    return true;
}
} // namespace ir
