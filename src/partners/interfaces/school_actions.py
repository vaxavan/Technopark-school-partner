"""Действия самой школы по ссылке из письма: прислать ссылку на публикацию, отписаться."""

from django.utils import timezone

from core.events import record
from partners import container
from partners.domain.entities import PlacementStatus
from partners.domain.matching import TextMatcher
from partners.infrastructure.crawlers import HttpClient, SourceUnavailable, extract_article
from partners.infrastructure.repositories import DjangoMaterialReader


def report_link(placement, url):
    """Школа прислала ссылку: система сразу сверяет текст. Совпало — подтверждено со снимком,
    иначе запись уходит администратору на решение."""
    if placement.status == PlacementStatus.CONFIRMED.value:
        return placement
    try:
        title, text = extract_article(HttpClient(pause=0).get(url))
    except SourceUnavailable:
        title, text = "", ""
    original = DjangoMaterialReader().get(placement.material_id).text
    res = TextMatcher().compare(original, text) if text else None
    placement.found_url, placement.found_title = url, title[:500]
    placement.similarity = res.similarity if res else None
    if res and res.decision == PlacementStatus.CONFIRMED:
        placement.save()
        container.resolve_disputed()(placement.id, accept=True, url=url, note="ссылку прислала школа")
        placement.refresh_from_db()
    else:
        placement.status = PlacementStatus.DISPUTED.value
        placement.resolution_note = "ссылку прислала школа, нужна проверка"
        placement.save()
    record(placement.organization, "placement.reported", obj=placement.material,
           message=f"{placement.school.name} прислала ссылку: {url}", url=url)
    return placement


def unsubscribe(placement):
    school = placement.school
    if not school.unsubscribed_at:
        school.unsubscribed_at = timezone.now()
        school.save(update_fields=["unsubscribed_at"])
        record(placement.organization, "school.unsubscribed", obj=school, message=f"{school.name} отписалась от рассылки")
    return school
