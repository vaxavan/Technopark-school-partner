"""Сценарии partners проверяются на фейковых адаптерах — без Django и без базы."""

from datetime import datetime, timedelta

from partners.application.ports import MaterialForPartners
from partners.application.use_cases import (DetectPlacement, DispatchToPartners, RemindPartner,
                                            ResolveDisputedPlacement)
from partners.domain.entities import (FoundPublication, Partner, Placement, PlacementStatus, Source,
                                      SourceKind)

TEXT = ("В технопарке прошёл турнир по робототехнике. Участвовали двенадцать команд из Фрязино и Щёлково. "
        "Победила команда школы номер шестнадцать, второе место занял лицей номер четырнадцать.")


class Partners:
    def __init__(self, partners):
        self.p = {x.id: x for x in partners}
        self.found, self.marks = {}, []

    def active_partners(self, org):
        return [x for x in self.p.values() if x.active]

    def get(self, pid):
        return self.p[pid]

    def save_found(self, pid, items):
        self.found[pid] = items

    def mark_source(self, s, ok, error=""):
        self.marks.append((s.url, ok))


class Placements:
    def __init__(self):
        self.items, self.next_id = {}, 1

    def get(self, i):
        return self.items[i]

    def for_material(self, m):
        return [p for p in self.items.values() if p.material_id == m]

    def open_placements(self, org=None):
        return [p for p in self.items.values() if p.status in (PlacementStatus.EXPECTED, PlacementStatus.NOT_FOUND)]

    def create_expected(self, m, pid):
        if any(p.material_id == m and p.partner_id == pid for p in self.items.values()):
            return None
        p = Placement(id=self.next_id, material_id=m, partner_id=pid, created_at=NOW)
        self.items[p.id] = p
        self.next_id += 1
        return p

    def find(self, m, pid):
        return next((p for p in self.items.values() if p.material_id == m and p.partner_id == pid), None)

    def save(self, p, **extra):
        self.items[p.id] = p


class Materials:
    def get(self, m):
        return MaterialForPartners(id=m, organization_id=1, title="Турнир", text=TEXT, attachments=[])


class Mailer:
    def __init__(self):
        self.sent, self.reminders = [], []

    def send_material(self, partner, material, placement):
        if partner.email == "broken@x":
            raise OSError("smtp down")
        self.sent.append(partner.name)

    def send_reminder(self, partner, material, placement):
        self.reminders.append(partner.name)


class Crawler:
    def __init__(self, pages):
        self.pages = pages

    def fetch(self, source):
        if source.url == "bad":
            raise ConnectionError("timeout")
        return self.pages.get(source.url, [])


class Evidence:
    def __init__(self):
        self.captured = []

    def capture(self, m, pid, url):
        self.captured.append((m, pid, url))


NOW = datetime(2026, 10, 14, 12, 0)


def make_partners():
    return Partners([
        Partner(1, "Школа 16", "s16@x", True, [Source(1, SourceKind.HTML, "s16"), Source(2, SourceKind.SITEMAP, "bad")]),
        Partner(2, "Лицей 14", "l14@x", True, [Source(3, SourceKind.HTML, "l14")]),
        Partner(3, "Без почты", "", True, [Source(4, SourceKind.HTML, "np")]),
        Partner(4, "Сломанная почта", "broken@x", True, []),
        Partner(5, "Вышла из партнёров", "old@x", False, []),
    ])


def test_dispatch_creates_placements_and_isolates_failures():
    partners, placements, mailer = make_partners(), Placements(), Mailer()
    report = DispatchToPartners(partners, placements, Materials(), mailer, clock=lambda: NOW)(10)
    assert report.sent == 2 and mailer.sent == ["Школа 16", "Лицей 14"]
    assert report.without_email == ["Без почты"]
    assert report.failed[0][0] == "Сломанная почта"
    assert len(placements.items) == 4                      # неактивной школе не рассылаем
    again = DispatchToPartners(partners, placements, Materials(), mailer, clock=lambda: NOW)(10)
    assert again.already == 2 and again.sent == 0          # кому отправлено — не дублируем
    assert again.without_email == ["Без почты"] and again.failed[0][0] == "Сломанная почта"  # неотправленным — снова


