from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("api/v1/", include("web.api_urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="api-docs"),
    path("", include("web.urls")),
]

if settings.SERVE_MEDIA:
    # без nginx (локальный запуск) файлы отдаёт сам Django; в Docker их раньше перехватывает nginx
    urlpatterns.insert(0, re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}))

admin.site.site_header = "Служебная админка"
admin.site.site_title = "Служебная админка"
