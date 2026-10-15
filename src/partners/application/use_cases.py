"""Сценарии модуля partners. Работают только через порты — Django здесь нет."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from partners.domain.entities import PlacementStatus
from partners.domain.matching import TextMatcher

from .ports import (Crawler, EvidenceStore, MaterialReader, PartnerMailer, PartnerRepository,
                    PlacementRepository)

log = logging.getLogger(__name__)


@dataclass
class DispatchReport:
    sent: int = 0
    without_email: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    already: int = 0


class DispatchToPartners:
    """Рассылка материала всем активным школам и создание ожидаемых размещений."""

    def __init__(self, partners: PartnerRepository, placements: PlacementRepository,
                 materials: MaterialReader, mailer: PartnerMailer, clock=datetime.now):
        self.partners, self.placements, self.materials, self.mailer, self.clock = (
            partners, placements, materials, mailer, clock)

    def __call__(self, material_id: int) -> DispatchReport:
        material = self.materials.get(material_id)
        report = DispatchReport()
        for partner in self.partners.active_partners(material.organization_id):
            placement = self.placements.create_expected(material_id, partner.id)
            if placement is None:
                # уже есть запись; если письмо тогда не ушло (сбой почты, не было адреса) — отправим сейчас
                placement = self.placements.find(material_id, partner.id)
                if placement is None or placement.dispatched_at is not None or not placement.is_open:
                    report.already += 1
                    continue
            if not partner.email:
                report.without_email.append(partner.name)
                continue
            try:
                self.mailer.send_material(partner, material, placement)
                placement.dispatched_at = self.clock()
                self.placements.save(placement)
                report.sent += 1
            except Exception as exc:  # одна школа не должна сорвать рассылку остальным
                log.warning("Рассылка школе %s не удалась: %s", partner.name, exc)
                report.failed.append((partner.name, str(exc)))
        return report


@dataclass
class DetectReport:
    checked: int = 0
    confirmed: int = 0
    disputed: int = 0
    not_found: int = 0
    unchecked: int = 0          # ни один источник школы не открылся — решения не принимаем
    source_errors: list = field(default_factory=list)


class DetectPlacement:
    """Ежедневный обход ресурсов школ и сопоставление текстов.
    Исходы: подтверждено, требует решения, не найдено."""

    def __init__(self, partners: PartnerRepository, placements: PlacementRepository,
                 materials: MaterialReader, crawler: Crawler, evidence: EvidenceStore,
                 matcher: TextMatcher | None = None, not_found_after_days: int = 9, clock=datetime.now):
        self.partners, self.placements, self.materials = partners, placements, materials
        self.crawler, self.evidence = crawler, evidence
        self.matcher = matcher or TextMatcher()
        self.not_found_after = timedelta(days=not_found_after_days)
        self.clock = clock

    def __call__(self, organization_id: int | None = None, partner_id: int | None = None) -> DetectReport:
        report = DetectReport()
        open_items = self.placements.open_placements(organization_id)
        if partner_id is not None:
            open_items = [p for p in open_items if p.partner_id == partner_id]
        by_partner = {}
        for p in open_items:
            by_partner.setdefault(p.partner_id, []).append(p)

        texts = {}
        for pid, items in by_partner.items():
            partner = self.partners.get(pid)
            found, reachable = [], 0
            for source in partner.ordered_sources():
                try:
                    found.extend(self.crawler.fetch(source))
                    self.partners.mark_source(source, ok=True)
                    reachable += 1
                except Exception as exc:  # отказ одного источника не прерывает перебор
                    self.partners.mark_source(source, ok=False, error=str(exc))
                    report.source_errors.append((partner.name, source.url, str(exc)))
            if partner.sources and not reachable:
                # Сайт школы не открылся: мы не знаем, есть ли там материал. Не ставим «не найдено»
                # и не меняем состояние — школа может подтвердить сама по ссылке из письма.
                report.unchecked += len(items)
                continue
            # убираем дубли по адресу
            unique = {f.url: f for f in found}
            found = list(unique.values())
            self.partners.save_found(pid, found)

            for placement in items:
                report.checked += 1
                if placement.material_id not in texts:
                    texts[placement.material_id] = self.materials.get(placement.material_id).text
                candidates = [f for f in found if f.url not in placement.rejected_urls]
                pub, res = self.matcher.best(texts[placement.material_id], candidates)
                now = self.clock()
                if res is not None and res.decision == PlacementStatus.CONFIRMED:
                    placement.status = PlacementStatus.CONFIRMED
                    placement.similarity, placement.found_url, placement.found_title = res.similarity, pub.url, pub.title
                    self.placements.save(placement, checked_at=now)
                    self.evidence.capture(placement.material_id, pid, pub.url)
                    report.confirmed += 1
                elif res is not None and res.decision == PlacementStatus.DISPUTED:
                    placement.status = PlacementStatus.DISPUTED
                    placement.similarity, placement.found_url, placement.found_title = res.similarity, pub.url, pub.title
                    self.placements.save(placement, checked_at=now)
                    report.disputed += 1
                else:
                    placement.similarity = res.similarity if res else None
                    # срок отсчитывается от отправки письма: школа, которой ничего не отправили, не «не нашлась»
                    started = placement.dispatched_at
                    if started and now - started > self.not_found_after:
                        placement.status = PlacementStatus.NOT_FOUND
                        report.not_found += 1
                    self.placements.save(placement, checked_at=now)
        return report


class RemindPartner:
    """Ограниченное число напоминаний школам, которые ещё не разместили материал."""

    def __init__(self, partners: PartnerRepository, placements: PlacementRepository,
                 materials: MaterialReader, mailer: PartnerMailer, max_reminders: int = 2,
                 interval_days: int = 3, clock=datetime.now):
        self.partners, self.placements, self.materials, self.mailer = partners, placements, materials, mailer
        self.max_reminders = max_reminders
        self.interval = timedelta(days=interval_days)
        self.clock = clock

    def __call__(self, organization_id: int | None = None) -> int:
        sent = 0
        now = self.clock()
        for placement in self.placements.open_placements(organization_id):
            if placement.status != PlacementStatus.EXPECTED or placement.dispatched_at is None:
                continue
            if placement.reminders_sent >= self.max_reminders:
                continue
            last = placement.last_reminder_at or placement.dispatched_at
            if now - last < self.interval:
                continue
            partner = self.partners.get(placement.partner_id)
            if not partner.email or not partner.active:
                continue
            try:
                self.mailer.send_reminder(partner, self.materials.get(placement.material_id), placement)
            except Exception as exc:
                log.warning("Напоминание школе %s не отправлено: %s", partner.name, exc)
                continue
            placement.reminders_sent += 1
            placement.last_reminder_at = now
            self.placements.save(placement)
            sent += 1
        return sent


class ResolveDisputedPlacement:
    """Решение администратора по спорной записи или ручное подтверждение по ссылке."""

    def __init__(self, placements: PlacementRepository, evidence: EvidenceStore, clock=datetime.now):
        self.placements, self.evidence, self.clock = placements, evidence, clock

    def __call__(self, placement_id: int, accept: bool, url: str = "", note: str = "", user_id=None):
        placement = self.placements.get(placement_id)
        if accept:
            if url:
                placement.found_url = url
            if not placement.found_url:
                raise ValueError("Для подтверждения нужна ссылка на публикацию")
            placement.status = PlacementStatus.CONFIRMED
            self.placements.save(placement, resolved_by_id=user_id, resolution_note=note, checked_at=self.clock())
            self.evidence.capture(placement.material_id, placement.partner_id, placement.found_url)
        else:
            if placement.found_url:
                placement.rejected_urls.append(placement.found_url)
            placement.status = PlacementStatus.EXPECTED
            placement.found_url = placement.found_title = ""
            placement.similarity = None
            self.placements.save(placement, resolved_by_id=user_id, resolution_note=note)
        return placement
