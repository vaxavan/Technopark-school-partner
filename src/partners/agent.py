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
