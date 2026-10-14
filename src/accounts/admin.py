from django.contrib import admin

from .models import Membership, Organization


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "kind")
    inlines = [MembershipInline]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role")
    list_filter = ("organization", "role")
