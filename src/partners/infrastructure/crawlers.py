"""Обходчики ресурсов школ: карта сайта, RSS, ВКонтакте, разбор раздела сайта.
Перенесено и обобщено из прототипа (legacy/TechnoparkTest/main/school_parser.py)."""

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from django.conf import settings

from partners.domain.entities import FoundPublication, Source, SourceKind

log = logging.getLogger(__name__)

MAX_PAGES_PER_SOURCE = 25
FRESH_DAYS = 60
NEWS_HINTS = ("news", "novost", "post", "event", "meropri", "article", "press", "blog")
TEXT_SELECTORS = ["div.user_content", "article", "div.news-detail", "div.news_detail", "div.entry-content",
                  "div.post-content", "div.content", "main"]


class SourceUnavailable(Exception):
    pass


class HttpClient:
    def __init__(self, pause=0.5, timeout=20):
        self.pause, self.timeout = pause, timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = settings.CRAWLER_USER_AGENT
        self._cache = {}

    def get(self, url):
        if url in self._cache:
            return self._cache[url]
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise SourceUnavailable(f"{url}: {exc}") from exc
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"
        self._cache[url] = resp.text
        if self.pause:
            time.sleep(self.pause)
        return resp.text


def extract_article(html: str):
    """Заголовок и основной текст публикации."""
    soup = BeautifulSoup(html, "lxml")
    for bad in soup(["script", "style", "nav", "header", "footer", "noscript", "form"]):
        bad.decompose()
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else (soup.title.get_text(strip=True) if soup.title else "")
    for sel in TEXT_SELECTORS:
        node = soup.select_one(sel)
        if node:
            text = node.get_text(" ", strip=True)
            if len(text) > 200:
                return title, text
    # запасной вариант: блок с наибольшим количеством текста в абзацах
    best, best_len = None, 0
    for div in soup.find_all(["div", "section"]):
        length = sum(len(p.get_text(strip=True)) for p in div.find_all("p", recursive=False))
        if length > best_len:
            best, best_len = div, length
    return title, best.get_text(" ", strip=True) if best is not None else ""


class HtmlSectionCrawler:
    """Раздел сайта со списком публикаций (как edumsko.ru: /activity/.../post/...)."""

    def __init__(self, http: HttpClient):
        self.http = http

    def fetch(self, source: Source):
        html = self.http.get(source.url)
        soup = BeautifulSoup(html, "lxml")
        domain = urlparse(source.url).netloc
        prefix = source.link_prefix or urlparse(source.url).path.rstrip("/") + "/"
        links, seen = [], set()
        for a in soup.find_all("a", href=True):
            full = urljoin(source.url, a["href"])
            parsed = urlparse(full)
            if parsed.netloc != domain or not parsed.path.startswith(prefix):
                continue
            clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if clean.rstrip("/") == source.url.rstrip("/") or clean in seen:
                continue
            seen.add(clean)
            links.append(clean)
        return self._read_pages(links[:MAX_PAGES_PER_SOURCE], SourceKind.HTML)

    def _read_pages(self, urls, kind):
        result = []
        for url in urls:
            try:
                title, text = extract_article(self.http.get(url))
            except SourceUnavailable as exc:
                log.info("Пропущена страница: %s", exc)
                continue
            if text:
                result.append(FoundPublication(url=url, title=title, text=text, source_kind=kind))
        return result


