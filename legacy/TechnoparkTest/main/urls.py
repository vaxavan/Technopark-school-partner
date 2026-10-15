from django.urls import path
from . import views


urlpatterns = [
    path('', views.index, name='home'),
    path('about',views.about, name = 'about'),
    path('generate/', views.generate, name='generate'),
    path('api/generate/', views.generate_news_api, name='generate_news_api'),
    path("monitoring/", views.monitoring, name="monitoring"),
    path(
    "api/monitoring/start/", views.start_monitoring, name="start_monitoring"
    ),
]
