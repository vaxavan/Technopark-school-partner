"""Рабочее место: поиск по сайтам школ по теме и выгрузки (Excel, архив со снимками)."""

from datetime import timedelta

from django.contrib import messages
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from materials.models import Material
from partners.interfaces import search as svc
from partners.models import CrawlAgent, SchoolSearch

from .forms import SchoolSearchForm
from .views_staff import staff_required

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _filename(prefix, search):
    return f"{prefix}-{timezone.localtime(search.created_at):%Y-%m-%d-%H%M}"


@staff_required
def search_list(request):
    org = request.org
    agent = CrawlAgent.objects.filter(organization=org).first()
    form = SchoolSearchForm(request.POST or None, org=org,
                            initial={"since": timezone.localdate() - timedelta(days=365), "screenshots": True})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            search = svc.start_search(org, request.user, d["query"], since=d["since"],
                                      school_ids=[s.pk for s in d["schools"]], screenshots=d["screenshots"],
                                      on_server=d["on_server"])
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            return redirect("search_detail", pk=search.pk)
    searches = SchoolSearch.objects.filter(organization=org).prefetch_related("results")[:30]
    rows = [{"search": s, "summary": svc.summary(s)} for s in searches]
    return render(request, "staff/search_list.html", {"form": form, "rows": rows,
                                                       "agent_active": bool(agent and agent.is_active)})


@staff_required
def search_detail(request, pk):
    search = get_object_or_404(SchoolSearch, organization=request.org, pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "cancel":
            svc.cancel(search)
            messages.success(request, "Поиск остановлен")
        elif action == "server":
            svc.queue_on_server(search, only_unreachable=search.status == "done")
            messages.success(request, "Школы поставлены в очередь на проверку с сервера")
        return redirect("search_detail", pk=search.pk)
    results = list(svc._results(search))
    return render(request, "staff/search_detail.html", {
        "search": search, "results": results, "summary": svc.summary(search),
        "running": search.status in ("queued", "running", "agent"),
    })


@staff_required
def search_xlsx(request, pk):
    search = get_object_or_404(SchoolSearch, organization=request.org, pk=pk)
    resp = HttpResponse(svc.export_search_xlsx(search), content_type=XLSX)
    resp["Content-Disposition"] = f'attachment; filename="{_filename("poisk", search)}.xlsx"'
    return resp


@staff_required
def search_zip(request, pk):
    search = get_object_or_404(SchoolSearch, organization=request.org, pk=pk)
    return FileResponse(svc.export_search_zip(search), as_attachment=True,
                        filename=f"{_filename('poisk', search)}.zip", content_type="application/zip")


@staff_required
def report_zip(request):
    material = None
    if request.GET.get("m"):
        material = get_object_or_404(Material, organization=request.org, public_id=request.GET["m"])
    return FileResponse(svc.export_evidence_zip(request.org, material), as_attachment=True,
                        filename=f"snimki-shkoly-{timezone.localdate():%Y-%m-%d}.zip", content_type="application/zip")


@staff_required
@require_POST
def search_delete(request, pk):
    search = get_object_or_404(SchoolSearch, organization=request.org, pk=pk)
    for r in search.results.all():
        for h in r.hits.all():
            for f in (h.screenshot, h.html_copy):
                if f:
                    f.delete(save=False)
    search.delete()
    messages.success(request, "Поиск и его снимки удалены")
    return redirect("search_list")
