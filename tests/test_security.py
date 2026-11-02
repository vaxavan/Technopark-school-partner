"""Проверки безопасности, добавленные по итогам тестирования 7 семестра:
защита входа от подбора пароля и разбор XML с сайтов школ без внешних сущностей."""
import pytest

from partners.domain.entities import Source, SourceKind
from partners.infrastructure.crawlers import RssCrawler, SitemapCrawler, SourceUnavailable

BOMB = """<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>&lol2;</loc></url></urlset>"""


def _login(client, email, password):
    return client.post("/login/", {"username": email, "password": password})


@pytest.mark.django_db
def test_web_login_locks_after_five_failures(client, admin_user):
    for _ in range(5):
        r = _login(client, "admin@example.com", "wrong")
        assert "Неверный e-mail или пароль" in r.content.decode()
    r = _login(client, "admin@example.com", "pass12345")       # даже верный пароль не пускает
    assert r.status_code == 200 and "Слишком много неудачных попыток" in r.content.decode()
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_successful_login_resets_counter(client, admin_user):
    for _ in range(4):
        _login(client, "admin@example.com", "wrong")
    assert _login(client, "admin@example.com", "pass12345").status_code == 302
    client.logout()
    for _ in range(4):
        _login(client, "admin@example.com", "wrong")
    assert _login(client, "admin@example.com", "pass12345").status_code == 302


@pytest.mark.django_db
def test_api_login_locks_too(client, admin_user):
    for _ in range(5):
        assert client.post("/api/v1/auth/login/", {"email": "admin@example.com", "password": "x"},
                           content_type="application/json").status_code == 400
    r = client.post("/api/v1/auth/login/", {"email": "admin@example.com", "password": "pass12345"},
                    content_type="application/json")
    assert r.status_code == 429


class OneUrl:
    def __init__(self, body):
        self.body = body

    def get(self, url):
        return self.body


def test_sitemap_with_entities_is_rejected():
    with pytest.raises(SourceUnavailable):
        SitemapCrawler(OneUrl(BOMB)).fetch(Source(1, SourceKind.SITEMAP, "https://s.example/sitemap.xml"))


def test_rss_with_entities_is_rejected():
    with pytest.raises(SourceUnavailable):
        RssCrawler(OneUrl(BOMB.replace("urlset", "rss"))).fetch(Source(1, SourceKind.RSS, "https://s.example/rss"))
