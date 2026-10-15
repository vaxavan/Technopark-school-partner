import json

import pytest

from core.ports import PlatformError
from publishing.platforms import TelegramPlatform


class Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body


@pytest.fixture
def calls(monkeypatch):
    log = []

    def fake_post(url, data=None, files=None, timeout=None):
        method = url.rsplit("/", 1)[-1]
        log.append((method, dict(data or {}), sorted((files or {}).keys())))
        chat = {"id": -1001234567890, "type": "channel", "username": "istok_test"}
        if method == "sendMediaGroup":
            return Resp({"ok": True, "result": [{"message_id": 10, "chat": chat}, {"message_id": 11, "chat": chat}]})
        return Resp({"ok": True, "result": {"message_id": 7 + len(log), "chat": chat}})

    monkeypatch.setattr("publishing.platforms.requests.post", fake_post)
    return log


def test_text_only(calls):
    r = TelegramPlatform("@istok_test", "T").publish("Короткий пост", [])
    assert calls == [("sendMessage", {"chat_id": "@istok_test", "text": "Короткий пост"}, [])]
    assert r.url == "https://t.me/istok_test/8"


def test_photo_with_caption(calls, tmp_path):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"jpg")
    TelegramPlatform("@istok_test", "T").publish("Подпись", [str(img)])
    assert calls[0][0] == "sendPhoto" and calls[0][1]["caption"] == "Подпись" and len(calls) == 1


def test_long_text_goes_after_album(calls, tmp_path):
    paths = []
    for i in range(3):
        p = tmp_path / f"{i}.jpg"
        p.write_bytes(b"jpg")
        paths.append(str(p))
    TelegramPlatform("-1001234567890", "T").publish("x" * 2000, paths)
    assert [c[0] for c in calls] == ["sendMediaGroup", "sendMessage"]
    media = json.loads(calls[0][1]["media"])
    assert len(media) == 3 and "caption" not in media[0] and calls[0][2] == ["p0", "p1", "p2"]


def test_private_channel_url():
    p = TelegramPlatform("-1001234567890", "T")
    assert p.post_url({"message_id": 5, "chat": {"id": -1001234567890}}) == "https://t.me/c/1234567890/5"


def test_error_hides_token(monkeypatch):
    monkeypatch.setattr("publishing.platforms.requests.post",
                        lambda *a, **k: Resp({"ok": False, "error_code": 403,
                                              "description": "Forbidden: bot is not a member of the channel chat"}, 403))
    with pytest.raises(PlatformError) as e:
        TelegramPlatform("@x", "SECRET-TOKEN").publish("t", [])
    assert "not a member" in str(e.value) and "SECRET" not in str(e.value)


@pytest.mark.django_db
def test_telegram_used_when_configured(org, admin_user, submission, calls):
    from materials import services
    from materials.models import Channel
    from publishing.models import Publication
    from publishing.services import save_platform_account
    from publishing.tasks import publish_due

    save_platform_account(org, "telegram", target_id="@istok_test", token="123:ABC")
    m = services.accept_submission(submission, admin_user, [Channel.TELEGRAM])
    v = m.current_variant(Channel.TELEGRAM)
    assert len(v.text) <= 1000
    services.approve(m, admin_user, "none")
    assert publish_due() == 1
    pub = Publication.objects.get()
    assert pub.status == "published" and not pub.demo and pub.url.startswith("https://t.me/istok_test/")


def test_rejected_photo_falls_back_to_text(monkeypatch, tmp_path):
    """Telegram не принял фото (слишком большое, HEIC) — пост выходит хотя бы текстом."""
    img = tmp_path / "big.heic"
    img.write_bytes(b"heic")
    log = []

    def fake_post(url, data=None, files=None, timeout=None):
        method = url.rsplit("/", 1)[-1]
        log.append(method)
        if method == "sendPhoto":
            return Resp({"ok": False, "error_code": 400, "description": "Bad Request: IMAGE_PROCESS_FAILED"}, 400)
        return Resp({"ok": True, "result": {"message_id": 3, "chat": {"username": "istok_test"}}})

    monkeypatch.setattr("publishing.platforms.requests.post", fake_post)
    r = TelegramPlatform("@istok_test", "T").publish("Текст", [str(img)])
    assert log == ["sendPhoto", "sendMessage"] and r.url == "https://t.me/istok_test/3"
