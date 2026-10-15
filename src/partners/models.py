# Django ищет модели в partners.models — сами модели лежат в слое инфраструктуры.
from partners.infrastructure.models import (  # noqa: F401
    CrawlAgent, FoundPublicationRecord, PlacementRecord, School, SchoolSearch, SchoolSearchHit, SchoolSearchResult,
    Source)
