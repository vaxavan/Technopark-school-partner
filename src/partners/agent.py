"""Агент обхода сайтов школ.

Запускается на компьютере, с которого сайты школ открываются (например, в офисе технопарка),
если сервер они не пускают. Агент:
  1) спрашивает у сервера, какие источники проверить (GET /api/v1/agent/tasks/);
  2) сам скачивает страницы теми же обходчиками, что и сервер;
  3) отправляет найденные публикации по каждой школе (POST /api/v1/agent/results/);
  4) сообщает итог (POST /api/v1/agent/done/).
Сверку текстов, решения и напоминания делает сервер. Модели Django здесь не используются,
поэтому агент работает и без базы данных — см. scripts/crawl_agent.py."""

import time

import requests

from partners.domain.entities import Source, SourceKind
from partners.infrastructure.crawlers import CompositeCrawler

MAX_TEXT = 12000      # знаков на одну публикацию — для сверки этого достаточно
MAX_ITEMS = 25


class AgentError(Exception):
    pass


class AgentClient:
    def __init__(self, server, token, timeout=60):
        self.base = server.rstrip("/") + "/api/v1/agent/"
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})
        self.timeout = timeout

    def _call(self, method, path, **kw):
        try:
            resp = self.session.request(method, self.base + path, timeout=self.timeout, **kw)
        except requests.RequestException as exc:
            raise AgentError(f"сервер недоступен: {exc}") from exc
        if resp.status_code in (401, 403):
            raise AgentError("сервер не принял ключ агента — создайте новый в «Настройках»")
        if resp.status_code >= 400:
            raise AgentError(f"сервер ответил {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def tasks(self):
        return self._call("GET", "tasks/")

    def send(self, school_id, sources):
        return self._call("POST", "results/", json={"school_id": school_id, "sources": sources})

    def done(self, summary):
        return self._call("POST", "done/", json=summary)

    def search_result(self, data, files):
        return self._call("POST", "search-result/", data=data, files=files)


def _item(f):
    return {"url": f.url, "title": f.title[:500], "text": f.text[:MAX_TEXT], "source_kind": f.source_kind.value,
            "published_at": f.published_at.isoformat() if f.published_at else None}


def run_once(server, token, log=print, crawler=None, client=None):
    """Один проход. Возвращает итог: сколько школ и источников проверено, что подтвердил сервер."""
    client = client or AgentClient(server, token)
    crawler = crawler or CompositeCrawler()
    data = client.tasks()
    by_school = {}
    for s in data.get("sources", []):
        by_school.setdefault(s["school_id"], {"name": s["school"], "sources": []})["sources"].append(s)
    log(f"Организация: {data.get('organization', '?')}. Школ к проверке: {len(by_school)}")
    total = {"schools": 0, "sources_ok": 0, "sources_failed": 0, "confirmed": 0, "disputed": 0, "errors": []}
    for school_id, info in by_school.items():
        results = []
        for s in info["sources"]:
            src = Source(id=s["id"], kind=SourceKind(s["kind"]), url=s["url"], link_prefix=s.get("link_prefix", ""))
            started = time.monotonic()
            try:
                found = crawler.fetch(src)[:MAX_ITEMS]
                results.append({"id": s["id"], "ok": True, "items": [_item(f) for f in found]})
                total["sources_ok"] += 1
                log(f"  {info['name'][:60]}: {s['url']} — найдено {len(found)} ({time.monotonic() - started:.0f} с)")
            except Exception as exc:  # один сайт не должен останавливать обход
                results.append({"id": s["id"], "ok": False, "error": str(exc)[:1000]})
                total["sources_failed"] += 1
                total["errors"].append(f"{info['name'][:60]}: {exc}"[:300])
                log(f"  {info['name'][:60]}: {s['url']} — ошибка: {exc}")
        report = client.send(school_id, results)
        total["schools"] += 1
        total["confirmed"] += report.get("confirmed", 0)
        total["disputed"] += report.get("disputed", 0)
    total["errors"] = total["errors"][:30]
    searches = data.get("searches") or []
    if searches:
        total["searches"] = run_searches(client, searches, log)
    client.done(total)
    log(f"Готово: школ {total['schools']}, источников открылось {total['sources_ok']}, "
        f"не открылось {total['sources_failed']}; подтверждено {total['confirmed']}, "
        f"требует решения {total['disputed']}")
    return total


def run_forever(server, token, every_hours, log=print):
    while True:
        try:
            run_once(server, token, log)
        except AgentError as exc:
            log(f"Ошибка: {exc}")
        log(f"Следующий обход через {every_hours} ч.")
        time.sleep(every_hours * 3600)


# ---------- поиск по теме («битва роботов») по сайтам школ

class LocalSnapshotter:
    """Снимок страницы на компьютере агента. Нужен Playwright:
    pip install playwright && python -m playwright install chromium. Без него — только копия HTML."""

    def __init__(self):
        self.pw = self.browser = None
        try:
            from playwright.sync_api import sync_playwright

            self.pw = sync_playwright().start()
            self.browser = self.pw.chromium.launch()
        except Exception:  # Playwright не установлен — снимков не будет, копии страниц будут
            self.close()

    def capture(self, url):
        if self.browser is None:
            return None
        try:
            page = self.browser.new_page(viewport={"width": 1280, "height": 900}, locale="ru-RU")
            page.goto(url, wait_until="networkidle", timeout=45000)
            png = page.screenshot(full_page=True, type="jpeg", quality=75)
            page.close()
            return png
        except Exception:
            return None

    def close(self):
        for x in (self.browser, self.pw):
            try:
                if x is not None:
                    (x.close if x is self.browser else x.stop)()
            except Exception:
                pass
        self.browser = self.pw = None


def run_searches(client, searches, log=print, explorer_factory=None, snapshotter=None):
    import json
    from datetime import datetime

    from partners.domain.topic import TopicQuery
    from partners.infrastructure.explorer import SiteExplorer

    log(f"Поиск по теме: школ к проверке {len(searches)}")
    snap = snapshotter if snapshotter is not None else (
        LocalSnapshotter() if any(t.get("screenshots") for t in searches) else None)
    found = 0
    try:
        for task in searches:
            query = TopicQuery(task["query"])
            since = datetime.fromisoformat(task["since"] + "T00:00:00+00:00") if task.get("since") else None
            sources = [Source(id=x["id"], kind=SourceKind(x["kind"]), url=x["url"], link_prefix=x.get("link_prefix", ""))
                       for x in task.get("sources", [])]
            explorer = explorer_factory() if explorer_factory else SiteExplorer()
            started = time.monotonic()
            try:
                report = explorer.explore(task.get("site", ""), sources, query, since)
            except Exception as exc:
                client.search_result({"result_id": task["result_id"], "status": "unreachable",
                                      "error": str(exc)[:1000], "hits": "[]"}, files={})
                log(f"  {task['school'][:60]}: ошибка: {exc}")
                continue
            hits, files = [], {}
            for page in report.pages:
                m = query.match(page.title, page.text)
                if m is None:
                    continue
                i = len(hits)
                hits.append({"url": page.url, "title": page.title, "snippet": m.snippet, "score": m.score,
                             "published_at": page.published_at.isoformat() if page.published_at else None})
                if page.html:
                    files[f"html_{i}"] = (f"{i}.html", page.html.encode("utf-8"), "text/html")
                shot = snap.capture(page.url) if (snap is not None and task.get("screenshots")) else None
                if shot:
                    files[f"shot_{i}"] = (f"{i}.jpg", shot, "image/jpeg")
            status = "found" if hits else ("unreachable" if report.unreachable or
                                           (not report.pages and report.errors) else "none")
            client.search_result({"result_id": task["result_id"], "status": status, "pages_read": report.read,
                                  "error": "; ".join(report.errors[:5])[:1000] if status != "found" else "",
                                  "hits": json.dumps(hits, ensure_ascii=False)}, files=files)
            found += bool(hits)
            log(f"  {task['school'][:60]}: найдено {len(hits)}, прочитано страниц {report.read} "
                f"({time.monotonic() - started:.0f} с)")
    finally:
        if snap is not None and hasattr(snap, "close"):
            snap.close()
    return {"schools": len(searches), "found": found}
