from partners.domain.entities import Source, SourceKind
from partners.infrastructure.crawlers import HtmlSectionCrawler, RssCrawler, SitemapCrawler

SECTION = "https://sch16-schel.edumsko.ru/activity/proforientation/miria"

PAGES = {
    SECTION: """<html><body><nav><a href="/">Главная</a></nav>
        <a href="/activity/proforientation/miria/post/101">Битва роботов</a>
        <a href="/activity/proforientation/miria/post/101?utm=1">дубль</a>
        <a href="/activity/proforientation/miria/post/102">Экскурсия</a>
        <a href="https://other.ru/activity/proforientation/miria/post/5">чужой сайт</a>
        <a href="/news/1">другой раздел</a></body></html>""",
    SECTION + "/post/101": """<html><head><title>t</title></head><body><header>Шапка сайта школы</header>
        <h1>Команда РТУ МИРЭА Istok.Robotics на битве роботов</h1>
        <div class="user_content"><p>Уже в следующую субботу, 11 октября, отважная команда РТУ МИРЭА
        Istok.Robotics представит свою разработку: единственную машину Международного чемпионата по битве
        роботов на гусеничном ходу — RedHorn 3.</p><p>Робот создан при поддержке технологического партнёра.</p></div>
        <footer>Подвал</footer></body></html>""",
    SECTION + "/post/102": "<html><body><h1>Экскурсия</h1><article>" + "Ученики посетили музей. " * 20 + "</article></body></html>",
    "https://s.example/sitemap.xml": """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <sitemap><loc>https://s.example/sm-news.xml</loc><lastmod>2026-10-10</lastmod></sitemap></sitemapindex>""",
    "https://s.example/sm-news.xml": """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>https://s.example/news/robots</loc><lastmod>2026-10-12</lastmod></url>
        <url><loc>https://s.example/about</loc><lastmod>2026-10-12</lastmod></url>
        <url><loc>https://s.example/news/old</loc><lastmod>2020-01-01</lastmod></url></urlset>""",
    "https://s.example/news/robots": "<html><body><h1>Роботы</h1><div class='entry-content'>" + "Текст новости о роботах. " * 15 + "</div></body></html>",
    "https://s.example/about": "<html><body><h1>О школе</h1><main>" + "Описание школы. " * 30 + "</main></body></html>",
    "https://s.example/rss": """<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
        <item><title>Битва роботов</title><link>https://s.example/news/robots</link><pubDate>Sun, 12 Oct 2026 10:00:00 +0300</pubDate>
        <content:encoded><![CDATA[<p>""" + "Полный текст новости в ленте. " * 15 + """</p>]]></content:encoded></item></channel></rss>""",
}


class FakeHttp:
    def __init__(self):
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        return PAGES[url]


def test_edumsko_section():
    http = FakeHttp()
    found = HtmlSectionCrawler(http).fetch(Source(1, SourceKind.HTML, SECTION, "/activity/proforientation/miria/post/"))
    assert [f.url for f in found] == [SECTION + "/post/101", SECTION + "/post/102"]
    robots = found[0]
    assert robots.title.startswith("Команда РТУ МИРЭА")
    assert "RedHorn 3" in robots.text and "Шапка" not in robots.text and "Подвал" not in robots.text


def test_sitemap_prefers_fresh_news():
    found = SitemapCrawler(FakeHttp()).fetch(Source(2, SourceKind.SITEMAP, "https://s.example/sitemap.xml"))
    assert [f.url for f in found] == ["https://s.example/news/robots"]


def test_rss_full_text():
    found = RssCrawler(FakeHttp()).fetch(Source(3, SourceKind.RSS, "https://s.example/rss"))
    assert found[0].title == "Битва роботов" and "Полный текст" in found[0].text
    assert found[0].published_at.year == 2026
