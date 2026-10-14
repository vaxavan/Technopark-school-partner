"""Хранение через ORM."""

from django.db import IntegrityError, transaction
from django.utils import timezone

from materials.models import Channel, Material
from partners.application.ports import MaterialForPartners
from partners.domain.entities import FoundPublication, Partner, Placement, Source

from .mappers import apply_placement, placement_to_entity, school_to_entity
from .models import FoundPublicationRecord, PlacementRecord, School
from .models import Source as SourceRecord

OPEN_STATUSES = ("expected", "not_found")


class DjangoPartnerRepository:
    def active_partners(self, organization_id) -> list[Partner]:
        qs = School.objects.filter(organization_id=organization_id, active=True).prefetch_related("sources")
        return [school_to_entity(s) for s in qs]

    def get(self, partner_id) -> Partner:
        return school_to_entity(School.objects.prefetch_related("sources").get(pk=partner_id))

    def save_found(self, partner_id, items: list[FoundPublication]):
        for f in items:
            FoundPublicationRecord.objects.update_or_create(
                school_id=partner_id, url=f.url[:700],
                defaults={"title": f.title[:500], "text": f.text, "source_kind": f.source_kind.value,
                          "fetched_at": timezone.now()},
            )

    def mark_source(self, source: Source, ok: bool, error: str = ""):
        if source.id is None:
            return
        if ok:
            SourceRecord.objects.filter(pk=source.id).update(last_ok_at=timezone.now(), last_error="")
        else:
            SourceRecord.objects.filter(pk=source.id).update(last_error=error[:2000])


class DjangoPlacementRepository:
    def get(self, placement_id) -> Placement:
        return placement_to_entity(PlacementRecord.objects.get(pk=placement_id))

    def for_material(self, material_id):
        return [placement_to_entity(r) for r in PlacementRecord.objects.filter(material_id=material_id)]

    def open_placements(self, organization_id=None):
        qs = PlacementRecord.objects.filter(status__in=OPEN_STATUSES, school__active=True)
        if organization_id is not None:
            qs = qs.filter(organization_id=organization_id)
        return [placement_to_entity(r) for r in qs.order_by("school_id", "id")]

    def create_expected(self, material_id, partner_id):
        material = Material.objects.only("organization_id").get(pk=material_id)
        try:
            with transaction.atomic():
                r = PlacementRecord.objects.create(organization_id=material.organization_id,
                                                   material_id=material_id, school_id=partner_id)
        except IntegrityError:
            return None  # уже разослано этой школе
        return placement_to_entity(r)

    def save(self, placement: Placement, **extra):
        r = PlacementRecord.objects.get(pk=placement.id)
        apply_placement(r, placement)
        for k, v in extra.items():
            setattr(r, k, v)
        r.save()


class DjangoMaterialReader:
    def get(self, material_id) -> MaterialForPartners:
        m = Material.objects.select_related("submission").get(pk=material_id)
        variant = m.current_variant(Channel.PARTNERS) or m.current_variant(Channel.DZEN)
        text = variant.text if variant else m.submission.text
        photos = [p.image.path for p in m.submission.photos.all() if p.image]
        return MaterialForPartners(id=m.id, organization_id=m.organization_id, title=m.title, text=text,
                                   attachments=photos)
