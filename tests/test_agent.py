"""Агент обхода: сайты школ проверяет компьютер, который они пускают, а сверку делает сервер."""

import pytest

from materials.models import Channel, Material, TextVariant
from partners.agent import run_once
from partners.domain.entities import FoundPublication, SourceKind
from partners.models import CrawlAgent, PlacementRecord, School, Source

TEXT = ("В технопарке прошёл турнир по робототехнике. Участвовали двенадцать команд из Фрязино и Щёлково. "
        "Победила команда школы номер шестнадцать, второе место занял лицей номер четырнадцать.")


class DjangoClientAdapter:
    """Агент разговаривает с сервером через тестовый клиент Django вместо сети."""

    def __init__(self, client, token):
        self.c, self.h = client, {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def tasks(self):
        return self.c.get("/api/v1/agent/tasks/", **self.h).json()

    def send(self, school_id, sources):
        r = self.c.post("/api/v1/agent/results/", {"school_id": school_id, "sources": sources},
                        content_type="application/json", **self.h)
        assert r.status_code == 200, r.content
        return r.json()

    def done(self, summary):
        return self.c.post("/api/v1/agent/done/", summary, content_type="application/json", **self.h).json()


class LocalCrawler:
    def fetch(self, source):
        if "blocked" in source.url:
            raise ConnectionError("сайт не отвечает")
        return [FoundPublication(url=source.url + "/news/1", title="Турнир", text="Новости. " + TEXT,
                                 source_kind=SourceKind.HTML)]


@pytest.fixture
def placed(org, submission):
    material = Material.objects.create(organization=org, submission=submission, title="Турнир", status="published")
    TextVariant.objects.create(material=material, channel=Channel.PARTNERS, text=TEXT, version=1)
    ok = School.objects.create(organization=org, name="СОШ №16", email="s16@example.com")
    Source.objects.create(school=ok, kind="html", url="https://s16.example/news")
    Source.objects.create(school=ok, kind="vk", url="https://vk.com/s16")   # ВКонтакте сервер читает сам
    bad = School.objects.create(organization=org, name="Лицей №14", email="l14@example.com")
    Source.objects.create(school=bad, kind="html", url="https://blocked.example/news")
    for s in (ok, bad):
        PlacementRecord.objects.create(organization=org, material=material, school=s)
    return material


@pytest.mark.django_db
def test_agent_crawls_and_server_decides(client, org, other_org, placed):
    token = CrawlAgent.issue(org)
    tasks = DjangoClientAdapter(client, token).tasks()
    assert {s["kind"] for s in tasks["sources"]} == {"html"}               # vk агенту не отдаём
    total = run_once("", token, log=lambda *_: None, crawler=LocalCrawler(), client=DjangoClientAdapter(client, token))
    assert total["confirmed"] == 1 and total["sources_failed"] == 1
    statuses = dict(PlacementRecord.objects.values_list("school__name", "status"))
    assert statuses == {"СОШ №16": "confirmed", "Лицей №14": "expected"}  # недоступный сайт — не «не найдено»
    agent = CrawlAgent.objects.get(organization=org)
    assert agent.is_active and "подтверждено 1" in agent.last_summary

    # чужая организация и неверный ключ
    other_school = School.objects.create(organization=other_org, name="Чужая")
    r = client.post("/api/v1/agent/results/", {"school_id": other_school.id, "sources": []},
                    content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert r.status_code == 400
    assert client.get("/api/v1/agent/tasks/", HTTP_AUTHORIZATION="Bearer agt_wrong").status_code in (401, 403)

    # пока агент работает, ночной обход сервера эту организацию пропускает
    from partners.tasks import crawl_all

    assert crawl_all() == 0


@pytest.mark.django_db
def test_agent_key_from_settings_page(client, org, admin_user):
    client.force_login(admin_user)
    client.post("/app/settings/", {"form": "agent", "action": "issue"})
    page = client.get("/app/settings/").content.decode()
    import re

    token = re.search(r"agt_[\w-]+", page).group(0)
    assert token.startswith("agt_") and CrawlAgent.by_token(token).organization == org
    assert token not in client.get("/app/settings/").content.decode()        # ключ показывается один раз
    assert CrawlAgent.objects.get().token_hash != token                       # в базе — только отпечаток


def test_user_agent_is_latin1_and_beat_tasks_registered(settings):
    """Кириллица в заголовке ломала каждый запрос к сайтам школ; задачи расписания должны существовать."""
    from config.celery import app

    settings.CRAWLER_USER_AGENT.encode("latin-1")
    app.loader.import_default_modules()
    for entry in settings.CELERY_BEAT_SCHEDULE.values():
        assert entry["task"] in app.tasks, entry["task"]
