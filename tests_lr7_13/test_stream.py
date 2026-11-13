# Пояснение к файлу: Проверки потокового пути большого корпуса на малых примерах.
# Как читать: TSV → C++ препроцессор/аудит → индекс; сравнение с файловым форматом. Обнаруживает потерю метаданных и рассогласование потоков.
# Команды запуска и структура проекта: README.md.

"""Пакетный путь сравнивается с прежними CLI и независимыми ожидаемыми данными."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.project.build import EXE
BIN = ROOT / "build/labs_3_5"
ENGINE = ROOT / ("build/ir_engine" + EXE)


def run(*args, success=True):
    result = subprocess.run([str(x) for x in args], cwd=ROOT, capture_output=True, encoding="utf-8")
    if success:
        result.check_returncode()
    return result


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mai_stream_")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.texts = {"7": "Клетки ЁЖИК Агент 007 7 12000–14000 C++ альфа-бета", "2": "", "99": "чтение чтения программирование станция клетки клетки"}
        self.source = self.folder / "texts.tsv"
        self.source.write_text("".join(f"{doc}\t{text}\tЗаголовок {doc}\thttps://example.test/{doc}\n" for doc, text in self.texts.items()), encoding="utf-8")
        self.tokens, self.stems = self.folder / "tokens.tsv", self.folder / "stems.tsv"
        run(BIN / ("preprocess_stream" + EXE), self.source, self.tokens, self.stems, ROOT / "lab03/abbrevs.txt", self.folder / "counts.json")

    def test_preprocessing_matches_existing_clis(self):
        texts, tokens = self.folder / "texts", self.folder / "token_files"
        texts.mkdir()
        for doc, text in self.texts.items():
            (texts / (doc + ".txt")).write_text(text, encoding="utf-8")
        run(BIN / ("tokenization" + EXE), texts, tokens, ROOT / "lab03/abbrevs.txt")
        expected = dict((row.split("\t")[0], row.split("\t")[1]) for row in self.tokens.read_text(encoding="utf-8").splitlines())
        for doc in self.texts:
            raw = tokens / (doc + ".tokens")
            self.assertEqual(raw.read_text(encoding="utf-8").splitlines(), expected[doc].split())
            stem = self.folder / (doc + ".stems")
            run(BIN / ("stemming" + EXE), raw, stem)
            actual = next(row.split("\t")[1] for row in self.stems.read_text(encoding="utf-8").splitlines() if row.startswith(doc + "\t"))
            self.assertEqual(stem.read_text(encoding="utf-8").splitlines(), actual.split())

    def test_index_is_identical_to_file_manifest(self):
        rows = []
        for line in self.stems.read_text(encoding="utf-8").splitlines():
            doc, terms, title, url = line.split("\t")
            path = self.folder / (doc + ".stems")
            path.write_text("".join(t + "\n" for t in terms.split()), encoding="utf-8")
            rows.append(f"{doc}\t{path.as_posix()}\t{title}\t{url}\n")
        manifest = self.folder / "manifest.tsv"
        manifest.write_text("".join(rows), encoding="utf-8")
        raw, inline = self.folder / "files.ir", self.folder / "inline.ir"
        run(ENGINE, "build", "--manifest", manifest, "--index", raw)
        run(ENGINE, "build", "--inline", "--manifest", self.stems, "--index", inline)
        self.assertEqual(raw.read_bytes(), inline.read_bytes())
        result = json.loads(run(ENGINE, "search", "--index", inline, "--query", "!неизвестно").stdout)
        self.assertEqual(result["count"], 3)  # Включая пустой документ.

    def test_frequency_counts_are_independent(self):
        source = self.folder / "counts.tsv"
        source.write_text("1\tа б а\tA\t\n2\tб в\tB\t\n3\t\tEmpty\t\n", encoding="utf-8")
        freq, stats = self.folder / "freq.txt", self.folder / "stats.json"
        run(BIN / ("zipf_count" + EXE), "--inline", source, freq, stats)
        self.assertEqual(freq.read_text(encoding="utf-8"), "2 а\n2 б\n1 в\n")
        data = json.loads(stats.read_text(encoding="utf-8"))
        self.assertEqual((data["documents"], data["total_terms"], data["unique_terms"]), (3, 5, 3))

    def test_audit_checks_saved_alignment(self):
        output = self.folder / "audit.json"
        run(BIN / ("audit_stream" + EXE), self.tokens, self.stems, output)
        data = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(data["documents"], 3)
        self.assertGreater(data["changed_occurrences"], 0)
        changed = self.stems.read_text(encoding="utf-8").replace("\t\tЗаголовок 2", "\tлишнее\tЗаголовок 2")
        self.stems.write_text(changed, encoding="utf-8")
        self.assertNotEqual(run(BIN / ("audit_stream" + EXE), self.tokens, self.stems, output, success=False).returncode, 0)

    def test_invalid_utf8_is_rejected(self):
        self.source.write_bytes(b"1\tbad\xff\tTitle\tURL\n")
        result = run(BIN / ("preprocess_stream" + EXE), self.source, self.tokens, self.stems,
                     ROOT / "lab03/abbrevs.txt", self.folder / "bad.json", success=False)
        self.assertNotEqual(result.returncode, 0)

    def test_unicode_paths(self):
        folder = self.folder / "Проверка путей"
        folder.mkdir()
        tokens, stems = folder / "токены.tsv", folder / "основы.tsv"
        run(BIN / ("preprocess_stream" + EXE), self.source, tokens, stems,
            ROOT / "lab03/abbrevs.txt", folder / "статистика.json")
        run(BIN / ("audit_stream" + EXE), tokens, stems, folder / "аудит.json")
        self.assertEqual(tokens.read_bytes(), self.tokens.read_bytes())


if __name__ == "__main__":
    unittest.main(verbosity=2)
