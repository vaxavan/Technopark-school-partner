# Django ищет модели в partners.models — сами модели лежат в слое инфраструктуры.
from partners.infrastructure.models import FoundPublicationRecord, PlacementRecord, School, Source  # noqa: F401
