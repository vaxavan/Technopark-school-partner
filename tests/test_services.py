from datetime import timedelta

import pytest
from django.utils import timezone

from core.ports import LLMError
from materials import services
from materials.generation import check_variant, generate_for_channel
from materials.models import Channel
from publishing import services as pub_services
from publishing.models import Publication
from submissions.clarify import ask_model, next_round


class ScriptedLLM:
    name = "scripted"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def complete(self, system, user, **kw):
        self.calls += 1
        r = self.replies.pop(0) if self.replies else self.last
        self.last = r
        if isinstance(r, Exception):
            raise r
        return r


class Org:
    name = "Технопарк"
    style_guide = ""


SOURCE = "Турнир прошёл 11 октября. Участвовали 12 команд. Победила школа №16."


def test_check_flags_invented_facts_and_length():
    issues = check_variant(Channel.VK, "Турнир собрал 300 участников 5 ноября. " * 2, SOURCE)
    joined = " ".join(issues)
    assert "300" in joined and "5 ноября" in joined and "меньше" in joined


def test_generation_retries_then_gives_up_with_issues():
    good = ("Турнир по робототехнике прошёл 11 октября. Участвовали 12 команд из разных школ, "
            "борьба была упорной, а победила команда школы №16. Поздравляем ребят и наставников и ждём "
            "новых соревнований в технопарке! #технопарк #роботы")
    llm = ScriptedLLM(["Пришли 500 человек " * 3, good])
    text, issues, attempts = generate_for_channel(Org(), Channel.VK, SOURCE, llm=llm)
    assert attempts == 2 and not issues and text == good

    llm = ScriptedLLM(["Пришли 500 человек"])
    text, issues, attempts = generate_for_channel(Org(), Channel.VK, SOURCE, llm=llm)
    assert attempts == 3 and issues and llm.calls == 3


@pytest.mark.django_db
def test_clarify_schema_errors_and_timeout(submission):
    assert ask_model(submission, ScriptedLLM(["не json", "{\"bad\": 1}"])) is None
    assert ask_model(submission, ScriptedLLM([LLMError("timeout")])) is None
    ok = ask_model(submission, ScriptedLLM(['```json\n{"complete": false, "missing": ["итог"], "questions": '
                                            '[{"id": "x", "type": "choice", "text": "Сколько?", "options": ["1"]}]}\n```']))
    assert ok.questions[0].type == "text" and ok.questions[0].id == "q1"  # choice с 1 вариантом → text


@pytest.mark.django_db
def test_clarify_max_two_rounds(submission):
    submission.questions_finished = False
    submission.save()
    reply = '{"complete": false, "missing": ["x"], "questions": [{"id": "q1", "type": "text", "text": "Где?"}]}'
    for i in range(2):
        rnd = next_round(submission, ScriptedLLM([reply]))
        assert rnd is not None and rnd.number == i + 1
    assert next_round(submission, ScriptedLLM([reply])) is None   # третьего раунда нет
    submission.refresh_from_db()
    assert submission.questions_finished


@pytest.mark.django_db
def test_llm_down_gives_manual_drafts(submission, admin_user, monkeypatch):
    monkeypatch.setattr("materials.generation.get_llm", lambda org=None: ScriptedLLM([LLMError("нет связи")]))
    material = services.accept_submission(submission, admin_user, [Channel.VK, Channel.MAX])
    material.refresh_from_db()
    assert material.status == "on_approval" and "нет связи" in material.generation_error
    v = material.current_variant(Channel.VK)
    assert v.source == "manual" and "вручную" in v.checks[0]


@pytest.mark.django_db
def test_publication_not_repeated_after_crash(submission, admin_user, director):
    material = services.accept_submission(submission, admin_user, [Channel.VK])
    services.approve(material, director, "director")
    pub = Publication.objects.get(material=material)
    # имитируем падение воркера посередине публикации
    Publication.objects.filter(pk=pub.pk).update(status="publishing", started_at=timezone.now() - timedelta(minutes=30))
    assert pub_services.publish_one(pub.pk) is None          # «публикуется» — повторно не берётся
    pub_services.release_stuck()
    pub.refresh_from_db()
    assert pub.status == "needs_check"
    material.refresh_from_db()
    assert material.status == "publish_error"
    assert pub_services.due_publications() == []              # и в очередь не возвращается сама


