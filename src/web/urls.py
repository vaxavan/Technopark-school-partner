from django.contrib.auth import views as auth_views
from django.urls import path

from . import views_public as pub
from . import views_staff as staff

urlpatterns = [
    # публичная часть
    path("", pub.landing, name="landing"),
    path("f/<slug:code>/", pub.public_form, name="public_form"),
    path("f/<slug:code>/s/<uuid:sid>/questions/", pub.public_questions, name="public_questions"),
    path("f/<slug:code>/s/<uuid:sid>/think/", pub.public_think, name="public_think"),
    path("f/<slug:code>/s/<uuid:sid>/done/", pub.public_done, name="public_done"),
    path("f/<slug:code>/s/<uuid:sid>/add/", pub.public_add, name="public_add"),
    path("n/<slug:code>/<uuid:mid>/", pub.news_page, name="news_page"),
    path("rss/<slug:code>.xml", pub.rss_feed, name="rss_feed"),
    path("demo/<slug:channel>/<uuid:mid>/", pub.demo_post, name="demo_post"),
    path("p/<str:token>/", pub.school_report, name="school_report"),
    path("p/<str:token>/unsubscribe/", pub.school_unsubscribe, name="school_unsubscribe"),

    # вход
    path("login/", auth_views.LoginView.as_view(template_name="staff/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("org/<int:org_id>/", staff.switch_org, name="switch_org"),

    # рабочее место
    path("app/", staff.dashboard, name="dashboard"),
    path("app/submissions/", staff.submission_list, name="submission_list"),
    path("app/submissions/<uuid:sid>/", staff.submission_detail, name="submission_detail"),
    path("app/materials/", staff.material_list, name="material_list"),
    path("app/materials/<uuid:mid>/", staff.material_detail, name="material_detail"),
    path("app/calendar/", staff.calendar, name="calendar"),
    path("app/schools/", staff.school_list, name="school_list"),
    path("app/schools/new/", staff.school_edit, name="school_new"),
    path("app/schools/<int:pk>/", staff.school_edit, name="school_edit"),
    path("app/schools/<int:pk>/check/", staff.school_check, name="school_check"),
    path("app/report/", staff.partners_report, name="partners_report"),
    path("app/report/crawl/", staff.crawl_now, name="crawl_now"),
    path("app/placements/<int:pk>/resolve/", staff.placement_resolve, name="placement_resolve"),
    path("app/events/", staff.events, name="events"),
    path("app/settings/", staff.org_settings, name="settings"),
]
