import logging

from celery import shared_task

from .models import Material, MaterialStatus
from .services import finish_generation_with_drafts, run_generation

log = logging.getLogger(__name__)


@shared_task(soft_time_limit=420, time_limit=480)
def generate_texts(material_id, only=None):
    material = Material.objects.select_related("organization", "submission").get(pk=material_id)
    if material.status != MaterialStatus.PREPARING:
        return  # задача уже выполнена (повторный запуск после перезапуска воркера)
    try:
        run_generation(material, only=only)
    except Exception:  # в том числе превышение времени — материал не должен застрять в «Подготовке»
        log.exception("Подготовка текстов прервалась: материал %s", material_id)
        finish_generation_with_drafts(material, "Подготовка текстов прервалась — подготовьте текст вручную "
                                                "или нажмите «Подготовить заново»")
