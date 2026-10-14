"""REST API (Django REST framework). Сессия + CSRF, данные — только текущей организации.
Наружу отдаются публичные UUID, а не порядковые номера."""

from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from core.models import Event
from core.ports import LLMError
from materials import domain, services
from materials.models import Channel, Material, TextVariant
from partners import container
from partners.models import PlacementRecord, School, Source
from publishing.models import Publication
from submissions.models import Submission


class IsOrgMember(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and getattr(request, "org", None))


def run(fn):
    try:
        return fn()
    except (domain.TransitionError, LLMError, ValueError) as exc:
        raise ValidationError({"detail": str(exc)})


# ---------- Сериализаторы ----------

class RoundSerializer(serializers.Serializer):
    number = serializers.IntegerField()
    questions = serializers.JSONField()
    answers = serializers.JSONField()
    skipped = serializers.BooleanField()


class SubmissionSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    rounds = RoundSerializer(many=True, read_only=True)
    photos = serializers.SerializerMethodField()
    material_id = serializers.SerializerMethodField()

    class Meta:
        model = Submission
        fields = ["id", "number", "title", "text", "event_date", "place", "author_name", "author_email",
                  "author_phone", "author_org", "status", "decision_reason", "completeness", "rounds", "photos",
                  "material_id", "created_at"]
        read_only_fields = fields

    def get_photos(self, obj):
        return [p.image.url for p in obj.photos.all() if p.image]

    def get_material_id(self, obj):
        m = getattr(obj, "material", None)
        return str(m.public_id) if m else None


class VariantSerializer(serializers.ModelSerializer):
    limits = serializers.SerializerMethodField()

    class Meta:
        model = TextVariant
        fields = ["id", "channel", "version", "text", "source", "instruction", "checks", "attempts", "manual_share",
                  "model_edit_share", "is_current", "created_at", "limits"]

    def get_limits(self, obj):
        lo, hi = obj.limits
        return {"min": lo, "max": hi, "length": obj.length}


class PublicationSerializer(serializers.ModelSerializer):
    evidence = serializers.SerializerMethodField()

    class Meta:
        model = Publication
        fields = ["channel", "status", "scheduled_at", "published_at", "url", "demo", "error", "evidence"]

    def get_evidence(self, obj):
        return [{"url": e.url, "captured_at": e.captured_at, "checksum": e.checksum,
                 "screenshot": e.screenshot.url if e.screenshot else None,
                 "html_copy": e.html_copy.url if e.html_copy else None} for e in obj.evidence.all()]


class MaterialSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    submission_id = serializers.UUIDField(source="submission.public_id", read_only=True)
    variants = serializers.SerializerMethodField()
    publications = PublicationSerializer(many=True, read_only=True)
    approvals = serializers.SerializerMethodField()

    class Meta:
        model = Material
        fields = ["id", "submission_id", "title", "status", "channels", "scheduled_at", "generation_error",
                  "variants", "publications", "approvals", "created_at"]
        read_only_fields = fields

    def get_variants(self, obj):
        return VariantSerializer(obj.current_variants(), many=True).data

    def get_approvals(self, obj):
        return [{"user": a.user.get_username(), "basis": a.basis, "comment": a.comment, "created_at": a.created_at}
                for a in obj.approvals.select_related("user")]


class SourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Source
        fields = ["id", "kind", "url", "link_prefix", "last_ok_at", "last_error"]
        read_only_fields = ["last_ok_at", "last_error"]


class SchoolSerializer(serializers.ModelSerializer):
    sources = SourceSerializer(many=True, required=False)

    class Meta:
        model = School
        fields = ["id", "number", "territory", "name", "email", "contact_person", "website", "active", "notes",
                  "sources"]

    def create(self, data):
        sources = data.pop("sources", [])
        school = School.objects.create(organization=self.context["request"].org, **data)
        for s in sources:
            Source.objects.create(school=school, **s)
        return school

    def update(self, inst, data):
        sources = data.pop("sources", None)
        inst = super().update(inst, data)
        if sources is not None:
            inst.sources.all().delete()
            for s in sources:
                Source.objects.create(school=inst, **s)
        return inst


class PlacementSerializer(serializers.ModelSerializer):
    material_id = serializers.UUIDField(source="material.public_id", read_only=True)
    material_title = serializers.CharField(source="material.title", read_only=True)
    school_name = serializers.CharField(source="school.name", read_only=True)

    class Meta:
        model = PlacementRecord
        fields = ["id", "material_id", "material_title", "school", "school_name", "status", "similarity",
                  "found_url", "found_title", "dispatched_at", "checked_at", "reminders_sent", "resolution_note"]
        read_only_fields = fields


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = ["id", "kind", "message", "object_type", "object_id", "data", "created_at", "processed_at"]


