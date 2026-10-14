from celery import shared_task

from .models import Material, MaterialStatus
from .services import run_generation


@shared_task(soft_time_limit=300)
def generate_texts(material_id):
    material = Material.objects.select_related("organization", "submission").get(pk=material_id)
    if material.status != MaterialStatus.PREPARING:
        return  # задача уже выполнена (повторный запуск после перезапуска воркера)
    run_generation(material)
