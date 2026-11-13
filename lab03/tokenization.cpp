// Пояснение к файлу: Файловая CLI-оболочка токенизатора для небольших примеров и тестов.
// Как читать: Проверка аргументов и каталогов → чтение ID.txt → split_into_tokens → запись ID.tokens. Правила выделены в funcs/tokenizer.cpp.
// Команды запуска и структура проекта: README.md.

// Обработка папки текстов: чтение, вызов алгоритма, запись токенов и общая статистика.
// g++ -std=c++17 -O2 tokenization.cpp funcs/tokenizer.cpp -o tokenization.exe

#include <iostream>
#include <fstream>
#include <filesystem>
#include <chrono>
#include <iomanip>
#include <cctype>
#include <iterator>

#include "funcs/tokenizer.h"

// Читаем список исключений: их написание сохраняется как в abbrevs.txt.
std::vector<std::string> fetch_abbreviations(const std::string &path) {
    std::vector<std::string> abbrev_list;
    std::ifstream input(path, std::ios::binary);
    if (!input.is_open()) {
        throw std::runtime_error("Cannot read abbreviation list: " + path);
    }

    std::string line;
    while (std::getline(input, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        line.erase(0, line.find_first_not_of(" \t\n\r"));
        line.erase(line.find_last_not_of(" \t\n\r") + 1);
        if (!line.empty()) abbrev_list.push_back(line);
    }
    if (input.bad()) throw std::runtime_error("Cannot read abbreviation list: " + path);
    return abbrev_list;
}

// Сохраняем токены документа в ID.tokens, по одному на строку.
void write_tokens(const std::string& id, const std::vector<std::string> &tokens, const std::string &output) {
    std::filesystem::create_directories(output);
    std::string filename = output + "/" + id + ".tokens";
    std::ofstream out(filename, std::ios::binary);
    if (!out) throw std::runtime_error("Cannot write tokens");
    for (const auto &t : tokens) {
        out << t << '\n';
    }
    out.close();
    if (!out) throw std::runtime_error("Cannot finish token file: " + filename);
}

// Загружаем один текстовый документ целиком в память.
std::string load_document(const std::string &path) {
    std::ifstream file(path, std::ios::binary);
    if (!file) throw std::runtime_error("Cannot read document: " + path);
    std::string text(std::istreambuf_iterator<char>(file), {});
    if (file.bad()) throw std::runtime_error("Cannot read document: " + path);
    return text;
}

// Точка входа программы. Аргументы передаются при запуске из терминала.
int main(int argc, char **argv) {
    if (argc != 4) { std::cerr << "Usage: tokenization INPUT OUTPUT ABBREVS\n"; return 1; }
    const std::string output = argv[2];

    int doc_count = 0;
    long long total_tok = 0;
    long long total_chars = 0;
    long long total_bytes = 0;

    // Общий замер включает чтение текстов и сохранение файлов токенов.
    auto start_time = std::chrono::high_resolution_clock::now();

    try {
        const auto abbreviations = fetch_abbreviations(argv[3]);
        std::filesystem::create_directories(output);
        for (const auto &entry : std::filesystem::directory_iterator(argv[1])) {
            if (!entry.is_regular_file() || entry.path().extension() != ".txt") continue;

            std::string content = load_document(entry.path().string());

            total_bytes += content.size();
            // Правила разбиения реализованы в funcs/tokenizer.cpp.
            auto tokens = split_into_tokens(content, abbreviations);

            std::string base_name = entry.path().stem().string();
            if (!is_plain_number(base_name)) throw std::runtime_error("Document filename must be a numeric id: " + base_name);
            // Пустой документ также перезаписывает выходной файл: старые токены не останутся.
            write_tokens(base_name, tokens, output);

            total_tok += tokens.size();
            for (const auto& tok : tokens) {
                total_chars += utf8_length(tok);
            }
            ++doc_count;

            if (doc_count % 1000 == 0) {
                auto now = std::chrono::high_resolution_clock::now();
                double elapsed = std::chrono::duration_cast<std::chrono::duration<double>>(now - start_time).count();
                double kb_processed = total_bytes / 1024.0;
                double speed = (elapsed > 0) ? kb_processed / elapsed : 0.0;
                std::cout << "Обработано " << doc_count << " документов, токенов: " << total_tok
                          << ", скорость: " << std::fixed << std::setprecision(2) << speed << " КБ/сек\n";
            }
        }
    } catch (const std::exception &ex) {
        std::cerr << "Ошибка: " << ex.what() << "\n";
        return 1;
    }

    auto end_time = std::chrono::high_resolution_clock::now();
    double total_sec = std::chrono::duration_cast<std::chrono::duration<double>>(end_time - start_time).count();
    double total_kb = total_bytes / 1024.0;
    double speed_kb = (total_sec > 0) ? total_kb / total_sec : 0.0;
    // Средняя длина = сумма символов / количество всех токенов, включая повторы.
    double avg_token_len = (total_tok > 0) ? static_cast<double>(total_chars) / total_tok : 0.0;

    std::cout << "\nВсего документов обработано: " << doc_count << "\n";
    std::cout << "Всего токенов: " << total_tok << "\n";
    std::cout << "Средняя длина токена: " << std::fixed << std::setprecision(2) << avg_token_len << " символов\n";
    std::cout << "Время выполнения: " << std::fixed << std::setprecision(2) << total_sec << " сек\n";
    std::cout << "Скорость токенизации: " << std::fixed << std::setprecision(2) << speed_kb << " КБ/сек\n";

    return 0;
}
