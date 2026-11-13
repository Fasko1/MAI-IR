// Пояснение к файлу: Адаптер общего токенизатора и стеммера для индексации и запросов.
// Как читать: Инициализация словарей → токенизация → необязательный стемминг → callback. Одинаковые правила не дают запросу расходиться с корпусом.
// Команды запуска и структура проекта: README.md.

// STL is confined to this adapter because the course explicitly permits it
// in tokenization. Indexing, containers, parsing, ranking and compression use none.
#include "engine.h"
#include "extensions.h"
#include "../../lab03/funcs/tokenizer.h"
#include "../../lab04/funcs/stemmer.h"
#include <string>
#include <vector>
#include <exception>
namespace {
std::vector<std::string> abbreviations;
struct StopWords {
    ir::Vec<char *> words;
    void clear() {
        for (size_t i = 0; i < words.size; ++i)
            std::free(words[i]);
        words.clear();
    }
    ~StopWords() { clear(); }
} stopwords;
void read_words(const char *path, std::vector<std::string> &result) {
    result.clear();
    if (!path || !*path)
        return;
    ir::File input;
    input.open(path, "rb");
    ir::Vec<char> line;
    while (ir::read_line(input.fp, line)) {
        const char *text = line.data;
        if (result.empty() && std::strncmp(text, "\xef\xbb\xbf", 3) == 0)
            text += 3;
        if (*text)
            result.emplace_back(text);
    }
}
void set_error(char *error, size_t size, const char *message) {
    if (size)
        std::snprintf(error, size, "%s", message);
}
} // namespace
extern "C" bool ir_normalizer_init(const char *abbr, const char *stop, char *error, size_t size) {
    try {
        read_words(abbr, abbreviations);
        stopwords.clear();
        if (stop && *stop) {
            ir::File input;
            input.open(stop, "rb");
            ir::Vec<char> line;
            while (ir::read_line(input.fp, line))
                if (*line.data)
                    stopwords.words.push(ir::copy_string(line.data));
        }
        return true;
    } catch (const ir::Error &e) {
        set_error(error, size, e.message);
    } catch (const std::exception &e) {
        set_error(error, size, e.what());
    } catch (...) {
        set_error(error, size, "Tokenizer initialization failed");
    }
    return false;
}
extern "C" bool ir_normalize(const char *input, ir_token_callback callback, void *user, char *error,
                             size_t size) {
    try {
        auto tokens = split_into_tokens(input, abbreviations);
        for (const auto &token : tokens) {
            bool skip = false;
            for (size_t i = 0; i < stopwords.words.size; ++i)
                if (token == stopwords.words[i]) {
                    skip = true;
                    break;
                }
            if (skip)
                continue;
            char *word = ir::copy_string(token.c_str());
            try {
                if (ir::has_lemmas()) {
                    const char *value = ir::lemma(word);
                    if (*value)
                        callback(value, user);
                } else {
                    extract_stem(word);
                    if (*word)
                        callback(word, user);
                }
            } catch (...) {
                std::free(word);
                throw;
            }
            std::free(word);
        }
        return true;
    } catch (const ir::Error &e) {
        set_error(error, size, e.message);
    } catch (const std::exception &e) {
        set_error(error, size, e.what());
    } catch (...) {
        set_error(error, size, "Token normalization failed");
    }
    return false;
}
extern "C" bool ir_tokenize(const char *input, ir_token_callback callback, void *user, char *error,
                            size_t size) {
    try {
        auto tokens = split_into_tokens(input, abbreviations);
        for (const auto &token : tokens)
            callback(token.c_str(), user);
        return true;
    } catch (const ir::Error &e) {
        set_error(error, size, e.message);
    } catch (const std::exception &e) {
        set_error(error, size, e.what());
    } catch (...) {
        set_error(error, size, "Tokenization failed");
    }
    return false;
}
