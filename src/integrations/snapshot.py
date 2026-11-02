"""Адаптер снимков страниц (Playwright). Снимок и копия HTML — часть доказательства."""

import logging

import requests
from django.conf import settings

from core.ports import Snapshot

log = logging.getLogger(__name__)


def _playwright_proxy():
    """CRAWLER_PROXY в формате Playwright: логин и пароль передаются отдельно."""
    if not settings.CRAWLER_PROXY:
        return None
    from urllib.parse import urlparse

    u = urlparse(settings.CRAWLER_PROXY)
    scheme = "socks5" if u.scheme.startswith("socks5") else u.scheme
    proxy = {"server": f"{scheme}://{u.hostname}:{u.port}" if u.port else f"{scheme}://{u.hostname}"}
    if u.username:
        proxy.update(username=u.username, password=u.password or "")
    return proxy


class PlaywrightSnapshotter:
    def capture(self, url):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return HtmlOnlySnapshotter().capture(url)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(args=["--no-sandbox"], proxy=_playwright_proxy())
                page = browser.new_page(viewport={"width": 1280, "height": 900}, locale="ru-RU",
                                        user_agent=settings.CRAWLER_USER_AGENT)
                page.goto(url, wait_until="networkidle", timeout=45000)
                png = page.screenshot(full_page=True)
                html = page.content()
                browser.close()
                return Snapshot(png=png, html=html)
        except Exception as exc:
            log.warning("Снимок %s не получен: %s", url, exc)
            return HtmlOnlySnapshotter().capture(url)


class HtmlOnlySnapshotter:
    def capture(self, url):
        try:
            from partners.infrastructure.crawlers import crawler_session

            resp = crawler_session(retries=1).get(url, timeout=(10, 25))
            resp.raise_for_status()
            return Snapshot(png=None, html=resp.text)
        except requests.RequestException as exc:
            log.warning("Копия %s не получена: %s", url, exc)
            return None


def get_snapshotter():
    if settings.SNAPSHOT_BACKEND == "playwright":
        return PlaywrightSnapshotter()
    return HtmlOnlySnapshotter()
