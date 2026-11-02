from django.contrib import admin

from .models import ClarificationRound, Submission, SubmissionPhoto


class PhotoInline(admin.TabularInline):
    model = SubmissionPhoto
    extra = 0


class RoundInline(admin.TabularInline):
    model = ClarificationRound
    extra = 0


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("number", "title", "organization", "author_name", "status", "created_at")
    list_filter = ("organization", "status")
    search_fields = ("title", "text", "author_name", "author_email")
    inlines = [PhotoInline, RoundInline]
