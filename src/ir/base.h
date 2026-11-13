// Пояснение к файлу: Собственные контейнеры и файловые примитивы поискового движка без STL.
// Как читать: Vec → StringMap → сортировка и ввод-вывод → UTF-8/JSON. Это инфраструктура, а не поисковый алгоритм.
// Команды запуска и структура проекта: README.md.

#pragma once
// Small, project-owned containers. The index and query engine do not use STL.
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <cstdarg>
#include <cmath>
#include <climits>
#include <cstddef>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace ir {
struct Error {
    char message[768];
};
[[noreturn]] inline void fail(const char *format, ...) {
    Error e;
    va_list args;
    va_start(args, format);
    std::vsnprintf(e.message, sizeof(e.message), format, args);
    va_end(args);
    throw e;
}
// Растущий массив с realloc: применяется для простых записей, не вызывает конструкторы/деструкторы элементов как std::vector.
template <class T> struct Vec {
    T *data = nullptr;
    size_t size = 0, capacity = 0;
    Vec() = default;
    Vec(const Vec &) = delete;
    Vec &operator=(const Vec &) = delete;
    ~Vec() { std::free(data); }
    void reserve(size_t n) {
        if (n <= capacity)
            return;
        if (n > SIZE_MAX / sizeof(T))
            fail("Container size overflow");
        size_t next = capacity ? capacity : 16;
        while (next < n) {
            if (next > SIZE_MAX / 2) {
                next = n;
                break;
            }
            next *= 2;
        }
        if (next > SIZE_MAX / sizeof(T))
            next = n;
        void *p = std::realloc(data, next * sizeof(T));
        if (!p)
            fail("Out of memory");
        data = static_cast<T *>(p);
        capacity = next;
    }
    void resize(size_t n) {
        reserve(n);
        if (n > size)
            std::memset(data + size, 0, (n - size) * sizeof(T));
        size = n;
    }
    void push(T value) {
        reserve(size + 1);
        data[size++] = value;
    }
    void clear() { size = 0; }
    T &operator[](size_t i) { return data[i]; }
    const T &operator[](size_t i) const { return data[i]; }
    void swap(Vec &other) {
        T *p = data;
        data = other.data;
        other.data = p;
        size_t n = size;
        size = other.size;
        other.size = n;
        n = capacity;
        capacity = other.capacity;
        other.capacity = n;
    }
};
inline char *copy_string(const char *s) {
    size_t n = std::strlen(s) + 1;
    char *p = static_cast<char *>(std::malloc(n));
    if (!p)
        fail("Out of memory");
    std::memcpy(p, s, n);
    return p;
}
inline uint64_t hash_bytes(const void *bytes, size_t n, uint64_t h = 14695981039346656037ull) {
    const unsigned char *p = static_cast<const unsigned char *>(bytes);
    for (size_t i = 0; i < n; ++i) {
        h ^= p[i];
        h *= 1099511628211ull;
    }
    return h;
}
struct HashEntry {
    const char *key;
    uint32_t value;
};
// Хеш-таблица строк с открытой адресацией и линейным пробированием; ключи принадлежат вызывающему коду.
struct StringMap {
    Vec<HashEntry> table;
    size_t used = 0;
    bool find(const char *key, uint32_t &value) const {
        if (!table.size)
            return false;
        size_t at = static_cast<size_t>(hash_bytes(key, std::strlen(key))) & (table.size - 1);
        while (table[at].key) {
            if (std::strcmp(table[at].key, key) == 0) {
                value = table[at].value;
                return true;
            }
            at = (at + 1) & (table.size - 1);
        }
        return false;
    }
    void insert(const char *key, uint32_t value) {
        if (!table.size || (used + 1) * 10 >= table.size * 7) {
            Vec<HashEntry> old;
            old.swap(table);
            table.resize(old.size ? old.size * 2 : 32);
            used = 0;
            for (size_t i = 0; i < old.size; ++i)
                if (old[i].key)
                    insert(old[i].key, old[i].value);
        }
        size_t at = static_cast<size_t>(hash_bytes(key, std::strlen(key))) & (table.size - 1);
        while (table[at].key) {
            if (std::strcmp(table[at].key, key) == 0)
                fail("Duplicate key: %s", key);
            at = (at + 1) & (table.size - 1);
        }
        table[at] = {key, value};
        ++used;
    }
};
// In-place heap sort: O(n log n), O(1) additional memory, no library sorter.
template <class T, class Less> void sort(T *a, size_t n, Less less) {
    if (n < 2)
        return;
    auto sift = [&](size_t root, size_t end) {
        while (root < end / 2) {
            size_t child = root * 2 + 1;
            if (child + 1 < end && less(a[child], a[child + 1]))
                ++child;
            if (!less(a[root], a[child]))
                break;
            T tmp = a[root];
            a[root] = a[child];
            a[child] = tmp;
            root = child;
        }
    };
    for (size_t i = n / 2; i > 0; --i)
        sift(i - 1, n);
    for (size_t end = n; end > 1; --end) {
        T tmp = a[0];
        a[0] = a[end - 1];
        a[end - 1] = tmp;
        sift(0, end - 1);
    }
}
inline bool valid_utf8(const char *s) {
    const unsigned char *p = reinterpret_cast<const unsigned char *>(s);
    while (*p) {
        uint32_t c = *p++;
        unsigned n = 0;
        if (c < 128)
            continue;
        if (c >= 0xc2 && c <= 0xdf) {
            n = 1;
            c &= 31;
        } else if (c >= 0xe0 && c <= 0xef) {
            n = 2;
            c &= 15;
        } else if (c >= 0xf0 && c <= 0xf4) {
            n = 3;
            c &= 7;
        } else
            return false;
        for (unsigned i = 0; i < n; ++i) {
            if ((*p & 0xc0) != 0x80)
                return false;
            c = (c << 6) | (*p++ & 63);
        }
        if ((n == 1 && c < 128) || (n == 2 && c < 2048) || (n == 3 && c < 65536) || c > 0x10ffff ||
            (c >= 0xd800 && c <= 0xdfff))
            return false;
    }
    return true;
}
#ifdef _WIN32
struct WidePath {
    Vec<wchar_t> data;
    explicit WidePath(const char *s) {
        int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s, -1, nullptr, 0);
        if (!n)
            fail("Invalid UTF-8 path");
        data.resize(n);
        if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s, -1, data.data, n))
            fail("Invalid UTF-8 path");
    }
};
#endif
inline FILE *open_file(const char *path, const char *mode) {
#ifdef _WIN32
    WidePath p(path);
    wchar_t m[8] = {};
    size_t n = std::strlen(mode);
    if (n > 7)
        fail("Invalid file mode");
    for (size_t i = 0; i < n; ++i)
        m[i] = mode[i];
    return _wfopen(p.data.data, m);
#else
    return std::fopen(path, mode);
#endif
}
inline void remove_file(const char *path) {
#ifdef _WIN32
    WidePath p(path);
    _wremove(p.data.data);
#else
    std::remove(path);
#endif
}
inline void replace_file(const char *source, const char *target) {
#ifdef _WIN32
    WidePath s(source), t(target);
    if (!MoveFileExW(s.data.data, t.data.data, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
        fail("Cannot publish index: %s", target);
#else
    if (std::rename(source, target))
        fail("Cannot publish index: %s", target);
#endif
}
struct File {
    FILE *fp = nullptr;
    File() = default;
    File(const File &) = delete;
    File &operator=(const File &) = delete;
    ~File() {
        if (fp)
            std::fclose(fp);
    }
    void open(const char *path, const char *mode) {
        fp = open_file(path, mode);
        if (!fp)
            fail("Cannot open file: %s", path);
    }
    void close() {
        if (fp) {
            FILE *p = fp;
            fp = nullptr;
            if (std::fclose(p))
                fail("File close/write failed");
        }
    }
};
inline uint64_t tell(FILE *f) {
#ifdef _WIN32
    auto n = _ftelli64(f);
#else
    auto n = ftello(f);
#endif
    if (n < 0)
        fail("Cannot determine file offset");
    return static_cast<uint64_t>(n);
}
inline void seek(FILE *f, uint64_t offset) {
    if (offset > INT64_MAX)
        fail("File offset overflow");
#ifdef _WIN32
    if (_fseeki64(f, static_cast<int64_t>(offset), SEEK_SET))
        fail("Cannot seek file");
#else
    if (fseeko(f, static_cast<off_t>(offset), SEEK_SET))
        fail("Cannot seek file");
#endif
}
inline uint64_t file_size(FILE *f) {
#ifdef _WIN32
    if (_fseeki64(f, 0, SEEK_END))
        fail("Cannot seek file");
#else
    if (fseeko(f, 0, SEEK_END))
        fail("Cannot seek file");
#endif
    uint64_t n = tell(f);
    seek(f, 0);
    return n;
}
inline bool read_line(FILE *f, Vec<char> &line, size_t limit = 1048576) {
    line.clear();
    int c;
    while ((c = std::fgetc(f)) != EOF && c != '\n') {
        if (c == 0)
            fail("NUL byte in text input");
        if (line.size >= limit)
            fail("Input line exceeds %zu bytes", limit);
        line.push(static_cast<char>(c));
    }
    if (std::ferror(f))
        fail("Text input read failed");
    if (c == EOF && !line.size)
        return false;
    if (line.size && line[line.size - 1] == '\r')
        --line.size;
    line.push(0);
    return true;
}
inline void json_string(FILE *out, const char *s) {
    std::fputc('"', out);
    for (const unsigned char *p = reinterpret_cast<const unsigned char *>(s); *p; ++p) {
        if (*p == '"' || *p == '\\') {
            std::fputc('\\', out);
            std::fputc(*p, out);
        } else if (*p < 32)
            std::fprintf(out, "\\u%04x", *p);
        else
            std::fputc(*p, out);
    }
    std::fputc('"', out);
}
} // namespace ir
