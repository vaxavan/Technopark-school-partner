"""Адаптер «Доказательства» для модуля partners."""

from publishing.evidence import capture_evidence


class DjangoEvidenceStore:
    def capture(self, material_id, partner_id, url):
        from materials.models import Material

        material = Material.objects.select_related("organization").get(pk=material_id)
        capture_evidence(material.organization, material, url, school_id=partner_id,
                         note="размещение у школы")
