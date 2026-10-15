"""Рабочее место администратора и директора."""

from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.access import SESSION_KEY, AccessDenied, memberships_of
from accounts.models import Role
from core.events import record
from core.models import Event
from core.ports import LLMError
from materials import domain, services
from materials.generation import EDIT_COMMANDS
from materials.models import CHANNEL_RULES, Channel, Material, MaterialStatus
from partners import container
from partners.interfaces.importer import import_schools
from partners.interfaces.report import export_xlsx, placement_rows, summary
from partners.models import FoundPublicationRecord, PlacementRecord, School
from publishing.models import PLATFORM_CHANNELS, PlatformAccount, Publication
from publishing.services import integration_modes, save_llm_account, save_platform_account
from submissions.models import Submission, SubmissionStatus

from . import selectors
from .forms import (
    AcceptForm,
    ApproveForm,
    LLMForm,
    ImportForm,
    OrgSettingsForm,
    PlatformForm,
    ReasonForm,
    SchoolForm,
    SourceFormSet,
)


def staff_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.org is None:
            raise AccessDenied("Вы не состоите ни в одной организации")
        return view(request, *args, **kwargs)

    return wrapped


def _act(request, fn, ok_message):
    """Выполняет действие сценария и показывает понятную ошибку вместо падения."""
    try:
        result = fn()
    except (domain.TransitionError, LLMError, ValueError) as exc:
        messages.error(request, str(exc))
        return None
    messages.success(request, ok_message)
    return result or True


def _safe_next(request, default):
    """Адрес возврата только внутри сайта — иначе ссылка могла бы увести на чужой сайт."""
    nxt = request.POST.get("next", "")
    return nxt if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}) else default


@login_required
def switch_org(request, org_id):
    if any(m.organization_id == org_id for m in memberships_of(request.user)):
        request.session[SESSION_KEY] = org_id
    return redirect("dashboard")


# ---------- Рабочий стол ----------

@staff_required
def dashboard(request):
    return render(request, "staff/dashboard.html", selectors.dashboard(request.org))


# ---------- Заявки ----------

@staff_required
def submission_list(request):
    status = request.GET.get("status", "")
    qs = Submission.objects.filter(organization=request.org).select_related("material")
    if status:
        qs = qs.filter(status=status)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(text__icontains=q) | Q(author_name__icontains=q))
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "staff/submissions.html", {"page": page, "status": status, "q": q,
                                                      "statuses": SubmissionStatus.choices})


@staff_required
def submission_detail(request, sid):
    sub = get_object_or_404(Submission, organization=request.org, public_id=sid)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "accept":
            form = AcceptForm(request.POST)
            if form.is_valid():
                material = _act(request, lambda: services.accept_submission(sub, request.user,
                                                                             form.cleaned_data["channels"]),
                                "Заявка принята. Тексты готовятся в фоне — обычно до минуты.")
                if material and material is not True:
                    return redirect("material_detail", mid=material.public_id)
        elif action in ("reject", "request_info"):
            form = ReasonForm(request.POST)
            if form.is_valid():
                fn = services.reject_submission if action == "reject" else services.request_info
                _act(request, lambda: fn(sub, request.user, form.cleaned_data["reason"]),
                     "Решение сохранено, автор получит письмо")
            else:
                messages.error(request, "Заполните причину")
        return redirect("submission_detail", sid=sid)
    material = getattr(sub, "material", None) if hasattr(sub, "material") else None
    return render(request, "staff/submission_detail.html", {
        "sub": sub, "material": material, "accept_form": AcceptForm(), "reason_form": ReasonForm(),
        "events": Event.objects.filter(organization=request.org, object_type="Submission", object_id=str(sub.pk)),
    })


# ---------- Материалы ----------

@staff_required
def material_list(request):
    status = request.GET.get("status", "")
    qs = Material.objects.filter(organization=request.org).select_related("submission")
    if status:
        qs = qs.filter(status=status)
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "staff/materials.html", {"page": page, "status": status,
                                                    "statuses": MaterialStatus.choices})


