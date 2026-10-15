"""Обход сайта школы для поиска по теме: находит страницы новостей и читает их.

Откуда берутся страницы (по порядку):
  1) источники школы из справочника — карта сайта, RSS, раздел сайта;
  2) сам сайт: robots.txt и /sitemap.xml, лента RSS со главной, разделы «Новости», «События»,
     «Пресс-центр» и т. п. со всеми страницами списка (пагинация).
Сначала читаются страницы, у которых уже в списке видно нужные слова, затем самые свежие.
Если сайт не принимает подключение, остальные адреса этого сайта не перебираются.
Модули Django здесь не используются — тот же код работает у агента обхода."""

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from partners.domain.entities import Source, SourceKind
from partners.domain.topic import TopicQuery

from .crawlers import NEWS_HINTS, HttpClient, SourceUnavailable, _parse_date, extract_article

log = logging.getLogger(__name__)

SKIP_EXT = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".jpg", ".jpeg", ".png", ".gif", ".zip",
            ".rar", ".mp4", ".mp3", ".webp", ".svg")
SECTION_WORDS = ("новост", "событ", "пресс", "мероприят", "объявлен", "наши достижен", "профориент")
PAGE_PARAMS = ("page", "p", "pg", "pagen", "start", "offset")
RU_MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
             "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}


@dataclass
class Page:
    url: str
    title: str
    text: str
    published_at: datetime | None
    html: str = ""


@dataclass
class Candidate:
    url: str
    hint: str = ""                      # текст ссылки в списке новостей
    date: datetime | None = None        # дата из карты сайта, если есть
    order: int = 0


@dataclass
class ExploreReport:
    pages: list = field(default_factory=list)      # прочитанные страницы (Page)
    read: int = 0
    candidates: int = 0
    errors: list = field(default_factory=list)
    unreachable: bool = False


