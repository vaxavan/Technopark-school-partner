"""Модуль «Доказательства»: снимок страницы, копия HTML и контрольная сумма."""

import hashlib
import logging

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from integrations.snapshot import get_snapshotter

from .models import Evidence

log = logging.getLogger(__name__)


def capture_evidence(organization, material, url, *, school_id=None, publication=None, note="", snapshotter=None):
    snapshotter = snapshotter or get_snapshotter()
    fetch_url = url
    if settings.INTERNAL_SITE_URL != settings.SITE_URL and url.startswith(settings.SITE_URL):
        fetch_url = settings.INTERNAL_SITE_URL + url[len(settings.SITE_URL):]
    snap = snapshotter.capture(fetch_url) if url.startswith("http") else None
    ev = Evidence(organization=organization, material=material, school_id=school_id, publication=publication,
                  url=url, captured_at=timezone.now(), note=note)
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    base = f"{material.public_id.hex[:8]}-{publication.channel if publication else f's{school_id}'}-{stamp}"
    if snap is not None:
        html_bytes = snap.html.encode("utf-8")
        ev.checksum = hashlib.sha256(html_bytes).hexdigest()
        ev.html_copy.save(f"{base}.html", ContentFile(html_bytes), save=False)
        if snap.png:
            ev.screenshot.save(f"{base}.png", ContentFile(snap.png), save=False)
    else:
        ev.note = (note + "; страница недоступна для снимка").strip("; ")
    ev.save()
    return ev