@staff_required
def material_detail(request, mid):
    material = get_object_or_404(Material.objects.select_related("submission"), organization=request.org,
                                 public_id=mid)
    channel = request.GET.get("ch") or (material.channels[0] if material.channels else Channel.VK)
    if request.method == "POST":
        action = request.POST.get("action")
        channel = request.POST.get("channel", channel)
        user = request.user
        if action == "save_text":
            _act(request, lambda: services.edit_manually(material, channel, request.POST.get("text", ""), user),
                 "Новая версия сохранена")
        elif action == "command":
            cmd = request.POST.get("command") or request.POST.get("custom", "")
            _act(request, lambda: services.edit_by_command(material, channel, cmd, user), "Модель поправила текст")
        elif action == "restore":
            _act(request, lambda: services.restore_version(material, request.POST.get("variant_id"), user),
                 "Версия восстановлена")
        elif action == "channels":
            _act(request, lambda: services.set_channels(material, request.POST.getlist("channels"), user),
                 "Площадки обновлены")
        elif action == "approve":
            form = ApproveForm(request.POST, role=request.role)
            if form.is_valid():
                d = form.cleaned_data
                _act(request, lambda: services.approve(material, user, d["basis"], d["comment"], d["scheduled_at"]),
                     "Материал утверждён и поставлен в расписание")
            else:
                messages.error(request, "Проверьте основание и дату")
        elif action == "reject":
            _act(request, lambda: services.reject_material(material, user, request.POST.get("reason", "")),
                 "Материал отклонён")
        elif action == "regenerate":
            _act(request, lambda: services.regenerate(material, user), "Тексты готовятся заново")
        elif action == "stop_generation":
            _act(request, lambda: services.stop_generation(material, user), "Созданы черновики для ручной правки")
        elif action == "unschedule":
            _act(request, lambda: services.unschedule(material, user), "Снято с публикации")
        elif action == "drop_channel":
            _act(request, lambda: services.drop_channel(material, request.POST.get("publication_id"), user),
                 "Площадка убрана из материала")
        elif action == "retry":
            _act(request, lambda: services.retry_publication(material, user), "Публикация будет повторена")
        elif action == "mark_published":
            _act(request, lambda: services.mark_published(material, request.POST.get("publication_id"),
                                                          request.POST.get("url", "").strip(), user),
                 "Отмечено как опубликованное")
        elif action == "dispatch":
            _act(request, lambda: services.dispatch_to_schools(material, user), "Рассылка школам запущена")
        return redirect(f"{request.path}?ch={channel}")

    variants = {v.channel: v for v in material.current_variants()}
    current = variants.get(channel)
    lo, hi, hint = CHANNEL_RULES.get(channel, (0, 0, ""))
    tabs = [{"code": c, "label": Channel(c).label, "variant": variants.get(c),
             "warn": bool(variants.get(c) and variants[c].checks)} for c in material.channels]
    approve_form = ApproveForm(role=request.role, initial={
        "scheduled_at": timezone.localtime(timezone.now() + timezone.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")})
    preparing_stuck = (material.status == MaterialStatus.PREPARING
                       and timezone.now() - material.updated_at > services.PREPARING_STUCK_AFTER)
    return render(request, "staff/material_detail.html", {
        "preparing_stuck": preparing_stuck,
        "material": material, "sub": material.submission, "channel": channel, "channel_label": Channel(channel).label
        if channel in Channel.values else channel, "current": current, "limit_lo": lo, "limit_hi": hi, "hint": hint,
        "tabs": tabs, "history": material.variants.filter(channel=channel).order_by("-version"),
        "commands": [(k, v) for k, v in EDIT_COMMANDS.items()],
        "command_labels": {"shorter": "Сократить", "formal": "Официальнее", "simpler": "Проще",
                           "fix": "Исправить ошибки", "title": "Заголовок"},
        "approve_form": approve_form, "all_channels": Channel.choices,
        "actions": domain.allowed_actions(domain.MATERIAL_TRANSITIONS, material.status, request.role),
        "publications": material.publications.prefetch_related("evidence"),
        "approvals": material.approvals.select_related("user").order_by("-created_at"),
        "placements": summary(request.org, material) if material.placements.exists() else None,
        "evidence": material.evidence.filter(publication__isnull=True).select_related("school")[:20],
    })


@staff_required
def calendar(request):
    pubs = (Publication.objects.filter(material__organization=request.org)
            .select_related("material").order_by("-scheduled_at")[:200])
    days = {}
    for p in pubs:
        day = timezone.localtime(p.published_at or p.scheduled_at).date()
        days.setdefault(day, []).append(p)
    return render(request, "staff/calendar.html", {"days": sorted(days.items(), reverse=True),
                                                   "today": timezone.localdate()})


# ---------- Школы ----------

@staff_required
def school_list(request):
    qs = School.objects.filter(organization=request.org).annotate(
        n_confirmed=Count("placements", filter=Q(placements__status="confirmed")),
        n_total=Count("placements"))
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(territory__icontains=q) | Q(email__icontains=q))
    import_form = ImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and import_form.is_valid():
        try:
            c, u = import_schools(request.org, request.FILES["file"])
            messages.success(request, f"Импорт: добавлено {c}, обновлено {u}")
            record(request.org, "schools.imported", actor=request.user, message=f"Импорт школ: +{c}, обновлено {u}")
        except Exception as exc:
            messages.error(request, f"Не удалось прочитать файл: {exc}")
        return redirect("school_list")
    return render(request, "staff/schools.html", {"schools": qs.prefetch_related("sources"), "q": q,
                                                  "import_form": import_form,
                                                  "no_email": qs.filter(email="", active=True).count()})


@staff_required
def school_edit(request, pk=None):
    school = get_object_or_404(School, organization=request.org, pk=pk) if pk else School(organization=request.org)
    form = SchoolForm(request.POST or None, instance=school)
    formset = SourceFormSet(request.POST or None, instance=school)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        school = form.save(commit=False)
        school.organization = request.org
        if school.email and school.unsubscribed_at and "resubscribe" in request.POST:
            school.unsubscribed_at = None
        school.save()
        formset.instance = school
        formset.save()
        messages.success(request, "Школа сохранена")
        return redirect("school_list")
    found = FoundPublicationRecord.objects.filter(school=school).order_by("-fetched_at")[:15] if school.pk else []
    return render(request, "staff/school_edit.html", {"form": form, "formset": formset, "school": school,
                                                      "found": found})


@staff_required
@require_POST
def school_check(request, pk):
    from partners.tasks import crawl_all

    school = get_object_or_404(School, organization=request.org, pk=pk)
    crawl_all.delay(request.org.id, school.id, force=True)
    messages.success(request, f"Проверка «{school.name}» запущена")
    return redirect(_safe_next(request, "school_list"))


# ---------- Отчёт по школам ----------

@staff_required
def partners_report(request):
    materials = Material.objects.filter(organization=request.org, placements__isnull=False).distinct()
    material = None
    if request.GET.get("m"):
        material = get_object_or_404(Material, organization=request.org, public_id=request.GET["m"])
    status = request.GET.get("status", "")
    if request.GET.get("export"):
        data = export_xlsx(request.org, material)
        resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        name = f"otchet-shkoly-{timezone.localdate():%Y-%m-%d}.xlsx"
        resp["Content-Disposition"] = f'attachment; filename="{name}"'
        return resp
    rows = placement_rows(request.org, material)
    if status:
        rows = [r for r in rows if r["placement"].status == status]
    return render(request, "staff/report.html", {"rows": rows, "materials": materials, "material": material,
                                                 "summary": summary(request.org, material), "status": status,
                                                 "statuses": PlacementRecord.STATUS_CHOICES})


@staff_required
@require_POST
def placement_resolve(request, pk):
    placement = get_object_or_404(PlacementRecord, organization=request.org, pk=pk)
    accept = request.POST.get("decision") == "accept"
    done = _act(request, lambda: container.resolve_disputed()(placement.id, accept=accept,
                                                         url=request.POST.get("url", "").strip(),
                                                         note=request.POST.get("note", ""),
                                                         user_id=request.user.id),
         "Размещение подтверждено" if accept else "Совпадение отклонено, школа остаётся в ожидании")
    if done:
        record(request.org, "placement.resolved", actor=request.user, obj=placement.material,
               message=f"{placement.school.name}: {'подтверждено' if accept else 'отклонено'} вручную")
    return redirect(_safe_next(request, "partners_report"))


@staff_required
@require_POST
def crawl_now(request):
    from partners.tasks import crawl_all

    crawl_all.delay(request.org.id, force=True)
    messages.success(request, "Обход сайтов школ запущен. Результат появится через несколько минут.")
    return redirect(_safe_next(request, "partners_report"))


# ---------- Журнал и настройки ----------

@staff_required
def events(request):
    qs = Event.objects.filter(organization=request.org).select_related("actor")
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "staff/events.html", {"page": page})


@staff_required
def org_settings(request):
    org = request.org
    can_edit = request.role in (Role.DIRECTOR, Role.ADMIN)
    form = OrgSettingsForm(request.POST or None, instance=org, prefix="org")
    platform_forms = {}
    for ch in PLATFORM_CHANNELS:
        acc = PlatformAccount.objects.filter(organization=org, channel=ch).first()
        platform_forms[ch] = PlatformForm(request.POST or None, prefix=ch, initial={
            "target_id": acc.target_id if acc else "", "enabled": acc.enabled if acc else True})
    from core.models import LLMAccount

    llm_acc = LLMAccount.objects.filter(organization=org).first()
    llm_form = LLMForm(request.POST or None, prefix="llm", initial={
        "folder_id": llm_acc.folder_id if llm_acc else "", "model": llm_acc.model if llm_acc else "",
        "model_lite": llm_acc.model_lite if llm_acc else ""})
    from partners.models import CrawlAgent

    agent = CrawlAgent.objects.filter(organization=org).first()
    if request.method == "POST" and can_edit:
        which = request.POST.get("form")
        if which == "agent":
            if request.POST.get("action") == "revoke":
                CrawlAgent.objects.filter(organization=org).delete()
                messages.success(request, "Ключ агента удалён — сайты школ снова проверяет сервер")
            else:
                request.session["agent_token"] = CrawlAgent.issue(org)
                messages.success(request, "Новый ключ агента создан. Скопируйте его сейчас — позже он не показывается")
            return redirect("settings")
        if which == "llm" and llm_form.is_valid():
            d = llm_form.cleaned_data
            save_llm_account(org, api_key=d["api_key"], folder_id=d["folder_id"], model=d["model"],
                             model_lite=d["model_lite"], clear=d["clear"], actor=request.user)
            messages.success(request, "Настройки YandexGPT сохранены")
            return redirect("settings")
        if which == "org" and form.is_valid():
            form.save()
            messages.success(request, "Настройки организации сохранены")
            return redirect("settings")
        if which in platform_forms and platform_forms[which].is_valid():
            d = platform_forms[which].cleaned_data
            save_platform_account(org, which, target_id=d["target_id"], token=d["token"], enabled=d["enabled"],
                                  clear=d["clear"], actor=request.user)
            messages.success(request, "Настройки площадки сохранены")
            return redirect("settings")
    modes = integration_modes(org)
    return render(request, "staff/settings.html", {
        "form": form, "platform_forms": platform_forms, "modes": modes, "llm_form": llm_form,
        "llm_has_key": bool(llm_acc and llm_acc.api_key), "llm_updated": llm_acc.updated_at if llm_acc else None,
        "rss_url": modes["rss_url"], "public_url": modes["public_form_url"],
        "accounts": {a.channel: a for a in PlatformAccount.objects.filter(organization=org)},
        "agent": agent, "agent_token": request.session.pop("agent_token", None),
        "site_url": settings.SITE_URL,
    })
