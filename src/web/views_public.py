"""Публичная часть: форма заявки, уточняющие вопросы, дополнения автора, страница новости,
RSS-лента для Дзена и страница отметки размещения для школ."""

import hashlib
from email.utils import format_datetime

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_http_methods

from accounts.models import Organization
from materials import domain
from materials.models import Channel, Material
from materials.services import author_added
from partners.interfaces.school_actions import report_link, unsubscribe
from partners.models import PlacementRecord
from publishing.models import Publication, PublicationStatus
from submissions.models import Submission, SubmissionStatus
from submissions.services import (
    answer_round,
    create_submission,
    current_round,
    finish_submission,
)

from .forms import AddInfoForm, SchoolReportForm, SubmissionForm

RATE_LIMIT = 5          # заявок
RATE_WINDOW = 60 * 60   # в час с одного адреса


def _client_key(request, scope):
    ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", "")
    return f"rl:{scope}:{hashlib.sha256(ip.encode()).hexdigest()[:24]}"


def _rate_limited(request, scope, limit=RATE_LIMIT, window=RATE_WINDOW):
    key = _client_key(request, scope)
    count = cache.get(key, 0)
    if count >= limit:
        return True
    cache.set(key, count + 1, window)
    return False


def landing(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    orgs = Organization.objects.order_by("name")
    if orgs.count() == 1:
        return redirect("public_form", code=orgs.first().code)
    return render(request, "public/landing.html", {"orgs": orgs})


@require_http_methods(["GET", "POST"])
def public_form(request, code):
    org = get_object_or_404(Organization, code=code)
    form = SubmissionForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        if form.cleaned_data.get("website"):
            raise Http404  # бот заполнил скрытое поле
        if _rate_limited(request, f"form:{org.code}"):
            messages.error(request, "Слишком много заявок с вашего адреса. Попробуйте через час.")
            return render(request, "public/form.html", {"org": org, "form": form}, status=429)
        sub = create_submission(org, form.cleaned_data, form.cleaned_data["photos"])
        return redirect("public_questions", code=org.code, sid=sub.public_id)
    return render(request, "public/form.html", {"org": org, "form": form})


def _get_sub(code, sid):
    return get_object_or_404(Submission.objects.select_related("organization"), organization__code=code, public_id=sid)


@require_http_methods(["GET", "POST"])
def public_questions(request, code, sid):
    sub = _get_sub(code, sid)
    if request.method == "POST":
        answer_round(sub, request.POST, skip="skip" in request.POST)
        return redirect("public_questions", code=code, sid=sid)
    rnd = current_round(sub)
    if rnd is None:
        return redirect("public_done", code=code, sid=sid)
    return render(request, "public/questions.html", {"org": sub.organization, "sub": sub, "round": rnd,
                                                     "max_rounds": 3})


def public_done(request, code, sid):
    sub = _get_sub(code, sid)
    finish_submission(sub)
    return render(request, "public/done.html", {"org": sub.organization, "sub": sub})


@require_http_methods(["GET", "POST"])
def public_add(request, code, sid):
    sub = _get_sub(code, sid)
    if sub.status != SubmissionStatus.WAITING_AUTHOR:
        return render(request, "public/status.html", {"org": sub.organization, "sub": sub})
    form = AddInfoForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            author_added(sub, form.cleaned_data["text"], form.cleaned_data["photos"])
        except domain.TransitionError as exc:
            messages.error(request, str(exc))
        else:
            return render(request, "public/status.html", {"org": sub.organization, "sub": sub, "added": True})
    return render(request, "public/add.html", {"org": sub.organization, "sub": sub, "form": form})


def _published_dzen(org):
    return (Publication.objects.filter(material__organization=org, channel=Channel.DZEN,
                                       status=PublicationStatus.PUBLISHED)
            .select_related("material", "variant", "material__submission").order_by("-published_at"))


def news_page(request, code, mid):
    org = get_object_or_404(Organization, code=code)
    material = get_object_or_404(Material, organization=org, public_id=mid)
    pub = material.publications.filter(channel=Channel.DZEN, status=PublicationStatus.PUBLISHED).first()
    if pub is None and not (request.user.is_authenticated and getattr(request, "org", None) == org):
        raise Http404
    variant = (pub.variant if pub and pub.variant else material.current_variant(Channel.DZEN)
               or material.current_variant(Channel.PARTNERS))
    title, body = _split_title(variant.text if variant else material.title, material.title)
    return render(request, "public/news.html", {"org": org, "material": material, "title": title,
                                                "paragraphs": body, "pub": pub})


def _split_title(text, fallback):
    lines = [x.strip() for x in text.strip().splitlines()]
    if len(lines) > 1 and len(lines[0]) <= 160:
        title, rest = lines[0], lines[1:]
    else:
        title, rest = fallback, lines
    paragraphs = [p for p in "\n".join(rest).split("\n") if p.strip()]
    return title, paragraphs


def rss_feed(request, code):
    """RSS-лента для Дзена (RSS 2.0 c content:encoded и enclosure для фото)."""
    org = get_object_or_404(Organization, code=code)
    items = []
    for pub in _published_dzen(org)[:30]:
        m = pub.material
        variant = pub.variant or m.current_variant(Channel.DZEN)
        title, paragraphs = _split_title(variant.text if variant else m.title, m.title)
        photos = [f"{settings.SITE_URL}{p.image.url}" for p in m.submission.photos.all() if p.image]
        items.append({
            "title": title, "link": pub.url or f"{settings.SITE_URL}/n/{org.code}/{m.public_id}/",
            "guid": str(m.public_id), "pub_date": format_datetime(pub.published_at),
            "paragraphs": paragraphs, "description": paragraphs[0] if paragraphs else title, "photos": photos,
        })
    xml = render_to_string("public/rss.xml", {"org": org, "items": items, "site_url": settings.SITE_URL})
    return HttpResponse(xml, content_type="application/rss+xml; charset=utf-8")


def demo_post(request, channel, mid):
    """Страница «публикации» в демо-режиме — чтобы снимок и доказательство были настоящими."""
    material = get_object_or_404(Material, public_id=mid)
    pub = get_object_or_404(Publication, material=material, channel=channel)
    variant = pub.variant or material.current_variant(channel)
    photos = [p.image.url for p in material.submission.photos.all() if p.image]
    return render(request, "public/demo_post.html", {"material": material, "pub": pub, "variant": variant,
                                                     "photos": photos, "org": material.organization})


@require_http_methods(["GET", "POST"])
def school_report(request, token):
    """Школа сама присылает ссылку на публикацию; система сразу сверяет текст."""
    placement = get_object_or_404(PlacementRecord.objects.select_related("school", "material", "organization"),
                                  report_token=token)
    form = SchoolReportForm(request.POST or None)
    result = None
    if request.method == "POST" and form.is_valid():
        if _rate_limited(request, f"report:{token}", limit=10):
            messages.error(request, "Слишком много попыток, попробуйте позже.")
        else:
            report_link(placement, form.cleaned_data["url"])
            result = placement
    return render(request, "public/school_report.html", {"placement": placement, "form": form, "result": result,
                                                         "org": placement.organization})


@require_http_methods(["GET", "POST"])
def school_unsubscribe(request, token):
    placement = get_object_or_404(PlacementRecord.objects.select_related("school", "organization"),
                                  report_token=token)
    done = False
    if request.method == "POST":
        unsubscribe(placement)
        done = True
    return render(request, "public/unsubscribe.html", {"placement": placement, "done": done,
                                                       "org": placement.organization})
