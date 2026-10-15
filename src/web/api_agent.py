"""Программный интерфейс агента обхода (см. partners/agent.py). Вход — по ключу агента
в заголовке «Authorization: Bearer …»; ключ создаётся в «Настройках» и привязан к организации."""

from datetime import datetime

from django.db.models import Exists, OuterRef
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.events import record
from partners import container
from partners.domain.entities import FoundPublication, SourceKind
from partners.infrastructure.crawlers import CompositeCrawler, PrefetchedCrawler
from partners.infrastructure.repositories import OPEN_STATUSES
from partners.models import CrawlAgent, PlacementRecord, School, Source


class AgentView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def agent(self, request):
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else ""
        agent = CrawlAgent.by_token(token)
        if agent is None:
            raise AuthenticationFailed("Неверный ключ агента")
        CrawlAgent.objects.filter(pk=agent.pk).update(last_seen_at=timezone.now())
        return agent


class AgentTasksView(AgentView):
    """Какие источники проверить: школы организации, у которых есть неподтверждённые размещения.
    Сообщества ВКонтакте сервер читает через API сам — агенту их не отдаём."""

    def get(self, request):
        agent = self.agent(request)
        open_placements = PlacementRecord.objects.filter(school=OuterRef("pk"), status__in=OPEN_STATUSES)
        schools = School.objects.filter(organization=agent.organization, active=True).filter(Exists(open_placements))
        sources = (Source.objects.filter(school__in=schools).exclude(kind=SourceKind.VK.value)
                   .select_related("school").order_by("school_id", "id"))
        return Response({"organization": agent.organization.name, "sources": [
            {"id": s.id, "school_id": s.school_id, "school": s.school.name, "kind": s.kind, "url": s.url,
             "link_prefix": s.link_prefix} for s in sources], "searches": self._searches(agent)})

    @staticmethod
    def _searches(agent):
        """Поиски по теме, которые ждут агента: по каждой школе — сайт и источники."""
        from partners.interfaces.search import school_site
        from partners.models import SchoolSearchResult

        items = []
        results = (SchoolSearchResult.objects.filter(search__organization=agent.organization, search__status="agent",
                                                     status="queued")
                   .select_related("search", "school").prefetch_related("school__sources"))
        for r in results:
            items.append({"result_id": r.id, "query": r.search.query,
                          "since": r.search.since.isoformat() if r.search.since else None,
                          "screenshots": r.search.screenshots, "school": r.school.name,
                          "site": school_site(r.school),
                          "sources": [{"id": s.id, "kind": s.kind, "url": s.url, "link_prefix": s.link_prefix}
                                      for s in r.school.sources.all() if s.kind != SourceKind.VK.value]})
        return items


def _parse_item(raw, kind):
    published = raw.get("published_at")
    try:
        published = datetime.fromisoformat(published) if published else None
    except (TypeError, ValueError):
        published = None
    url, text = str(raw.get("url", ""))[:700], str(raw.get("text", ""))
    if not url.startswith(("http://", "https://")) or not text:
        return None
    return FoundPublication(url=url, title=str(raw.get("title", ""))[:500], text=text[:20000],
                            source_kind=kind, published_at=published)


class AgentResultsView(AgentView):
    """Найденное по одной школе: сервер сверяет тексты и принимает решения, как при своём обходе."""

    def post(self, request):
        agent = self.agent(request)
        school = School.objects.filter(organization=agent.organization, pk=request.data.get("school_id")).first()
        if school is None:
            raise ValidationError({"school_id": "Нет такой школы в организации"})
        kinds = dict(school.sources.values_list("id", "kind"))
        results = {}
        for entry in request.data.get("sources") or []:
            sid = entry.get("id")
            if sid not in kinds:
                continue
            if entry.get("ok"):
                kind = SourceKind(kinds[sid])
                items = [i for i in (_parse_item(r, kind) for r in (entry.get("items") or [])[:50]) if i]
                results[sid] = (True, items)
            else:
                results[sid] = (False, str(entry.get("error", "ошибка"))[:1000])
        crawler = PrefetchedCrawler(results, fallback=CompositeCrawler())
        report = container.detect_placement(crawler)(agent.organization_id, partner_id=school.id)
        return Response({"checked": report.checked, "confirmed": report.confirmed, "disputed": report.disputed,
                         "not_found": report.not_found, "unchecked": report.unchecked})


class AgentDoneView(AgentView):
    def post(self, request):
        agent = self.agent(request)
        d = request.data
        summary = (f"школ {d.get('schools', 0)}; источников открылось {d.get('sources_ok', 0)}, "
                   f"не открылось {d.get('sources_failed', 0)}; подтверждено {d.get('confirmed', 0)}, "
                   f"требует решения {d.get('disputed', 0)}")
        CrawlAgent.objects.filter(pk=agent.pk).update(last_summary=summary[:500])
        record(agent.organization, "partners.crawled", message=f"Обход агентом: {summary}",
               agent=True, errors=[str(e)[:300] for e in (d.get("errors") or [])][:30])
        return Response({"ok": True})


class AgentSearchResultView(AgentView):
    """Итог поиска по одной школе от агента: поля (status, pages_read, error, hits — JSON) и файлы
    shot_N / html_N — снимок и копия N-й найденной страницы. multipart, чтобы снимки не раздували JSON."""

    def post(self, request):
        from partners.interfaces.search import save_agent_result
        from partners.models import SchoolSearchResult

        agent = self.agent(request)
        result = SchoolSearchResult.objects.filter(pk=request.data.get("result_id"),
                                                   search__organization=agent.organization,
                                                   status__in=["queued", "running"]).first()
        if result is None:
            raise ValidationError({"result_id": "Нет такой проверки или она уже завершена"})
        save_agent_result(result, request.data, request.FILES)
        return Response({"status": result.status, "hits": result.hits_count})
