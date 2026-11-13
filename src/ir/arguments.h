// Пояснение к файлу: Преобразование аргументов Windows из UTF-16 в UTF-8.
// Как читать: Utf8Arguments владеет преобразованными строками до выхода из main; алгоритмы получают обычный char**.
// Команды запуска и структура проекта: README.md.

#pragma once
#include "base.h"
#ifdef _WIN32
#include <shellapi.h>
namespace ir {
// CRT narrow argv depends on the Windows code page; file APIs here expect UTF-8.
struct Utf8Arguments {
    Vec<char *> values;
    Utf8Arguments() {
        int count = 0;
        wchar_t **args = CommandLineToArgvW(GetCommandLineW(), &count);
        if (!args)
            fail("Cannot read Unicode arguments");
        try {
            for (int i = 0; i < count; ++i) {
                int n = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, args[i], -1, nullptr, 0,
                                            nullptr, nullptr);
                if (!n)
                    fail("Invalid Unicode argument");
                char *p = static_cast<char *>(std::malloc(n));
                if (!p)
                    fail("Out of memory");
                values.push(p);
                if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, args[i], -1, p, n, nullptr,
                                         nullptr))
                    fail("Argument conversion failed");
            }
        } catch (...) {
            LocalFree(args);
            throw;
        }
        LocalFree(args);
    }
    ~Utf8Arguments() {
        for (size_t i = 0; i < values.size; ++i)
            std::free(values[i]);
    }
};
} // namespace ir
#endif