def extract_date(html: str, soup=None):
    """Дата публикации со страницы: метатеги, <time>, затем «12 октября 2026» или «12.10.2026» в тексте."""
    soup = soup or BeautifulSoup(html, "lxml")
    for attrs in ({"property": "article:published_time"}, {"itemprop": "datePublished"},
                  {"name": "date"}, {"name": "pubdate"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            d = _parse_date(tag["content"][:25])
            if d:
                return d
    t = soup.find("time")
    if t is not None:
        d = _parse_date((t.get("datetime") or "")[:25])
        if d:
            return d
    text = soup.get_text(" ", strip=True)[:4000].lower()
    m = re.search(r"\b(\d{1,2})\s+(январ\w*|феврал\w*|март\w*|апрел\w*|ма[йя]\w*|июн\w*|июл\w*|август\w*|"
                  r"сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)\s+(20\d\d)", text)
    if m:
        month = next(v for k, v in RU_MONTHS.items() if m.group(2).startswith(k))
        try:
            return datetime(int(m.group(3)), month, int(m.group(1)), tzinfo=timezone.utc)
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(20\d\d)\b", text)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


class SiteExplorer:
    def __init__(self, http: HttpClient | None = None, max_pages=150, max_listings=30):
        self.http = http or HttpClient(pause=0.3)
        self.max_pages, self.max_listings = max_pages, max_listings
        self.dead_hosts = set()

    # ---------- загрузка с защитой от «мёртвого» сайта
    def _get(self, url):
        host = urlparse(url).netloc
        if host in self.dead_hosts:
            raise SourceUnavailable(f"{url}: сайт недоступен")
        try:
            return self.http.get(url)
        except SourceUnavailable as exc:
            if "не отвечает на подключение" in str(exc) or "не найден (DNS)" in str(exc) \
                    or "соединение сброшено" in str(exc):
                self.dead_hosts.add(host)
            raise

    # ---------- основной сценарий
    def explore(self, site_url: str, sources: list[Source], query: TopicQuery, since=None) -> ExploreReport:
        report = ExploreReport()
        cands: dict[str, Candidate] = {}
        pages: dict[str, Page] = {}
        roots = {f"{urlparse(u).scheme}://{urlparse(u).netloc}" for u in [site_url] + [s.url for s in sources]
                 if u and urlparse(u).netloc}

        def add(url, hint="", date=None):
            url = _clean(url)
            if not url or url.lower().endswith(SKIP_EXT) or urlparse(url).netloc not in {urlparse(r).netloc
                                                                                          for r in roots}:
                return
            c = cands.get(url)
            if c is None:
                cands[url] = Candidate(url, hint, date, len(cands))
            else:
                c.hint = c.hint or hint
                c.date = c.date or date

        listings = []
        for s in sources:
            try:
                if s.kind == SourceKind.SITEMAP:
                    for u, d in self._sitemap(s.url):
                        add(u, date=d)
                elif s.kind == SourceKind.RSS:
                    for p in self._rss(s.url):
                        pages[p.url] = p
                elif s.kind == SourceKind.HTML:
                    listings.append(s.url)
            except SourceUnavailable as exc:
                report.errors.append(str(exc))

        for root in sorted(roots):
            if urlparse(root).netloc in self.dead_hosts:
                continue
            for sm in self._sitemaps_of(root):
                try:
                    for u, d in self._sitemap(sm):
                        add(u, date=d)
                except SourceUnavailable as exc:
                    log.info("Карта сайта недоступна: %s", exc)
            try:
                home = self._get(root + "/")
            except SourceUnavailable as exc:
                report.errors.append(str(exc))
                continue
            soup = BeautifulSoup(home, "lxml")
            for link in soup.find_all("link", attrs={"type": "application/rss+xml"}):
                try:
                    for p in self._rss(urljoin(root + "/", link.get("href", ""))):
                        pages[p.url] = p
                except SourceUnavailable:
                    pass
            for a in soup.find_all("a", href=True):
                text = a.get_text(" ", strip=True).lower()
                path = urlparse(urljoin(root + "/", a["href"])).path.lower()
                if any(w in text for w in SECTION_WORDS) or any(h in path for h in NEWS_HINTS):
                    if path.count("/") <= 3 and not re.search(r"\d{3,}", path):
                        listings.append(urljoin(root + "/", a["href"]))
                    else:
                        add(urljoin(root + "/", a["href"]), hint=text)

        self._walk_listings(listings, add)
        if all(urlparse(r).netloc in self.dead_hosts for r in roots):
            report.unreachable = True

        # порядок чтения: сначала ссылки с нужными словами, затем свежие, затем в порядке нахождения
        def rank(c):
            fresh = -(c.date.timestamp() if c.date else 0)
            newsy = any(h in urlparse(c.url).path.lower() for h in NEWS_HINTS)
            return (0 if c.hint and query.hint(c.hint) else 1, 0 if newsy else 1, fresh, c.order)

        todo = [c for c in sorted(cands.values(), key=rank)
                if c.url not in pages and not (since and c.date and c.date < since)]
        report.candidates = len(todo) + len(pages)
        for c in todo[: self.max_pages]:
            try:
                html = self._get(c.url)
            except SourceUnavailable as exc:
                report.errors.append(str(exc))
                continue
            soup = BeautifulSoup(html, "lxml")
            title, text = extract_article(html)
            if not text:
                continue
            date = extract_date(html, soup) or c.date
            if since and date and date < since:
                continue
            pages[c.url] = Page(c.url, title, text, date, html)
            report.read += 1
        report.pages = [p for p in pages.values() if not (since and p.published_at and p.published_at < since)]
        return report

    # ---------- списки новостей и их страницы
    def _walk_listings(self, start, add):
        """Списки новостей и все их страницы. Ссылки на публикации ищем относительно исходного раздела:
        на странице «/press/news/page/2» публикации всё равно лежат в «/press/news/post/…»."""
        queue = [(u, urlparse(u).path.rstrip("/")) for u in dict.fromkeys(_clean(x) for x in start if x)]
        seen = set()
        while queue and len(seen) < self.max_listings:
            url, section = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            try:
                html = self._get(url)
            except SourceUnavailable:
                continue
            base = urlparse(url)
            for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
                full = urljoin(url, a["href"])
                p = urlparse(full)
                if p.netloc != base.netloc:
                    continue
                text = a.get_text(" ", strip=True)
                path = p.path.rstrip("/")
                if _is_pagination(p, section, text):
                    nxt = _clean(full, keep_query=True)
                    if nxt not in seen and all(nxt != q for q, _ in queue):
                        queue.append((nxt, section))
                elif path.startswith(section + "/") and not re.fullmatch(rf"{re.escape(section)}/page/\d+", path):
                    add(full, hint=text)

    def _sitemaps_of(self, root):
        found = []
        try:
            robots = self._get(root + "/robots.txt")
            found = [line.split(":", 1)[1].strip() for line in robots.splitlines()
                     if line.lower().startswith("sitemap:")]
        except SourceUnavailable:
            pass
        return found or [root + "/sitemap.xml"]

    def _sitemap(self, url, depth=0):
        try:
            root = ET.fromstring(self._get(url).encode("utf-8"))
        except ET.ParseError:
            return []      # не XML (например, HTML-карта сайта) — разберём как раздел
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        result = []
        if root.tag.endswith("sitemapindex"):
            if depth > 1:
                return result
            for child in root.findall("s:sitemap", ns)[:20]:
                loc = child.findtext("s:loc", "", ns).strip()
                if loc:
                    try:
                        result.extend(self._sitemap(loc, depth + 1))
                    except SourceUnavailable:
                        pass
            return result
        for node in root.findall("s:url", ns):
            loc = node.findtext("s:loc", "", ns).strip()
            if loc:
                result.append((loc, _parse_date(node.findtext("s:lastmod", "", ns).strip())))
        return result

    def _rss(self, url):
        try:
            root = ET.fromstring(self._get(url).encode("utf-8"))
        except ET.ParseError as exc:
            raise SourceUnavailable(f"{url}: не RSS") from exc
        content_ns = "{http://purl.org/rss/1.0/modules/content/}encoded"
        result = []
        for item in root.iter("item"):
            link = (item.findtext("link") or "").strip()
            html = item.findtext(content_ns) or item.findtext("description") or ""
            text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
            if link and text:
                result.append(Page(link, (item.findtext("title") or "").strip(), text,
                                   _parse_date(item.findtext("pubDate") or "")))
        return result


def _clean(url, keep_query=False):
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return ""
    q = f"?{p.query}" if keep_query and p.query else ""
    return f"{p.scheme}://{p.netloc}{p.path}{q}"


def _is_pagination(p, base_path, text):
    if text.strip().isdigit() or text.strip().lower() in ("следующая", "далее", "вперед", "вперёд", "»", "›", ">"):
        if p.path.rstrip("/") == base_path or re.fullmatch(rf"{re.escape(base_path)}/(page/)?\d+", p.path.rstrip("/")):
            return True
    q = {k.lower() for k in parse_qs(p.query)}
    return p.path.rstrip("/") == base_path and any(k in PAGE_PARAMS or k.startswith("pagen") for k in q)