def test_detect_three_outcomes_and_source_failure_tolerated():
    partners, placements = make_partners(), Placements()
    DispatchToPartners(partners, placements, Materials(), Mailer(), clock=lambda: NOW)(10)
    words = TEXT.split()
    crawler = Crawler({
        "s16": [FoundPublication("http://s16/news/1", "Турнир", "Новости. " + TEXT, SourceKind.HTML)],
        "l14": [FoundPublication("http://l14/n", "Про турнир", " ".join(words[:12]) + " а дальше совсем другой рассказ о "
                                 "школьной жизни, экскурсии и спортивных соревнованиях", SourceKind.HTML)],
        "np": [FoundPublication("http://np/x", "Осень", "Праздник осени в школе, стихи и песни", SourceKind.HTML)],
    })
    ev = Evidence()
    report = DetectPlacement(partners, placements, Materials(), crawler, ev, clock=lambda: NOW)(1)
    by_partner = {p.partner_id: p for p in placements.items.values()}
    assert by_partner[1].status == PlacementStatus.CONFIRMED
    assert by_partner[2].status == PlacementStatus.DISPUTED
    assert by_partner[3].status == PlacementStatus.EXPECTED
    assert ev.captured == [(10, 1, "http://s16/news/1")]
    assert ("bad", False) in partners.marks and ("s16", True) in partners.marks
    assert report.confirmed == 1 and report.disputed == 1 and len(report.source_errors) == 1


def test_not_found_after_deadline_and_reminders_limited():
    partners, placements, mailer = make_partners(), Placements(), Mailer()
    DispatchToPartners(partners, placements, Materials(), mailer, clock=lambda: NOW)(10)
    remind = RemindPartner(partners, placements, Materials(), mailer, max_reminders=2, interval_days=3,
                           clock=lambda: NOW + timedelta(days=4))
    assert remind(1) == 2                               # двум школам с почтой и рассылкой
    remind.clock = lambda: NOW + timedelta(days=5)
    assert remind(1) == 0                               # интервал не прошёл
    remind.clock = lambda: NOW + timedelta(days=8)
    assert remind(1) == 2
    remind.clock = lambda: NOW + timedelta(days=12)
    assert remind(1) == 0                               # лимит 2 напоминания
    late = DetectPlacement(partners, placements, Materials(), Crawler({}), Evidence(), not_found_after_days=9,
                           clock=lambda: NOW + timedelta(days=10))
    late(1)
    assert all(p.status == PlacementStatus.NOT_FOUND for p in placements.items.values()
               if p.dispatched_at is not None)


def test_resolve_disputed_reject_then_ignored_next_time():
    partners, placements = make_partners(), Placements()
    DispatchToPartners(partners, placements, Materials(), Mailer(), clock=lambda: NOW)(10)
    p = [x for x in placements.items.values() if x.partner_id == 2][0]
    p.status, p.found_url = PlacementStatus.DISPUTED, "http://l14/n"
    ev = Evidence()
    ResolveDisputedPlacement(placements, ev)(p.id, accept=False)
    assert p.status == PlacementStatus.EXPECTED and "http://l14/n" in p.rejected_urls
    ResolveDisputedPlacement(placements, ev)(p.id, accept=True, url="http://l14/real")
    assert p.status == PlacementStatus.CONFIRMED and ev.captured[-1][2] == "http://l14/real"


def test_unreachable_site_is_not_marked_not_found():
    """Сайт школы не открылся — мы не знаем, есть ли там материал: «не найдено» не ставим."""
    partners = Partners([Partner(1, "Школа", "s@x", True, [Source(1, SourceKind.HTML, "bad")])])
    placements = Placements()
    DispatchToPartners(partners, placements, Materials(), Mailer(), clock=lambda: NOW)(10)
    later = NOW + timedelta(days=30)
    report = DetectPlacement(partners, placements, Materials(), Crawler({}), Evidence(), clock=lambda: later)(1)
    assert report.unchecked == 1 and report.not_found == 0
    assert list(placements.items.values())[0].status == PlacementStatus.EXPECTED


def test_failed_mail_is_resent_on_next_dispatch():
    """Почта упала у одной школы — повторная рассылка отправит ей, а остальным не продублирует."""
    partners, placements, mailer = make_partners(), Placements(), Mailer()
    DispatchToPartners(partners, placements, Materials(), mailer, clock=lambda: NOW)(10)
    partners.p[4].email = "fixed@x"                               # адрес исправили
    again = DispatchToPartners(partners, placements, Materials(), mailer, clock=lambda: NOW)(10)
    assert again.sent == 1 and mailer.sent.count("Сломанная почта") == 1
    assert mailer.sent.count("Школа 16") == 1
