// ЛР4 (леммы), ЛР10 (координаты), ЛР12 (прыжки), ЛР14 (сниппеты).
// Собственные контейнеры; принятый формат index.ir остаётся совместимым.
#pragma once
#include "engine.h"
namespace ir {
void load_lemmas(const char *path);
const char *lemma(const char *token); // неизвестное слово сохраняется
bool has_lemmas();
int extension_command(int argc, char **argv); // -1: обычная команда движка
void open_coordinates(Index &index, const char *path);
void open_texts(Index &index, const char *texts, const char *offsets);
void exact_phrase(Index &index, const char *phrase, Vec<uint32_t> &out);
void snippet_json(Index &index, uint32_t doc, const Vec<uint32_t> &query, FILE *out);
extern bool use_skips;
extern uint64_t query_comparisons, query_jumps;
} // namespace ir
