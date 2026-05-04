from django import forms
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.html import format_html

from admin_dashboard.module_registry import MANAGED_MODULES
from .models import LicenseConfig, LicenseEvent
from .services import (
    activate_license_blob,
    can_issue_local_license,
    get_license_summary_cards,
    issue_local_license,
    refresh_license_status,
)


class LicenseConfigForm(forms.ModelForm):
    class Meta:
        model = LicenseConfig
        fields = "__all__"

    def clean_license_blob(self):
        raw_text = (self.cleaned_data.get("license_blob") or "").strip()
        if not raw_text:
            return raw_text
        config = self.instance or LicenseConfig(singleton_key=1)
        from .services import verify_license_blob  # lazy import for clean admin errors

        verify_license_blob(raw_text, instance_id=config.instance_id)
        return raw_text


class LicenseBuilderForm(forms.Form):
    license_id = forms.CharField(label="ID licence", max_length=120)
    customer_name = forms.CharField(label="Zákazník", max_length=255)
    organization_name = forms.CharField(label="Organizace", max_length=255, required=False)
    license_type = forms.CharField(label="Typ licence", max_length=60, initial="annual-onprem")
    valid_from = forms.DateField(label="Platná od", widget=forms.DateInput(attrs={"type": "date"}))
    valid_until = forms.DateField(label="Platná do", widget=forms.DateInput(attrs={"type": "date"}))
    support_until = forms.DateField(label="Podpora do", widget=forms.DateInput(attrs={"type": "date"}))
    grace_until = forms.DateField(label="Grace do", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    allowed_terminals = forms.IntegerField(label="Počet terminálů", min_value=1, initial=1)
    modules = forms.MultipleChoiceField(
        label="Povolené moduly",
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    notes = forms.CharField(label="Poznámky", required=False, widget=forms.Textarea(attrs={"rows": 4}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["modules"].choices = [
            (item["slug"], f'{item["name"]} — {item.get("description", "")}')
            for item in MANAGED_MODULES
            if item["slug"] in {"ankety", "finance", "fakturace", "pokladna", "sklad"}
        ]


@admin.register(LicenseConfig)
class LicenseConfigAdmin(admin.ModelAdmin):
    form = LicenseConfigForm
    change_form_template = "admin/licencovani/licenseconfig/change_form.html"
    readonly_fields = (
        "instance_id",
        "license_id",
        "customer_name",
        "organization_name",
        "license_type",
        "valid_from",
        "valid_until",
        "support_until",
        "status_badge",
        "status_message",
        "last_verified_at",
        "activated_at",
        "activated_by",
        "created_at",
        "updated_at",
    )
    fields = (
        "instance_id",
        "status_badge",
        "status_message",
        "license_blob",
        "license_id",
        "customer_name",
        "organization_name",
        "license_type",
        "valid_from",
        "valid_until",
        "support_until",
        "last_verified_at",
        "activated_at",
        "activated_by",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return not LicenseConfig.objects.exists()

    def changelist_view(self, request, extra_context=None):
        config = LicenseConfig.objects.first()
        if config:
            return HttpResponseRedirect(reverse("admin:licencovani_licenseconfig_change", args=[config.pk]))
        return super().changelist_view(request, extra_context)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "builder/<int:config_id>/",
                self.admin_site.admin_view(self.builder_view),
                name="licencovani_builder",
            ),
            path(
                "reverify/<int:config_id>/",
                self.admin_site.admin_view(self.reverify_view),
                name="licencovani_reverify",
            ),
        ]
        return custom_urls + urls

    @admin.display(description="Stav")
    def status_badge(self, obj):
        tone_map = {
            LicenseConfig.STATUS_ACTIVE: "good",
            LicenseConfig.STATUS_SUPPORT: "warning",
            LicenseConfig.STATUS_GRACE: "warning",
            LicenseConfig.STATUS_INVALID: "danger",
            LicenseConfig.STATUS_EXPIRED: "danger",
            LicenseConfig.STATUS_MISSING: "neutral",
        }
        return format_html(
            '<span class="dash-badge {}">{}</span>',
            tone_map.get(obj.status, "neutral"),
            obj.get_status_display(),
        )

    def save_model(self, request, obj, form, change):
        raw_text = (form.cleaned_data.get("license_blob") or "").strip()
        if raw_text:
            saved = activate_license_blob(raw_text, actor=request.user)
            obj.pk = saved.pk
            self.message_user(request, f"Licence byla úspěšně ověřena. {saved.status_message}", messages.SUCCESS)
            return
        obj.status = LicenseConfig.STATUS_MISSING
        obj.status_message = "V systému není nahraná žádná licence."
        super().save_model(request, obj, form, change)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        config = LicenseConfig.objects.filter(pk=object_id).first()
        summary = get_license_summary_cards()
        extra_context = extra_context or {}
        extra_context["license_summary"] = summary
        extra_context["reverify_url"] = reverse("admin:licencovani_reverify", args=[object_id])
        extra_context["builder_url"] = reverse("admin:licencovani_builder", args=[object_id])
        extra_context["can_issue_local_license"] = can_issue_local_license()
        if config:
            refresh_license_status(config)
        return super().change_view(request, object_id, form_url, extra_context)

    def reverify_view(self, request, config_id):
        config = LicenseConfig.objects.filter(pk=config_id).first()
        if not config:
            self.message_user(request, "Licenční záznam nebyl nalezen.", messages.ERROR)
            return HttpResponseRedirect(reverse("admin:licencovani_licenseconfig_changelist"))
        result = refresh_license_status(config)
        level = messages.SUCCESS if result.is_valid else messages.WARNING
        self.message_user(request, f"Ověření dokončeno. {result.message}", level)
        return HttpResponseRedirect(reverse("admin:licencovani_licenseconfig_change", args=[config.pk]))

    def builder_view(self, request, config_id):
        config = get_object_or_404(LicenseConfig, pk=config_id)
        summary = get_license_summary_cards()
        payload = summary.get("payload") or config.cached_payload or {}

        initial = {
            "license_id": payload.get("license_id") or config.license_id or "",
            "customer_name": payload.get("customer_name") or config.customer_name or "",
            "organization_name": payload.get("organization_name") or config.organization_name or "",
            "license_type": payload.get("license_type") or config.license_type or "annual-onprem",
            "valid_from": payload.get("valid_from") or config.valid_from,
            "valid_until": payload.get("valid_until") or config.valid_until,
            "support_until": payload.get("support_until") or config.support_until,
            "grace_until": payload.get("grace_until") or "",
            "allowed_terminals": payload.get("allowed_terminals") or 1,
            "modules": payload.get("modules") or summary.get("modules") or [],
            "notes": payload.get("notes") or "",
        }

        if request.method == "POST":
            form = LicenseBuilderForm(request.POST)
            if form.is_valid():
                issue_local_license(
                    license_id=form.cleaned_data["license_id"],
                    customer_name=form.cleaned_data["customer_name"],
                    organization_name=form.cleaned_data["organization_name"],
                    license_type=form.cleaned_data["license_type"],
                    valid_from=form.cleaned_data["valid_from"],
                    valid_until=form.cleaned_data["valid_until"],
                    support_until=form.cleaned_data["support_until"],
                    grace_until=form.cleaned_data["grace_until"],
                    modules=form.cleaned_data["modules"],
                    allowed_terminals=form.cleaned_data["allowed_terminals"],
                    notes=form.cleaned_data["notes"],
                    actor=request.user,
                )
                self.message_user(request, "Licence byla nově vystavena, podepsána a aktivována.", messages.SUCCESS)
                return HttpResponseRedirect(reverse("admin:licencovani_licenseconfig_change", args=[config.pk]))
        else:
            form = LicenseBuilderForm(initial=initial)

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": config,
            "title": "Vystavit a upravit licenci",
            "license_summary": summary,
            "builder_form": form,
            "instance_id": str(config.instance_id),
            "can_issue_local_license": can_issue_local_license(),
        }
        return render(request, "admin/licencovani/licenseconfig/license_builder.html", context)


@admin.register(LicenseEvent)
class LicenseEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event_type", "status", "message", "actor")
    list_filter = ("event_type", "status")
    search_fields = ("message", "status")
    readonly_fields = ("config", "event_type", "status", "message", "details", "actor", "created_at")
