# Пояснение к файлу: Низкоуровневые операции с PostgreSQL для робота.
# Как читать: Подключение → создание схемы → проверка наличия URL → подсчёт записей. Извлечение HTML выполняется в краулере.
# Команды запуска и структура проекта: README.md.

import psycopg2
from psycopg2.extensions import connection as PgConnection

from src.config import get_database_config


def connect(config: dict) -> PgConnection:
    """
    Создаёт подключение к PostgreSQL.
    """
    db_config = get_database_config(config)

    return psycopg2.connect(**db_config)


def create_tables(conn: PgConnection) -> None:
    """
    Создаёт таблицу documents, если она ещё не существует.
    """
    with conn.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id SERIAL PRIMARY KEY,

                url TEXT NOT NULL,
                normalized_url TEXT NOT NULL UNIQUE,

                html TEXT NOT NULL,
                clean_text TEXT,

                title TEXT,
                author TEXT,
                publish_date TEXT,

                source TEXT NOT NULL,

                fetch_timestamp BIGINT NOT NULL,
                last_fetch_attempt BIGINT NOT NULL,

                last_modified_header TEXT,
                etag_header TEXT
            );
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_documents_source
            ON documents(source);
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_documents_last_fetch_attempt
            ON documents(last_fetch_attempt);
            """
        )

    conn.commit()


def document_exists(
    conn: PgConnection,
    normalized_url: str,
) -> bool:
    """
    Проверяет наличие документа по нормализованному URL.
    """
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1
            FROM documents
            WHERE normalized_url = %s
            LIMIT 1;
            """,
            (normalized_url,),
        )

        return cursor.fetchone() is not None


def count_documents(conn: PgConnection) -> int:
    """
    Возвращает число документов в БД.
    """
    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM documents;")
        return cursor.fetchone()[0]