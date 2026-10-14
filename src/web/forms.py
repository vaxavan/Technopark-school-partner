from django import forms
from django.core.validators import FileExtensionValidator

from accounts.models import Organization
from materials.models import ApprovalBasis, Channel
from partners.models import School, Source

IMAGE_EXT = ["jpg", "jpeg", "png", "webp", "heic", "gif"]
MAX_PHOTOS = 10
MAX_PHOTO_MB = 15


class MultiFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultiFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultiFileInput(attrs={"accept": "image/*", "multiple": True}))
        kwargs.setdefault("validators", [FileExtensionValidator(IMAGE_EXT)])
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            files = [single(d, initial) for d in data if d]
        else:
            files = [single(data, initial)] if data else []
        if len(files) > MAX_PHOTOS:
            raise forms.ValidationError(f"Не больше {MAX_PHOTOS} фото")
        for f in files:
            if f.size > MAX_PHOTO_MB * 1024 * 1024:
                raise forms.ValidationError(f"Файл {f.name} больше {MAX_PHOTO_MB} МБ")
        return files


class SubmissionForm(forms.Form):
    title = forms.CharField(label="О чём новость", max_length=255,
                            widget=forms.TextInput(attrs={"placeholder": "Например: турнир по робототехнике"}))
    text = forms.CharField(label="Что произошло", max_length=10000, widget=forms.Textarea(attrs={
        "rows": 7, "placeholder": "Расскажите своими словами: что было, кто участвовал, чем закончилось"}))
    event_date = forms.CharField(label="Когда", max_length=100, required=False,
                                 widget=forms.TextInput(attrs={"placeholder": "12 октября 2026"}))
    place = forms.CharField(label="Где", max_length=255, required=False)
    photos = MultiFileField(label="Фотографии", required=False,
                            help_text=f"До {MAX_PHOTOS} фото, до {MAX_PHOTO_MB} МБ каждое")
    author_name = forms.CharField(label="Ваше имя", max_length=255)
    author_email = forms.EmailField(label="E-mail для ответа")
    author_phone = forms.CharField(label="Телефон", max_length=40, required=False)
    author_org = forms.CharField(label="Школа или подразделение", max_length=255, required=False)
    consent = forms.BooleanField(label="Согласен(а) на обработку персональных данных для рассмотрения заявки "
                                       "и связи со мной (152-ФЗ)")
    website = forms.CharField(required=False, widget=forms.HiddenInput)  # ловушка для ботов


class AddInfoForm(forms.Form):
    text = forms.CharField(label="Дополнение", max_length=5000, required=False, widget=forms.Textarea(attrs={"rows": 5}))
    photos = MultiFileField(label="Фотографии", required=False)

    def clean(self):
        data = super().clean()
        if not data.get("text") and not data.get("photos"):
            raise forms.ValidationError("Добавьте текст или фотографии")
        return data


class AcceptForm(forms.Form):
    channels = forms.MultipleChoiceField(label="Площадки", choices=Channel.choices,
                                         initial=Channel.values, widget=forms.CheckboxSelectMultiple)


class ReasonForm(forms.Form):
    reason = forms.CharField(label="Причина / что нужно", widget=forms.Textarea(attrs={"rows": 3}), max_length=2000)


class ApproveForm(forms.Form):
    basis = forms.ChoiceField(label="Основание", choices=ApprovalBasis.choices)
    scheduled_at = forms.DateTimeField(label="Дата и время выхода", required=False,
                                       widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
                                       input_formats=["%Y-%m-%dT%H:%M"])
    comment = forms.CharField(label="Комментарий", required=False, max_length=1000)

    def __init__(self, *args, role=None, **kwargs):
        super().__init__(*args, **kwargs)
        if role == "director":
            self.fields["basis"].choices = [(ApprovalBasis.DIRECTOR, ApprovalBasis.DIRECTOR.label)]
        else:
            self.fields["basis"].choices = [(ApprovalBasis.ORAL, ApprovalBasis.ORAL.label),
                                            (ApprovalBasis.NONE, ApprovalBasis.NONE.label)]


class SchoolForm(forms.ModelForm):
    class Meta:
        model = School
        fields = ["number", "territory", "name", "email", "contact_person", "website", "active", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2}), "name": forms.TextInput()}


SourceFormSet = forms.inlineformset_factory(School, Source, fields=["kind", "url", "link_prefix"], extra=1,
                                            can_delete=True)


class ImportForm(forms.Form):
    file = forms.FileField(label="Файл Excel (.xlsx)", validators=[FileExtensionValidator(["xlsx"])])


class OrgSettingsForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ["name", "style_guide"]
        widgets = {"style_guide": forms.Textarea(attrs={"rows": 8})}


class PlatformForm(forms.Form):
    target_id = forms.CharField(label="ID", max_length=64, required=False)
    token = forms.CharField(label="Токен", required=False, widget=forms.PasswordInput(render_value=False),
                            help_text="Оставьте пустым, чтобы не менять")
    enabled = forms.BooleanField(label="Включено", required=False, initial=True)
    clear = forms.BooleanField(label="Удалить токен", required=False)


class SchoolReportForm(forms.Form):
    url = forms.URLField(label="Ссылка на вашу публикацию", max_length=700)
