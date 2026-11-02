from django import forms
from django.contrib import admin

from .models import Evidence, PlatformAccount, Publication


class PlatformAccountForm(forms.ModelForm):
    token = forms.CharField(widget=forms.PasswordInput(render_value=False), required=False,
                            help_text="Оставьте пустым, чтобы не менять")

    class Meta:
        model = PlatformAccount
        fields = "__all__"

    def clean_token(self):
        token = self.cleaned_data.get("token")
        return token or (self.instance.token if self.instance.pk else "")


@admin.register(PlatformAccount)
class PlatformAccountAdmin(admin.ModelAdmin):
    form = PlatformAccountForm
    list_display = ("organization", "channel", "target_id", "enabled", "updated_at")


@admin.register(Publication)
class PublicationAdmin(admin.ModelAdmin):
    list_display = ("material", "channel", "status", "scheduled_at", "published_at", "demo")
    list_filter = ("channel", "status", "demo")


@admin.register(Evidence)
class EvidenceAdmin(admin.ModelAdmin):
    list_display = ("material", "school", "publication", "url", "captured_at", "checksum")
