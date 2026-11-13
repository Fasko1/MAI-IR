# Пояснение к файлу: Локальная HTML-оболочка над C++ поиском.
# Как читать: page рисует форму; create_server разбирает HTTP; SearchWorker держит один процесс с индексом; main запускает сервер и закрывает worker.
# Команды запуска и структура проекта: README.md.

"""HTML-форма поиска. Парсер, индекс и TF-IDF выполняются C++-программой."""
import argparse
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
import queue
from urllib.parse import parse_qs, urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.project.build import EXE

PAGE_SIZE = 50


# Разрешаем только абсолютные HTTP(S) ссылки; заголовки и запросы дополнительно экранируются при HTML-выводе.
def safe_link(url):
    parsed = urlsplit(url)
    return url if parsed.scheme.lower() in ("http", "https") and parsed.netloc else ""


# Сборка HTML без поискового алгоритма: форма, число результатов, ссылки, score и переходы по 50 документов.
def page(query="", ranked=False, offset=0, data=None, error=""):
    esc = html.escape
    checked = " checked" if ranked else ""
    results = ""
    if error:
        results = '<p class="error" role="alert">' + esc(error) + "</p>"
    elif data is not None:
        total = data["count"]
        results = '<p class="meta">Найдено документов: <b>' + str(total) + "</b>"
        if data["results"]:
            results += " · Показаны " + str(offset + 1) + "–" + str(offset + len(data["results"]))
        results += "</p><ol start='" + str(offset + 1) + "'>"
        for row in data["results"]:
            title = esc(str(row.get("title") or "Документ " + str(row["id"])))
            url = safe_link(str(row.get("url", "")))
            heading = '<a href="' + esc(url, quote=True) + '" rel="noopener noreferrer">' + title + "</a>" if url else title
            score = " · TF-IDF: " + format(row["score"], ".6f") if ranked else ""
            results += "<li><h2>" + heading + '</h2><p class="meta">ID: ' + esc(str(row["id"])) + score + "</p>"
            if url:
                results += '<p class="url">' + esc(url) + "</p>"
            if row.get("snippet_parts"):
                # Весь исходный текст экранируется; доверяем только булевому
                # признаку совпадения, а не HTML из корпуса или движка.
                fragments = [("<mark>" + esc(part["text"]) + "</mark>")
                             if part.get("match") else esc(part["text"])
                             for part in row["snippet_parts"]]
                results += '<p class="snippet">' + ("…" if row.get("snippet_prefix") else "") + "".join(fragments) + ("…" if row.get("snippet_suffix") else "") + "</p>"
            results += "</li>"
        results += "</ol><nav aria-label='Страницы результатов'>"
        for new_offset, label in ((offset - PAGE_SIZE, "← Предыдущие 50"), (offset + PAGE_SIZE, "Следующие 50 →")):
            if 0 <= new_offset < total:
                link = "/?" + urlencode({"q": query, "mode": "ranked" if ranked else "boolean", "offset": new_offset})
                results += '<a class="pager" href="' + esc(link, quote=True) + '">' + label + "</a>"
        results += "</nav>"
    return ('''<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Научный поиск · MAI-IR</title><style>
:root{font-family:system-ui,Arial,sans-serif;color:#1a2925;background:#f5f6f1}
body{margin:0}main{max-width:900px;margin:0 auto;padding:48px 24px}header{border-top:4px solid #286446;padding-top:20px}
.eyebrow{font-size:12px;letter-spacing:2px;color:#48644e}h1{font-size:clamp(30px,5vw,48px);font-weight:650;letter-spacing:-1.5px;margin:14px 0}
.intro{color:#536359;line-height:1.6}form{margin:28px 0;padding:22px;background:white;border:1px solid #d9e0d7;border-radius:12px}
.search{display:flex;gap:10px}input[type=search]{flex:1;min-width:0;padding:14px;font:inherit;border:1px solid #aab9ac;border-radius:6px}
button,.pager{background:#286446;color:white;padding:14px 22px;border:0;border-radius:6px;font:inherit;cursor:pointer}
label{display:block;margin:16px 0 0}code{background:#edf0e9;padding:2px 5px;border-radius:3px}.hint,.meta{font-size:14px;color:#56695b;line-height:1.6}
ol{padding-left:28px}li{padding:18px 0;border-bottom:1px solid #d9e0d7}h2{font-size:19px;font-weight:600;margin:0 0 6px}a{color:#205b3e}
.url{font-size:12px;color:#657267;overflow-wrap:anywhere;margin:4px 0}.meta{margin:6px 0}.error{padding:18px;background:#fff0e9;border:1px solid #d9a18c;border-radius:6px}
nav{display:flex;gap:12px;margin:28px 0}.pager{text-decoration:none;font-size:14px}footer{color:#6a786d;font-size:12px;margin-top:40px}
.snippet{line-height:1.6;margin:10px 0;color:#374b40;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}mark{background:#fff0a6;color:inherit}
@media(max-width:520px){main{padding:24px 16px}.search{flex-direction:column}form{padding:16px}}
</style><main><header><span class="eyebrow">MAI · ИНФОРМАЦИОННЫЙ ПОИСК</span>
<h1>Наука ближе, чем кажется.</h1><p class="intro">Поиск по корпусу статей о науке и технологиях.</p></header>
<form method="get"><div class="search"><input type="search" name="q" aria-label="Поисковый запрос" placeholder="Например: клетки && иммунитет" value="'''
            + esc(query, quote=True) + '''" maxlength="16000" required><button type="submit">Найти</button></div>
<label><input type="checkbox" name="mode" value="ranked"''' + checked + '''> Ранжировать по TF-IDF</label>
<p class="hint">Операторы: <code>&amp;&amp;</code> — И, <code>||</code> — ИЛИ, <code>!</code> — НЕ, скобки.
В булевом режиме слова через пробел соединяются И. С TF-IDF простой запрос находит хотя бы одно слово;
явные операторы сохраняют заданное условие. Цитату вводите в двойных кавычках: <code>"искусственный интеллект"</code>.
Цитата требует соседних токенов в заданном порядке, без подмены словоформ.</p></form>''' + results +
            '''<footer>MAI-IR · Булев поиск, TF-IDF, цитаты и сниппеты · По 50 результатов на странице</footer></main></html>''')


