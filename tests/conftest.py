import pytest


from core.ports import Snapshot


@pytest.fixture(autouse=True)
def _env(settings, monkeypatch, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    from django.core.cache import cache

    cache.clear()
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.LLM_BACKEND = "fake"
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.MAIL_PAUSE_SECONDS = 0
    from config.celery import app

    app.conf.task_always_eager = True
    # колбэки после фиксации транзакции выполняем сразу (в тестах транзакция не фиксируется)
    monkeypatch.setattr("django.db.transaction.on_commit", lambda fn, using=None, robust=False: fn())

    class FakeSnap:
        def capture(self, url):
            return Snapshot(png=b"\x89PNG fake", html=f"<html>{url}</html>")

    monkeypatch.setattr("publishing.evidence.get_snapshotter", lambda: FakeSnap())


@pytest.fixture
def org(db):
    from accounts.models import Organization

    return Organization.objects.create(code="istok", name="Технопарк «Исток»")


@pytest.fixture
def other_org(db):
    from accounts.models import Organization

    return Organization.objects.create(code="other", name="Другая организация")


def _user(django_user_model, org, username, role, email):
    from accounts.models import Membership

    u = django_user_model.objects.create_user(username=username, password="pass12345", email=email)
    Membership.objects.create(user=u, organization=org, role=role)
    return u


@pytest.fixture
def admin_user(django_user_model, org):
    return _user(django_user_model, org, "admin", "admin", "admin@example.com")


@pytest.fixture
def director(django_user_model, org):
    return _user(django_user_model, org, "director", "director", "director@example.com")


@pytest.fixture
def submission(org):
    from django.utils import timezone

    from submissions.models import Submission

    return Submission.objects.create(
        organization=org, title="Турнир по робототехнике",
        text="В технопарке прошёл турнир по робототехнике среди школьников. Участвовали 12 команд из "
             "Фрязино и Щёлково. Победила команда школы №16, второе место — Лицей №14.",
        event_date="11 октября 2026", place="Технопарк, Фрязино", author_name="Иван Петров",
        author_email="author@example.com", consent_at=timezone.now(), questions_finished=True)
