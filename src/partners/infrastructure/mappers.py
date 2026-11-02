"""Преобразование моделей Django в сущности предметной области и обратно."""

from partners.domain.entities import Partner, Placement, PlacementStatus, Source, SourceKind

from .models import PlacementRecord, School
from .models import Source as SourceRecord


def source_to_entity(r: SourceRecord) -> Source:
    return Source(id=r.id, kind=SourceKind(r.kind), url=r.url, link_prefix=r.link_prefix)


def school_to_entity(s: School) -> Partner:
    return Partner(id=s.id, name=s.name, email=s.email if not s.unsubscribed_at else "", active=s.active,
                   sources=[source_to_entity(x) for x in s.sources.all()])


def placement_to_entity(r: PlacementRecord) -> Placement:
    return Placement(
        id=r.id, material_id=r.material_id, partner_id=r.school_id, status=PlacementStatus(r.status),
        similarity=r.similarity, found_url=r.found_url, found_title=r.found_title,
        reminders_sent=r.reminders_sent, dispatched_at=r.dispatched_at, last_reminder_at=r.last_reminder_at,
        created_at=r.created_at, rejected_urls=list(r.rejected_urls or []),
    )


def apply_placement(r: PlacementRecord, p: Placement):
    r.status = p.status.value
    r.similarity = p.similarity
    r.found_url = p.found_url
    r.found_title = (p.found_title or "")[:500]
    r.reminders_sent = p.reminders_sent
    r.dispatched_at = p.dispatched_at
    r.last_reminder_at = p.last_reminder_at
    r.rejected_urls = p.rejected_urls
    return r
