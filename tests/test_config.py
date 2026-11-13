# Пояснение к файлу: Автопроверки: единый YAML, наследование лимитов и отсутствие изменения исходного словаря.
# Как читать: Подготовка фикстур/подмен зависимостей → вызов рабочей функции → assert ожидаемого поведения. Группа входит в project.ps1 tests.
# Команды запуска и структура проекта: README.md.

"""Проверки единой конфигурации без БД и сетевых запросов."""
import unittest
from src.config import get_collection_config


class CollectionConfigTests(unittest.TestCase):
    def test_common_limit_and_delay_are_inherited(self):
        config = {"logic": {"max_documents": 2000000, "delay_seconds": 0.5},
                  "collection": {"parallel_workers": 23}}
        result = get_collection_config(config)
        self.assertEqual(result, {"parallel_workers": 23, "max_documents": 2000000, "delay_seconds": 0.5})
        self.assertEqual(config["collection"], {"parallel_workers": 23})

    def test_explicit_override_is_preserved(self):
        config = {"logic": {"max_documents": 10, "delay_seconds": 1},
                  "collection": {"max_documents": 20, "delay_seconds": 2}}
        self.assertEqual(get_collection_config(config), {"max_documents": 20, "delay_seconds": 2})

    def test_missing_collection_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "collection"):
            get_collection_config({"logic": {}})