# HTTP-параметры проверяются до запуска поиска; ошибки запроса возвращаются пользователю, сервер продолжает работать.
def create_server(index, host="127.0.0.1", port=8080, engine=None):
    index = Path(index).resolve()
    executable = Path(engine or ROOT / ("build/ir_engine" + EXE)).resolve()
    worker = SearchWorker(executable, index)

    class Server(ThreadingHTTPServer):
        def server_close(self):
            super().server_close()
            worker.close()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == "/health":
                status = 200 if executable.is_file() and index.is_file() else 503
                return self.respond(status, json.dumps({"ready": status == 200}), "application/json")
            if parsed.path != "/":
                return self.respond(404, page(error="Страница не найдена."))
            params = parse_qs(parsed.query, keep_blank_values=True)
            query = params.get("q", [""])[0]
            ranked = params.get("mode", [""])[0] == "ranked"
            try:
                offset = max(0, int(params.get("offset", ["0"])[0]))
                if offset > 2**31 - 1:
                    raise ValueError
            except ValueError:
                return self.respond(400, page(query, ranked, error="Некорректный номер страницы."))
            if len(query) > 16000:
                return self.respond(400, page(error="Слишком длинный запрос (максимум 16 000 символов)."))
            if not query.strip():
                return self.respond(200, page(query, ranked))
            try:
                data = worker.search(query, ranked, offset)
                if data.get("error"):
                    return self.respond(422, page(query, ranked, offset, error="Ошибка запроса: " + str(data["error"])))
                return self.respond(200, page(query, ranked, offset, data))
            except TimeoutError:
                return self.respond(504, page(query, ranked, error="Превышено время ожидания поиска."))
            except (OSError, ValueError, KeyError, RuntimeError):
                return self.respond(503, page(query, ranked, error="Поисковый индекс недоступен. Выполните project.ps1 rebuild."))

        def respond(self, status, body, kind="text/html"):
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

    return Server((host, port), Handler)


# Один долгоживущий C++ процесс загружает индекс один раз; lock не даёт параллельным HTTP-запросам перепутать JSONL-ответы.
class SearchWorker:
    """Один C++-процесс и одна загрузка индекса на всё время демонстрации."""
    def __init__(self, executable, index):
        self.executable, self.index = executable, index
        self.lock = threading.Lock()
        self.process = None

    def close(self):
        if self.process is not None:
            self.process.kill() if self.process.poll() is None else None
            self.process.wait()
            self.process.stdin.close()
            self.process.stdout.close()
            self.process = None

    # Строка протокола: режим TAB offset TAB query. Таймаут ограничивает ожидание ответа зависшего процесса.
    def search(self, query, ranked, offset):
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                self.close()
                command = [str(self.executable), "search", "--index", str(self.index), "--protocol", "--limit", str(PAGE_SIZE)]
                settings = self.index.with_suffix(".search.json")
                if settings.is_file():
                    config = json.loads(settings.read_text(encoding="utf-8"))
                    for key in ("lemmas", "positions", "texts", "text-offsets", "skip-index"):
                        if key in config:
                            command.extend(["--" + key, str(ROOT / config[key])])
                self.process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
                self.responses = queue.Queue()
                def read_output(process, responses):
                    for line in process.stdout:
                        responses.put(line)
                    responses.put(None)
                threading.Thread(target=read_output, args=(self.process, self.responses), daemon=True).start()
            # Один HTTP-запрос всегда превращается в одну строку протокола.
            body = query.replace("\r", " ").replace("\n", " ").replace("\t", " ")
            self.process.stdin.write(f"{'ranked' if ranked else 'boolean'}\t{offset}\t{body}\n")
            self.process.stdin.flush()
            try:
                response = self.responses.get(timeout=60)
            except queue.Empty:
                self.close()
                raise TimeoutError("Search timeout")
            if response is None:
                self.close()
                raise RuntimeError("Search process stopped")
            return json.loads(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    advanced = ROOT / "data/advanced/index.ir"
    parser.add_argument("--index", type=Path, default=advanced if advanced.with_suffix('.search.json').is_file() else ROOT / "data/lab11_index/index.ir")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    server = create_server(args.index, args.host, args.port)
    print(f"Поиск: http://{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
