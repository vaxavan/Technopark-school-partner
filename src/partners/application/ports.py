"""Порты модуля partners: хранение, обход, почта и доказательства."""

from dataclasses import dataclass
from typing import Protocol

from partners.domain.entities import FoundPublication, Partner, Placement, Source


@dataclass
class MaterialForPartners:
    id: int
    organization_id: int
    title: str
    text: str            # текст варианта «Рассылка школам» — его и ищем у школ
    attachments: list    # пути к фото


class PartnerRepository(Protocol):
    def active_partners(self, organization_id: int) -> list[Partner]: ...
    def get(self, partner_id: int) -> Partner: ...
    def save_found(self, partner_id: int, items: list[FoundPublication]) -> None: ...
    def mark_source(self, source: Source, ok: bool, error: str = "") -> None: ...


class PlacementRepository(Protocol):
    def get(self, placement_id: int) -> Placement: ...
    def for_material(self, material_id: int) -> list[Placement]: ...
    def open_placements(self, organization_id: int | None = None) -> list[Placement]: ...
    def create_expected(self, material_id: int, partner_id: int) -> Placement | None: ...
    def save(self, placement: Placement, **extra) -> None: ...


class MaterialReader(Protocol):
    def get(self, material_id: int) -> MaterialForPartners: ...


class Crawler(Protocol):
    def fetch(self, source: Source) -> list[FoundPublication]: ...


class PartnerMailer(Protocol):
    def send_material(self, partner: Partner, material: MaterialForPartners, placement: Placement) -> None: ...
    def send_reminder(self, partner: Partner, material: MaterialForPartners, placement: Placement) -> None: ...


class EvidenceStore(Protocol):
    def capture(self, material_id: int, partner_id: int, url: str) -> None: ...
