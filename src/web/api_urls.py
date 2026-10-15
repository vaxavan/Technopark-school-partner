from rest_framework.routers import DefaultRouter

from . import api, api_agent

router = DefaultRouter()
router.register("submissions", api.SubmissionViewSet, basename="submission")
router.register("materials", api.MaterialViewSet, basename="material")
router.register("schools", api.SchoolViewSet, basename="school")
router.register("placements", api.PlacementViewSet, basename="placement")
router.register("events", api.EventViewSet, basename="event")

from django.urls import path  # noqa: E402

urlpatterns = router.urls + [
    path("auth/csrf/", api.CsrfView.as_view()),
    path("auth/login/", api.LoginView.as_view()),
    path("auth/logout/", api.LogoutView.as_view()),
    path("auth/me/", api.MeView.as_view()),
    path("dashboard/", api.DashboardView.as_view()),
    path("publications/", api.PublicationListView.as_view()),
    path("settings/", api.SettingsView.as_view()),
    path("settings/platforms/<slug:channel>/", api.PlatformSettingsView.as_view()),
    path("settings/llm/", api.LLMSettingsView.as_view()),
    path("settings/agent/", api.AgentSettingsView.as_view()),
    path("public/<slug:code>/", api.PublicOrgView.as_view()),
    path("public/<slug:code>/submissions/", api.PublicSubmissionCreate.as_view()),
    path("public/<slug:code>/submissions/<uuid:sid>/questions/", api.PublicSubmissionQuestions.as_view()),
    path("public/<slug:code>/submissions/<uuid:sid>/add/", api.PublicSubmissionAdd.as_view()),
    path("public/school-link/<str:token>/", api.SchoolLinkView.as_view()),
    path("agent/tasks/", api_agent.AgentTasksView.as_view()),
    path("agent/results/", api_agent.AgentResultsView.as_view()),
    path("agent/done/", api_agent.AgentDoneView.as_view()),
]