@pytest.mark.django_db
def test_failed_platform_then_retry(submission, admin_user, monkeypatch):
    from core.ports import PlatformError

    class Broken:
        demo = False

        def publish(self, text, images):
            raise PlatformError("ВКонтакте: 5 User authorization failed")

    monkeypatch.setattr("publishing.services.platform_for", lambda org, ch: Broken())
    material = services.accept_submission(submission, admin_user, [Channel.VK])
    services.approve(material, admin_user, "none")
    from publishing.tasks import publish_due

    publish_due()
    material.refresh_from_db()
    assert material.status == "publish_error"
    assert "authorization" in material.publications.get().error
    services.retry_publication(material, admin_user)
    material.refresh_from_db()
    assert material.status == "scheduled"


@pytest.mark.django_db
def test_import_schools_file(org):
    from pathlib import Path

    from partners.interfaces.importer import import_schools

    path = Path(__file__).resolve().parent.parent / "data" / "schools.xlsx"
    with open(path, "rb") as fh:
        created, updated = import_schools(org, fh)
    assert created == 18
    s5 = org.schools.get(number=4)
    assert not s5.active and "объедин" in s5.notes
    s16 = org.schools.get(number=13)
    src = s16.sources.get()
    assert src.kind == "html" and src.url.endswith("/activity/proforientation/miria")
    ucoz = org.schools.get(number=18).sources.values_list("kind", flat=True)
    assert set(ucoz) == {"sitemap", "rss"}
    with open(path, "rb") as fh:
        assert import_schools(org, fh) == (0, 18)


@pytest.mark.django_db
def test_token_encrypted_at_rest(org):
    from django.db import connection

    from publishing.models import PlatformAccount

    PlatformAccount.objects.create(organization=org, channel="vk", target_id="123", token="vk1.a.SECRET")
    with connection.cursor() as c:
        c.execute("select token from publishing_platformaccount")
        raw = c.fetchone()[0]
    assert raw.startswith("enc:") and "SECRET" not in raw
    assert PlatformAccount.objects.get().token == "vk1.a.SECRET"


@pytest.mark.django_db
def test_llm_key_from_settings_overrides_env(org, settings):
    from core.models import LLMAccount
    from integrations.llm import FakeLLM, YandexGPT, get_llm, llm_mode
    from publishing.services import save_llm_account

    settings.LLM_BACKEND = "yandex"
    settings.YANDEX_API_KEY = settings.YANDEX_FOLDER_ID = ""
    assert isinstance(get_llm(org), FakeLLM) and llm_mode(org) == "demo"

    settings.YANDEX_API_KEY, settings.YANDEX_FOLDER_ID = "env-key", "b1genv"
    assert llm_mode(org) == "env" and get_llm(org).folder_id == "b1genv"

    save_llm_account(org, api_key="AQVN-org-key", folder_id="b1gorg", model="yandexgpt/rc")
    llm = get_llm(org)
    assert isinstance(llm, YandexGPT) and llm.api_key == "AQVN-org-key" and llm.model == "yandexgpt/rc"
    assert llm.model_lite == settings.YANDEX_MODEL_LITE and llm_mode(org) == "settings"
    save_llm_account(org, api_key="", folder_id="b1gorg")          # пустой ключ — не меняется
    assert LLMAccount.objects.get().api_key == "AQVN-org-key"

    settings.LLM_BACKEND = "fake"                                   # тесты и демо — всегда без сети
    assert isinstance(get_llm(org), FakeLLM)


@pytest.mark.django_db
def test_llm_settings_page_and_api(client, org, admin_user):
    client.force_login(admin_user)
    r = client.post("/app/settings/", {"form": "llm", "llm-api_key": "AQVN-x", "llm-folder_id": "b1gx"})
    assert r.status_code == 302
    page = client.get("/app/settings/").content.decode()
    assert "Ключ сохранён" in page and "AQVN-x" not in page
    data = client.get("/api/v1/settings/").json()["llm"]
    assert data == {"folder_id": "b1gx", "model": "", "model_lite": "", "has_key": True}
    r = client.put("/api/v1/settings/llm/", {"clear": True}, content_type="application/json")
    assert r.json()["llm"]["has_key"] is False
