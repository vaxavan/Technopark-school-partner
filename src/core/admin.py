from django.contrib import admin

from .models import Event, LLMAccount


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "organization", "kind", "message", "processed_at", "attempts")
    list_filter = ("organization", "kind")
    search_fields = ("message",)
    readonly_fields = [f.name for f in Event._meta.fields]


@admin.register(LLMAccount)
class LLMAccountAdmin(admin.ModelAdmin):
    list_display = ("organization", "folder_id", "model", "updated_at")
    exclude = ("api_key",)  # ключ меняется в «Настройках» рабочего места
