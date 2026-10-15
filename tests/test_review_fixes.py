"""Исправления по итогам проверки логики: каждая проверка — сценарий, который раньше ломался."""

from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from core.events import deliver_pending, record
from core.models import Event
from materials import services
from materials.generation import clean_model_text
from materials.models import Channel, Material
from partners.infrastructure.crawlers import is_public_url
from publishing import services as pub_services
from publishing.models import Publication


def test_hashtags_survive_cleanup():
    assert clean_model_text("Текст\n#робототехника #технопарк") == "Текст\n#робототехника #технопарк"
    assert clean_model_text("# Заголовок\nТекст") == "Заголовок\nТекст"


def test_school_link_must_be_public():
    assert not is_public_url("http://127.0.0.1:8000/admin/")
    assert not is_public_url("http://10.0.0.5/")
    assert not is_public_url("file:///etc/passwd")
    assert is_public_url("https://8.8.8.8/news/1")


@pytest.mark.django_db
def test_any_publish_error_is_retryable_and_channel_can_be_dropped(submission, admin_user, monkeypatch):
    class Broken:
        demo = False

        def publish(self, text, images):
            raise KeyError("message_id")            # не PlatformError — раньше публикация зависала

    monkeypatch.setattr("publishing.services.platform_for", lambda org, ch: Broken())
    material = services.accept_submission(submission, admin_user, [Channel.VK, Channel.PARTNERS])
    services.approve(material, admin_user, "none")
    from publishing.tasks import publish_due

    publish_due()
    material.refresh_from_db()
    pub = material.publications.get()
    assert pub.status == "failed" and material.status == "publish_error"

    # «проверить вручную» тоже можно повторить
    Publication.objects.filter(pk=pub.pk).update(status="needs_check")
    services.retry_publication(material, admin_user)
    pub.refresh_from_db()
    assert pub.status == "scheduled"

    # площадку убрали — публиковать больше нечего, материал вышел и ушёл школам
    Publication.objects.filter(pk=pub.pk).update(status="failed")
    Material.objects.filter(pk=material.pk).update(status="publish_error")
    services.drop_channel(material, pub.pk, admin_user)
    material.refresh_from_db()
    assert material.status == "published" and Channel.VK not in material.channels


@pytest.mark.django_db
def test_partner_only_material_waits_for_its_date(submission, admin_user, monkeypatch):
    sent = []
    monkeypatch.setattr("partners.tasks.dispatch_material.delay", lambda mid: sent.append(mid))
    material = services.accept_submission(submission, admin_user, [Channel.PARTNERS])
    services.approve(material, admin_user, "none", scheduled_at=timezone.now() + timedelta(days=2))
    material.refresh_from_db()
    assert material.status == "scheduled" and sent == []
    pub_services.release_partner_only_due(now=timezone.now() + timedelta(days=3))
    material.refresh_from_db()
    assert material.status == "published" and sent == [material.pk]


@pytest.mark.django_db
def test_material_never_stuck_in_preparing(submission, admin_user, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("worker killed")

    monkeypatch.setattr("materials.services.generate_for_channel", boom)
    material = services.accept_submission(submission, admin_user, [Channel.VK, Channel.TELEGRAM])
    material.refresh_from_db()
    assert material.status == "on_approval"
    assert material.current_variant(Channel.VK).source == "manual"


@pytest.mark.django_db
def test_approve_requires_text_for_every_channel(submission, admin_user):
    material = services.accept_submission(submission, admin_user, [Channel.VK])
    Material.objects.filter(pk=material.pk).update(channels=[Channel.VK, Channel.MAX])
    material.refresh_from_db()
    with pytest.raises(Exception, match="Max"):
        services.approve(material, admin_user, "none")


@pytest.mark.django_db
def test_notification_resumes_without_duplicates(org, admin_user, submission, monkeypatch):
    """Почта упала на втором письме: при повторе первое не уходит второй раз; редакция — первой."""
    from integrations import mail as mail_mod

    real, calls = mail_mod.send_one, []

    def flaky(to, *a, **kw):
        calls.append(to)
        if to == submission.author_email and calls.count(to) == 1:
            raise OSError("smtp down")
        return real(to, *a, **kw)

    monkeypatch.setattr("core.notifications.send_one", flaky)
    record(org, "submission.received", obj=submission, submission_id=submission.pk)
    deliver_pending()
    ev = Event.objects.get(kind="submission.received")
    assert ev.processed_at is None and ev.retry_at is not None        # следующая попытка — с паузой
    assert calls[0] == "admin@example.com"                              # сначала редакции
    Event.objects.filter(pk=ev.pk).update(retry_at=timezone.now())
    deliver_pending()
    assert calls.count("admin@example.com") == 1                        # не продублировали
    assert sorted(m.to[0] for m in mail.outbox) == ["admin@example.com", submission.author_email]


@pytest.mark.django_db
def test_old_page_cannot_answer_next_round(org, monkeypatch):
    from submissions import services as sub_services

    class Asks:
        def complete(self, system, user, **kw):
            return '{"complete": false, "missing": [], "questions": [{"id": "q1", "type": "text", "text": "Где?"}]}'

    monkeypatch.setattr("submissions.clarify.get_llm", lambda org=None: Asks())
    sub, _ = sub_services.create_submission(org, {"title": "Т", "text": "Т", "author_name": "И",
                                                  "author_email": "i@example.com"})
    sub_services.prepare_round(sub)
    sub_services.answer_round(sub, {"q1": "Фрязино"}, round_number=1)
    sub_services.prepare_round(sub)                                          # второй раунд
    sub_services.answer_round(sub, {"q1": "старый ответ"}, round_number=1)   # кнопка «Назад» + отправить
    assert sub_services.open_round(sub).number == 2 and not sub_services.open_round(sub).answers
