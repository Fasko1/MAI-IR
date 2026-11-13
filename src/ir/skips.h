// ЛР12: отдельная таблица точек перезапуска декодера VB.
#pragma once
#include "engine.h"
namespace ir {
void build_skips(const char *index, const char *output, size_t stride);
void open_skips(Index &index, const char *path);
bool intersect_skips(Index &index, uint32_t a, uint32_t b, Vec<uint32_t> &out);
} // namespace ir
