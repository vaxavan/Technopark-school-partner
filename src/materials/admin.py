from django.contrib import admin

from .models import Approval, Material, TextVariant


class VariantInline(admin.TabularInline):
    model = TextVariant
    extra = 0
    fields = ("channel", "version", "source", "is_current", "manual_share", "model_edit_share", "created_at")
    readonly_fields = fields


class ApprovalInline(admin.TabularInline):
    model = Approval
    extra = 0
    readonly_fields = ("user", "basis", "comment", "created_at")


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ("title", "organization", "status", "scheduled_at", "created_at")
    list_filter = ("organization", "status")
    inlines = [VariantInline, ApprovalInline]
    # статус меняется только через сценарии (конечный автомат), не вручную
    readonly_fields = ("status",)