class SitemapCrawler(HtmlSectionCrawler):
    def fetch(self, source: Source):
        entries = self._collect(source.url, depth=0)
        fresh_after = datetime.now(timezone.utc) - timedelta(days=FRESH_DAYS)
        entries = [(u, d) for u, d in entries if d is None or d >= fresh_after]
        if source.link_prefix:
            entries = [(u, d) for u, d in entries if urlparse(u).path.startswith(source.link_prefix)]
        else:
            newsy = [(u, d) for u, d in entries if any(h in u.lower() for h in NEWS_HINTS)]
            entries = newsy or entries
        entries.sort(key=lambda x: x[1] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return self._read_pages([u for u, _ in entries[:MAX_PAGES_PER_SOURCE]], SourceKind.SITEMAP)

    def _collect(self, url, depth):
        try:
            root = ET.fromstring(self.http.get(url).encode("utf-8"))
        except ET.ParseError as exc:
            raise SourceUnavailable(f"{url}: не XML ({exc})") from exc
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        result = []
        if root.tag.endswith("sitemapindex"):
            if depth > 1:
                return result
            children = root.findall("s:sitemap", ns)
            children.sort(key=lambda n: (n.findtext("s:lastmod", "", ns)), reverse=True)
            for child in children[:5]:
                loc = child.findtext("s:loc", "", ns).strip()
                if loc:
                    try:
                        result.extend(self._collect(loc, depth + 1))
                    except SourceUnavailable as exc:
                        log.info("Пропущена карта: %s", exc)
            return result
        for node in root.findall("s:url", ns):
            loc = node.findtext("s:loc", "", ns).strip()
            lastmod = node.findtext("s:lastmod", "", ns).strip()
            result.append((loc, _parse_date(lastmod)))
        return result


class RssCrawler:
    def __init__(self, http: HttpClient):
        self.http = http

    def fetch(self, source: Source):
        try:
            root = ET.fromstring(self.http.get(source.url).encode("utf-8"))
        except ET.ParseError as exc:
            raise SourceUnavailable(f"{source.url}: не RSS ({exc})") from exc
        result = []
        content_ns = "{http://purl.org/rss/1.0/modules/content/}encoded"
        for item in root.iter("item"):
            link = (item.findtext("link") or "").strip()
            title = (item.findtext("title") or "").strip()
            html = item.findtext(content_ns) or item.findtext("description") or ""
            text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
            if len(text) < 300 and link:
                try:
                    _, page_text = extract_article(self.http.get(link))
                    text = page_text or text
                except SourceUnavailable:
                    pass
            if text:
                result.append(FoundPublication(url=link or source.url, title=title, text=text,
                                               source_kind=SourceKind.RSS,
                                               published_at=_parse_date(item.findtext("pubDate") or "")))
            if len(result) >= MAX_PAGES_PER_SOURCE:
                break
        return result


class VkCrawler:
    """Стена сообщества школы во ВКонтакте (нужен сервисный ключ приложения VK_SERVICE_TOKEN)."""

    API = "https://api.vk.com/method/"

    def __init__(self, token, version):
        self.token, self.version = token, version

    def fetch(self, source: Source):
        if not self.token:
            raise SourceUnavailable("не задан VK_SERVICE_TOKEN — стена ВКонтакте не проверяется")
        path = urlparse(source.url).path.strip("/")
        params = {"access_token": self.token, "v": self.version, "count": 30}
        m = re.fullmatch(r"(club|public)(\d+)", path)
        if m:
            params["owner_id"] = f"-{m.group(2)}"
        else:
            params["domain"] = path
        try:
            data = requests.get(self.API + "wall.get", params=params, timeout=20).json()
        except (requests.RequestException, ValueError) as exc:
            raise SourceUnavailable(f"ВКонтакте: {exc}") from exc
        if "error" in data:
            raise SourceUnavailable(f"ВКонтакте: {data['error'].get('error_msg')}")
        result = []
        for post in data.get("response", {}).get("items", []):
            text = post.get("text", "")
            if not text:
                continue
            url = f"https://vk.com/wall{post['owner_id']}_{post['id']}"
            result.append(FoundPublication(url=url, title=text[:80], text=text, source_kind=SourceKind.VK,
                                           published_at=datetime.fromtimestamp(post.get("date", 0), timezone.utc)))
        return result


class CompositeCrawler:
    """Выбирает обходчик по типу источника."""

    def __init__(self):
        http = HttpClient()
        self.by_kind = {
            SourceKind.SITEMAP: SitemapCrawler(http),
            SourceKind.RSS: RssCrawler(http),
            SourceKind.VK: VkCrawler(settings.VK_SERVICE_TOKEN, settings.VK_API_VERSION),
            SourceKind.HTML: HtmlSectionCrawler(http),
        }

    def fetch(self, source: Source):
        return self.by_kind[source.kind].fetch(source)


def _parse_date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
