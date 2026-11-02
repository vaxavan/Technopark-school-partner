"""Сущности предметной области. Чистый Python — ни одного импорта Django."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SourceKind(str, Enum):
    SITEMAP = "sitemap"
    RSS = "rss"
    VK = "vk"
    HTML = "html"


# Порядок надёжности источников: карта сайта, RSS, ВКонтакте, разбор страницы
SOURCE_PRIORITY = {SourceKind.SITEMAP: 1, SourceKind.RSS: 2, SourceKind.VK: 3, SourceKind.HTML: 4}


class PlacementStatus(str, Enum):
    EXPECTED = "expected"       # материал разослан, ждём
    CONFIRMED = "confirmed"     # размещение подтверждено
    DISPUTED = "disputed"       # требует решения администратора
    NOT_FOUND = "not_found"     # срок вышел, не найдено
    REJECTED = "rejected"       # администратор решил: это не наш материал


@dataclass
class Source:
    id: int | None
    kind: SourceKind
    url: str
    link_prefix: str = ""


@dataclass
class Partner:
    id: int
    name: str
    email: str
    active: bool
    sources: list[Source] = field(default_factory=list)

    def ordered_sources(self):
        return sorted(self.sources, key=lambda s: SOURCE_PRIORITY[s.kind])


@dataclass
class FoundPublication:
    url: str
    title: str
    text: str
    source_kind: SourceKind
    published_at: datetime | None = None


@dataclass
class Placement:
    id: int | None
    material_id: int
    partner_id: int
    status: PlacementStatus = PlacementStatus.EXPECTED
    similarity: float | None = None
    found_url: str = ""
    found_title: str = ""
    reminders_sent: int = 0
    dispatched_at: datetime | None = None
    last_reminder_at: datetime | None = None
    created_at: datetime | None = None
    rejected_urls: list = field(default_factory=list)

    @property
    def is_open(self):
        return self.status in (PlacementStatus.EXPECTED, PlacementStatus.NOT_FOUND)
