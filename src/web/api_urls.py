from rest_framework.routers import DefaultRouter

from . import api

router = DefaultRouter()
router.register("submissions", api.SubmissionViewSet, basename="submission")
router.register("materials", api.MaterialViewSet, basename="material")
router.register("schools", api.SchoolViewSet, basename="school")
router.register("placements", api.PlacementViewSet, basename="placement")
router.register("events", api.EventViewSet, basename="event")

urlpatterns = router.urls
