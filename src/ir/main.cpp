// Пояснение к файлу: Командная оболочка C++ движка; сами алгоритмы находятся в engine.cpp.
// Как читать: Аргументы выбирают build/compress/stats/verify/benchmark/search. Поиск читает один запрос или поток JSONL; --protocol нужен вебу.
// Команды запуска и структура проекта: README.md.

#include "engine.h"
#include "extensions.h"
#include "skips.h"
#ifdef _WIN32
#include <shellapi.h>
#endif
namespace {
const char *usage =
    "Usage:\n  ir_engine build --manifest TSV --index FILE [--compressed] [--memory-mb 64]\n  "
    "ir_engine search --index FILE [--ranked] [--limit 50] [--offset 0] [--query TEXT]\n    "
    "[--abbrevs lab03/abbrevs.txt] [--stopwords FILE]\n  ir_engine compress --input RAW --output "
    "PACKED\n  ir_engine stats|verify|benchmark --index FILE\n";
#ifdef _WIN32
// Narrow CRT argv uses the machine code page. Read the actual Unicode command
// line so Cyrillic paths and --query work on every Windows locale.
struct Utf8Arguments {
    ir::Vec<char *> values;
    Utf8Arguments() {
        int count = 0;
        wchar_t **wide = CommandLineToArgvW(GetCommandLineW(), &count);
        if (!wide)
            ir::fail("Cannot read command line");
        try {
            for (int i = 0; i < count; ++i) {
                int n = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide[i], -1, nullptr, 0,
                                            nullptr, nullptr);
                if (!n)
                    ir::fail("Invalid command line Unicode");
                char *p = static_cast<char *>(std::malloc(n));
                if (!p)
                    ir::fail("Out of memory");
                values.push(p);
                if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide[i], -1, p, n, nullptr,
                                         nullptr))
                    ir::fail("Invalid command line Unicode");
            }
        } catch (...) {
            LocalFree(wide);
            throw;
        }
        LocalFree(wide);
    }
    ~Utf8Arguments() {
        for (size_t i = 0; i < values.size; ++i)
            std::free(values[i]);
    }
};
#endif
size_t integer(const char *s, const char *name) {
    if (!*s)
        ir::fail("Invalid %s", name);
    size_t n = 0;
    for (; *s; ++s) {
        if (*s < '0' || *s > '9' || n > (SIZE_MAX - size_t(*s - '0')) / 10)
            ir::fail("Invalid %s", name);
        n = n * 10 + size_t(*s - '0');
    }
    return n;
}
void query(ir::Index &index, const char *text, const ir::SearchOptions &options) {
    try {
        ir::search(index, text, options, stdout);
    } catch (const ir::Error &e) {
        std::fputs("{\"query\":", stdout);
        if (ir::valid_utf8(text))
            ir::json_string(stdout, text);
        else
            ir::json_string(stdout, "");
        std::fputs(",\"count\":0,\"total\":0,\"results\":[],\"error\":", stdout);
        ir::json_string(stdout, e.message);
        std::fputs("}\n", stdout);
    }
    std::fflush(stdout);
}
} // namespace
int main(int argc, char **argv) {
#ifdef _WIN32
    SetConsoleCP(CP_UTF8);
    SetConsoleOutputCP(CP_UTF8);
#endif
    try {
#ifdef _WIN32
        Utf8Arguments unicode;
        argc = static_cast<int>(unicode.values.size);
        argv = unicode.values.data;
#endif
        int extension = ir::extension_command(argc, argv);
        if (extension >= 0)
            return extension;
        if (argc < 2) {
            std::fputs(usage, stderr);
            return 1;
        }
        const char *command = argv[1];
        const char *manifest = nullptr;
        const char *index = nullptr;
        const char *input = nullptr;
        const char *output = nullptr;
        const char *text = nullptr;
        const char *abbrevs = "lab03/abbrevs.txt";
        const char *stopwords = nullptr;
        const char *skip_index = nullptr;
        const char *dictionary = nullptr;
        const char *positions = nullptr;
        const char *texts = nullptr;
        const char *offsets = nullptr;
        bool packed = false, inline_terms = false, protocol = false;
        size_t memory = 64;
        ir::SearchOptions search;
        for (int i = 2; i < argc; ++i) {
            const char *a = argv[i];
            if (std::strcmp(a, "--profile") == 0) {
                search.profile = true;
                continue;
            }
            if (std::strcmp(a, "--no-skips") == 0) {
                ir::use_skips = false;
                continue;
            }
            if (std::strcmp(a, "--protocol") == 0) {
                protocol = true;
                continue;
            }
            if (std::strcmp(a, "--inline") == 0) {
                inline_terms = true;
                continue;
            }
            if (std::strcmp(a, "--compressed") == 0) {
                packed = true;
                continue;
            }
            if (std::strcmp(a, "--ranked") == 0) {
                search.ranked = true;
                continue;
            }
            if (i + 1 >= argc)
                ir::fail("Missing value for %s", a);
            const char *v = argv[++i];
            if (std::strcmp(a, "--skip-index") == 0)
                skip_index = v;
            else if (std::strcmp(a, "--lemmas") == 0)
                dictionary = v;
            else if (std::strcmp(a, "--positions") == 0)
                positions = v;
            else if (std::strcmp(a, "--texts") == 0)
                texts = v;
            else if (std::strcmp(a, "--text-offsets") == 0)
                offsets = v;
            else if (std::strcmp(a, "--manifest") == 0)
                manifest = v;
            else if (std::strcmp(a, "--index") == 0)
                index = v;
            else if (std::strcmp(a, "--input") == 0)
                input = v;
            else if (std::strcmp(a, "--output") == 0)
                output = v;
            else if (std::strcmp(a, "--query") == 0)
                text = v;
            else if (std::strcmp(a, "--limit") == 0)
                search.limit = integer(v, "limit");
            else if (std::strcmp(a, "--offset") == 0)
                search.offset = integer(v, "offset");
            else if (std::strcmp(a, "--memory-mb") == 0)
                memory = integer(v, "memory-mb");
            else if (std::strcmp(a, "--abbrevs") == 0)
                abbrevs = v;
            else if (std::strcmp(a, "--stopwords") == 0)
                stopwords = v;
            else
                ir::fail("Unknown option: %s", a);
        }
        if (std::strcmp(command, "build") == 0) {
            if (!manifest || !index)
                ir::fail("build requires --manifest and --index");
            ir::BuildOptions options{manifest, index, packed, memory, inline_terms};
            ir::build(options, stdout);
        } else if (std::strcmp(command, "compress") == 0) {
            if (!input || !output)
                ir::fail("compress requires --input and --output");
            ir::compress(input, output, stdout);
        } else if (std::strcmp(command, "search") == 0) {
            if (!index)
                ir::fail("search requires --index");
            char error[768] = {};
            if (!ir_normalizer_init(abbrevs, stopwords, error, sizeof(error)))
                ir::fail("%s", error);
            ir::Index loaded;
            loaded.load(index);
            if (skip_index)
                ir::open_skips(loaded, skip_index);
            if (dictionary)
                ir::load_lemmas(dictionary);
            if (positions)
                ir::open_coordinates(loaded, positions);
            if (texts && offsets)
                ir::open_texts(loaded, texts, offsets);
            if (text)
                query(loaded, text, search);
            else {
                ir::Vec<char> line;
                while (ir::read_line(stdin, line, 65536)) {
                    char *value = line.data;
                    if (protocol) {
                        char *offset = std::strchr(value, '\t');
                        if (!offset)
                            ir::fail("Protocol: mode TAB offset TAB query");
                        *offset++ = 0;
                        char *body = std::strchr(offset, '\t');
                        if (!body)
                            ir::fail("Protocol: missing query");
                        *body++ = 0;
                        search.ranked = std::strcmp(value, "ranked") == 0;
                        if (!search.ranked && std::strcmp(value, "boolean"))
                            ir::fail("Protocol: invalid mode");
                        search.offset = integer(offset, "offset");
                        value = body;
                    }
                    query(loaded, value, search);
                }
            }
        } else if (std::strcmp(command, "stats") == 0 || std::strcmp(command, "verify") == 0) {
            if (!index)
                ir::fail("stats/verify requires --index");
            ir::Index loaded;
            loaded.load(index);
            loaded.verify();
            loaded.stats(stdout);
        } else if (std::strcmp(command, "benchmark") == 0) {
            if (!index)
                ir::fail("benchmark requires --index");
            ir::Index loaded;
            loaded.load(index);
            loaded.benchmark(stdout);
        } else
            ir::fail("Unknown command: %s", command);
        if (std::fflush(stdout) || std::ferror(stdout))
            ir::fail("Output write failed");
        return 0;
    } catch (const ir::Error &e) {
        std::fprintf(stderr, "Error: %s\n", e.message);
        return 1;
    } catch (...) {
        std::fputs("Error: unexpected failure\n", stderr);
        return 1;
    }
}
