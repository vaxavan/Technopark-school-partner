from django.contrib import admin

from .models import FoundPublicationRecord, PlacementRecord, School, Source


class SourceInline(admin.TabularInline):
    model = Source
    extra = 0
    fields = ("kind", "url", "link_prefix", "last_ok_at", "last_error")
    readonly_fields = ("last_ok_at", "last_error")


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("number", "name", "territory", "email", "active")
    list_filter = ("organization", "territory", "active")
    search_fields = ("name", "email")
    inlines = [SourceInline]


@admin.register(PlacementRecord)
class PlacementAdmin(admin.ModelAdmin):
    list_display = ("material", "school", "status", "similarity", "reminders_sent", "checked_at")
    list_filter = ("organization", "status")


@admin.register(FoundPublicationRecord)
class FoundAdmin(admin.ModelAdmin):
    list_display = ("school", "title", "url", "source_kind", "fetched_at")
    list_filter = ("source_kind",)