# ---------- Представления ----------

class OrgScoped:
    permission_classes = [IsOrgMember]


class SubmissionViewSet(OrgScoped, viewsets.ReadOnlyModelViewSet):
    serializer_class = SubmissionSerializer
    lookup_field = "public_id"

    def get_queryset(self):
        qs = Submission.objects.filter(organization=self.request.org).prefetch_related("rounds", "photos")
        st = self.request.query_params.get("status")
        return qs.filter(status=st) if st else qs

    @action(detail=True, methods=["post"])
    def accept(self, request, public_id=None):
        channels = request.data.get("channels") or None
        m = run(lambda: services.accept_submission(self.get_object(), request.user, channels))
        return Response(MaterialSerializer(m).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def reject(self, request, public_id=None):
        run(lambda: services.reject_submission(self.get_object(), request.user, request.data.get("reason", "")))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="request-info")
    def request_info(self, request, public_id=None):
        run(lambda: services.request_info(self.get_object(), request.user, request.data.get("reason", "")))
        return Response(self.get_serializer(self.get_object()).data)


class MaterialViewSet(OrgScoped, viewsets.ReadOnlyModelViewSet):
    serializer_class = MaterialSerializer
    lookup_field = "public_id"

    def get_queryset(self):
        qs = Material.objects.filter(organization=self.request.org).select_related("submission")
        st = self.request.query_params.get("status")
        return qs.filter(status=st) if st else qs

    def _ok(self):
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"])
    def edit(self, request, public_id=None):
        """{"channel": "vk", "text": "..."} — ручная правка; {"channel": "vk", "command": "shorter"} — моделью."""
        m, ch = self.get_object(), request.data.get("channel")
        if ch not in Channel.values:
            raise ValidationError({"channel": "Неизвестная площадка"})
        if "text" in request.data:
            run(lambda: services.edit_manually(m, ch, request.data["text"], request.user))
        else:
            run(lambda: services.edit_by_command(m, ch, request.data.get("command", ""), request.user))
        return self._ok()

    @action(detail=True, methods=["post"])
    def approve(self, request, public_id=None):
        ser = serializers.DateTimeField(required=False, allow_null=True)
        when = ser.to_internal_value(request.data["scheduled_at"]) if request.data.get("scheduled_at") else None
        run(lambda: services.approve(self.get_object(), request.user, request.data.get("basis", ""),
                                     request.data.get("comment", ""), when))
        return self._ok()

    @action(detail=True, methods=["post"])
    def reject(self, request, public_id=None):
        run(lambda: services.reject_material(self.get_object(), request.user, request.data.get("reason", "")))
        return self._ok()

    @action(detail=True, methods=["post"])
    def regenerate(self, request, public_id=None):
        run(lambda: services.regenerate(self.get_object(), request.user))
        return self._ok()

    @action(detail=True, methods=["post"])
    def unschedule(self, request, public_id=None):
        run(lambda: services.unschedule(self.get_object(), request.user))
        return self._ok()

    @action(detail=True, methods=["post"])
    def retry(self, request, public_id=None):
        run(lambda: services.retry_publication(self.get_object(), request.user))
        return self._ok()

    @action(detail=True, methods=["get"])
    def history(self, request, public_id=None):
        return Response(VariantSerializer(self.get_object().variants.order_by("channel", "-version"), many=True).data)


class SchoolViewSet(OrgScoped, viewsets.ModelViewSet):
    serializer_class = SchoolSerializer

    def get_queryset(self):
        return School.objects.filter(organization=self.request.org).prefetch_related("sources")


class PlacementViewSet(OrgScoped, viewsets.ReadOnlyModelViewSet):
    serializer_class = PlacementSerializer

    def get_queryset(self):
        qs = PlacementRecord.objects.filter(organization=self.request.org).select_related("material", "school")
        for key in ("status",):
            if self.request.query_params.get(key):
                qs = qs.filter(**{key: self.request.query_params[key]})
        if self.request.query_params.get("material"):
            qs = qs.filter(material__public_id=self.request.query_params["material"])
        return qs

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        p = self.get_object()
        run(lambda: container.resolve_disputed()(p.id, accept=bool(request.data.get("accept")),
                                                   url=request.data.get("url", ""), note=request.data.get("note", ""),
                                                   user_id=request.user.id))
        p.refresh_from_db()
        return Response(self.get_serializer(p).data)


class EventViewSet(OrgScoped, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = EventSerializer

    def get_queryset(self):
        return Event.objects.filter(organization=self.request.org)

