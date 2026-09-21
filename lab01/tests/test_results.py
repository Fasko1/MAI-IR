import json
import unittest
from pathlib import Path


LAB_DIR = Path(__file__).resolve().parent.parent

PMC_FILE = LAB_DIR / "results" / "PMC8935530.json"
ERIC_FILE = LAB_DIR / "results" / "EJ854309.json"


class TestProcessedDocuments(unittest.TestCase):

    def load_json(self, path: Path) -> dict:
        self.assertTrue(path.exists(), f"Файл не найден: {path}")

        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def test_pmc_id(self) -> None:
        data = self.load_json(PMC_FILE)
        self.assertEqual(data["id"], "PMC8935530")

    def test_eric_id(self) -> None:
        data = self.load_json(ERIC_FILE)
        self.assertEqual(data["id"], "EJ854309")

    def test_pmc_title_not_empty(self) -> None:
        data = self.load_json(PMC_FILE)
        self.assertTrue(data["title"].strip())

    def test_eric_title_not_empty(self) -> None:
        data = self.load_json(ERIC_FILE)
        self.assertTrue(data["title"].strip())

    def test_pmc_authors(self) -> None:
        data = self.load_json(PMC_FILE)
        self.assertGreater(len(data["authors"]), 0)

    def test_eric_authors(self) -> None:
        data = self.load_json(ERIC_FILE)
        self.assertGreater(len(data["authors"]), 0)

    def test_pmc_text_is_large(self) -> None:
        data = self.load_json(PMC_FILE)
        self.assertGreater(len(data["text"]), 1000)

    def test_eric_text_is_large(self) -> None:
        data = self.load_json(ERIC_FILE)
        self.assertGreater(len(data["text"]), 1000)

    def test_common_structure(self) -> None:
        pmc = self.load_json(PMC_FILE)
        eric = self.load_json(ERIC_FILE)

        required_fields = {
            "id",
            "source",
            "title",
            "authors",
            "publication_date",
            "keywords",
            "abstract",
            "text",
        }

        self.assertTrue(required_fields.issubset(pmc.keys()))
        self.assertTrue(required_fields.issubset(eric.keys()))


if __name__ == "__main__":
    unittest.main()