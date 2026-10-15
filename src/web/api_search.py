"""REST API поиска по сайтам школ — те же действия, что на странице «Поиск по сайтам школ»."""

from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from partners.interfaces import search as svc
from partners.models import SchoolSearch

from .api import IsOrgMember
from .forms import SchoolSearchForm


def _hit(h, request):
    return {"url": h.url, "title": h.title, "published_at": h.published_at, "snippet": h.snippet,
            "score": h.score, "screenshot": request.build_absolute_uri(h.screenshot.url) if h.screenshot else None,
            "html_copy": request.build_absolute_uri(h.html_copy.url) if h.html_copy else None,
            "checksum": h.checksum}


def _search(s, request, full=False):
    data = {"id": s.pk, "query": s.query, "since": s.since, "status": s.status, "created_at": s.created_at,
            "finished_at": s.finished_at, "screenshots": s.screenshots, "summary": svc.summary(s)}
    if full:
        data["results"] = [{"school": {"id": r.school_id, "name": r.school.name, "number": r.school.number},
                            "status": r.status, "pages_read": r.pages_read, "error": r.error,
                            "checked_by_agent": r.checked_by_agent,
                            "hits": [_hit(h, request) for h in r.hits.all()]} for r in svc._results(s)]
    return data


class SearchListView(APIView):
    """GET — поиски; POST {"query", "since": "2026-01-01", "schools": [id…], "screenshots", "on_server"}."""
    permission_classes = [IsOrgMember]

    def get(self, request):
        return Response([_search(s, request) for s in SchoolSearch.objects.filter(organization=request.org)[:50]])

    def post(self, request):
        form = SchoolSearchForm(request.data, org=request.org)
        if not form.is_valid():
            raise ValidationError(form.errors)
        d = form.cleaned_data
        try:
            s = svc.start_search(request.org, request.user, d["query"], since=d["since"],
                                 school_ids=[x.pk for x in d["schools"]],
                                 screenshots=d["screenshots"] if "screenshots" in request.data else True,
                                 on_server=d["on_server"])
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(_search(s, request, full=True), status=status.HTTP_201_CREATED)


class SearchDetailView(APIView):
    """GET — итоги по школам с найденными публикациями; POST {"action": "cancel" | "server"}; DELETE."""
    permission_classes = [IsOrgMember]

    def _get(self, request, pk):
        return get_object_or_404(SchoolSearch, organization=request.org, pk=pk)

    def get(self, request, pk):
        return Response(_search(self._get(request, pk), request, full=True))

    def post(self, request, pk):
        s = self._get(request, pk)
        action = request.data.get("action")
        if action == "cancel":
            svc.cancel(s)
        elif action == "server":
            svc.queue_on_server(s, only_unreachable=s.status == "done")
        else:
            raise ValidationError({"action": "cancel или server"})
        return Response(_search(s, request, full=True))


class SearchExportView(APIView):
    """GET …/xlsx/ — Excel «есть / нет»; GET …/zip/ — архив со снимками."""
    permission_classes = [IsOrgMember]

    def get(self, request, pk, fmt):
        s = get_object_or_404(SchoolSearch, organization=request.org, pk=pk)
        if fmt == "zip":
            return FileResponse(svc.export_search_zip(s), as_attachment=True, filename=f"poisk-{s.pk}.zip",
                                content_type="application/zip")
        resp = HttpResponse(svc.export_search_xlsx(s),
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="poisk-{s.pk}.xlsx"'
        return resp
