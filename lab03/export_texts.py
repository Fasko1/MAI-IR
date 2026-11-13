# Пояснение к файлу: Экспорт чистых текстов из существующей БД.
# Как читать: main выбирает старые файлы или --stream; export_stream использует согласованный read-only снимок и пишет TSV с метаданными.
# Команды запуска и структура проекта: README.md.

# Подготовка входа: выгружаем clean_text из PostgreSQL в data/lab03_texts/ID.txt.
"""Экспорт из существующей базы проекта, без изменения документов."""
import argparse
import json
import sys
import hashlib
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

def main():
    from src.config import load_config
    from src.db import connect
    parser = argparse.ArgumentParser()
    parser.add_argument("config", nargs="?", default=str(ROOT / "config.yaml"))
    parser.add_argument("--stream", action="store_true", help="Один TSV вместо миллиона файлов")
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    config = load_config(str(config_path))
    out = ROOT / "data" / "lab03_texts"
    out.mkdir(parents=True, exist_ok=True)
    conn = connect(config)
    if args.stream:
        export_stream(conn)
        return
    # Список документов и метаданные для последующих этапов проекта.
    ids = []
    metadata = {}
    try:
        # Именованный курсор получает данные порциями, не загружая все тексты сразу.
        with conn.cursor(name="lab03_export") as cur:
            cur.itersize = 500
            # Exclusions are reversible annotations; source documents stay in the DB.
            with conn.cursor() as check:
                check.execute("SELECT to_regclass('corpus_exclusions')")
                exclusions_exist = check.fetchone()[0] is not None
            exclusion_filter = (" WHERE NOT EXISTS (SELECT 1 FROM corpus_exclusions e WHERE e.document_id=d.id)"
                                if exclusions_exist else "")
            cur.execute("SELECT d.id, d.clean_text, d.title, d.url, d.source FROM documents d"
                        + exclusion_filter + " ORDER BY d.id")
            for doc_id, text, title, url, source in cur:
                if not text or not text.strip():
                    continue
                (out / f"{int(doc_id)}.txt").write_text(text, encoding="utf-8")
                ids.append(int(doc_id))
                metadata[str(doc_id)] = {"title": title, "url": url, "source": source}
    finally:
        conn.close()
    # Убираем из выделенной папки только устаревшие результаты прежнего экспорта.
    keep = {f"{i}.txt" for i in ids}
    for path in out.glob("*.txt"):
        if path.name not in keep:
            path.unlink()
    (out / "manifest.json").write_text(json.dumps(ids), encoding="utf-8")
    (out / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Экспортировано документов: {len(ids)}")
    if not ids:
        raise SystemExit("В базе нет непустых clean_text.")


# Именованный серверный курсор читает согласованный снимок порциями; временный TSV публикуется после успешной выгрузки.
def export_stream(conn):
    """Согласованный снимок, атомарная публикация; исходная БД только читается."""
    conn.set_session(readonly=True, isolation_level="REPEATABLE READ")
    folder = ROOT / "data/corpus"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "texts.tsv"
    pending = target.with_suffix(".tsv.building")
    count = size = 0
    sources = Counter()
    digest = hashlib.sha256()
    def clean(value):
        text = str(value or "")
        if "\x00" in text:
            raise ValueError("NUL in corpus")
        return text.replace("\t", " ").replace("\r", " ").replace("\n", " ")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('corpus_exclusions')")
            excluded = cur.fetchone()[0] is not None
        with conn.cursor(name="stream_export") as cur, pending.open("wb") as output:
            cur.itersize = 1000
            where = " WHERE d.clean_text IS NOT NULL AND btrim(d.clean_text) <> ''"
            if excluded:
                where += " AND NOT EXISTS (SELECT 1 FROM corpus_exclusions e WHERE e.document_id=d.id)"
            cur.execute("SELECT d.id,d.clean_text,d.title,d.url,d.source FROM documents d" + where + " ORDER BY d.id")
            for doc_id, text, title, url, source in cur:
                if not text.strip():
                    continue
                payload = ("\t".join([str(doc_id), clean(text), clean(title), clean(url)]) + "\n").encode("utf-8")
                output.write(payload)
                digest.update(payload)
                size += len(text.encode("utf-8"))
                count += 1
                sources[source] += 1
                if count % 25000 == 0:
                    print(f"Exported {count} documents", flush=True)
        if not count:
            raise ValueError("Empty corpus")
        pending.replace(target)
        info = {"documents": count, "text_bytes": size, "tsv_bytes": target.stat().st_size,
                "sha256": digest.hexdigest(), "sources": dict(sources),
                "exported_at_utc": datetime.now(timezone.utc).isoformat(), "format": "id TAB text TAB title TAB url; UTF-8 LF"}
        (folder / "snapshot.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Export complete: {count} documents, {size} text bytes", flush=True)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
