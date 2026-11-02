"""Сборка зависимостей модуля partners. Единственное место, которое знает обо всех слоях."""

from django.conf import settings

from partners.application.use_cases import (DetectPlacement, DispatchToPartners, RemindPartner,
                                            ResolveDisputedPlacement)
from partners.infrastructure.crawlers import CompositeCrawler
from partners.infrastructure.evidence import DjangoEvidenceStore
from partners.infrastructure.mailers import DjangoPartnerMailer
from partners.infrastructure.repositories import (DjangoMaterialReader, DjangoPartnerRepository,
                                                  DjangoPlacementRepository)
from django.utils import timezone


def _clock():
    return timezone.now()


def dispatch_to_partners(org_name):
    return DispatchToPartners(DjangoPartnerRepository(), DjangoPlacementRepository(), DjangoMaterialReader(),
                              DjangoPartnerMailer(org_name), clock=_clock)


def detect_placement(crawler=None):
    days = settings.REMINDER_INTERVAL_DAYS * (settings.REMINDERS_MAX + 1)
    return DetectPlacement(DjangoPartnerRepository(), DjangoPlacementRepository(), DjangoMaterialReader(),
                           crawler or CompositeCrawler(), DjangoEvidenceStore(), not_found_after_days=days,
                           clock=_clock)


def remind_partner(org_name):
    return RemindPartner(DjangoPartnerRepository(), DjangoPlacementRepository(), DjangoMaterialReader(),
                         DjangoPartnerMailer(org_name), max_reminders=settings.REMINDERS_MAX,
                         interval_days=settings.REMINDER_INTERVAL_DAYS, clock=_clock)


def resolve_disputed():
    return ResolveDisputedPlacement(DjangoPlacementRepository(), DjangoEvidenceStore(), clock=_clock)
