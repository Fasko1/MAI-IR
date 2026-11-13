# Пояснение к файлу: Единый доступ к config.yaml и секретам из .env.
# Как читать: load_config проверяет секции; get_database_config добавляет пароль; get_collection_config наследует общие лимиты.
# Команды запуска и структура проекта: README.md.

from pathlib import Path
import os

import yaml
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


def load_config(config_path: str) -> dict:
    """
    Загружает YAML-конфигурацию проекта.
    """
    path = Path(config_path)

    if not path.exists():
        raise FileNotFoundError(f"Файл конфигурации не найден: {path}")

    with path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    if not isinstance(config, dict):
        raise ValueError("config.yaml должен содержать YAML-объект.")

    if "db" not in config:
        raise ValueError("В config.yaml отсутствует секция 'db'.")

    if "logic" not in config:
        raise ValueError("В config.yaml отсутствует секция 'logic'.")

    return config


def get_database_config(config: dict) -> dict:
    """
    Возвращает параметры подключения к PostgreSQL.

    Пароль берётся из .env и не хранится в config.yaml.
    """
    db = config["db"]

    password = os.getenv("POSTGRES_PASSWORD")

    if not password:
        raise RuntimeError(
            "Не задан POSTGRES_PASSWORD в файле .env."
        )

    return {
        "host": db.get("host", os.getenv("POSTGRES_HOST", "localhost")),
        "port": int(db.get("port", os.getenv("POSTGRES_PORT", "5432"))),
        "database": db.get(
            "database",
            os.getenv("POSTGRES_DB", "mai_ir"),
        ),
        "user": db.get(
            "user",
            os.getenv("POSTGRES_USER", "postgres"),
        ),
        "password": password,
    }


# Копируем секцию collection: частные значения имеют приоритет, отсутствующие лимиты наследуются из logic.
def get_collection_config(config: dict) -> dict:
    """Настройки расширенного сбора из единого конфига, без изменения входа."""
    section = config.get("collection")
    if not isinstance(section, dict):
        raise ValueError("В config.yaml отсутствует секция 'collection'.")
    result = dict(section)
    logic = config["logic"]
    result.setdefault("max_documents", logic["max_documents"])
    result.setdefault("delay_seconds", logic["delay_seconds"])
    return result
