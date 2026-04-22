from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from vydej.models import VydejSettings as RealVydejSettings

from .models import NastaveniVydaje, ProvozniDashboard
from .services import build_canteen_staff_dashboard


@admin.register(ProvozniDashboard)
class ProvozniDashboardAdmin(admin.ModelAdmin):
    change_list_template = "admin/provoz_jidelny/provoznidashboard/change_list.html"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        dashboard = build_canteen_staff_dashboard()
        context = {
            **self.admin_site.each_context(request),
            "title": "Provoz jídelny",
            "subtitle": "Provozní dashboard pro obsluhu jídelny",
            "opts": self.model._meta,
            "has_view_permission": self.has_view_permission(request),
            "cl": None,
            "media": self.media,
            "dashboard": dashboard,
        }
        if extra_context:
            context.update(extra_context)
        return render(request, self.change_list_template, context)


@admin.register(NastaveniVydaje)
class NastaveniVydajeAdmin(admin.ModelAdmin):
    list_display = ("timeout_seconds",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        settings_obj, _ = RealVydejSettings.objects.get_or_create()
        return HttpResponseRedirect(
            reverse("admin:provoz_jidelny_nastavenivydaje_change", args=[settings_obj.pk])
        )

    def response_add(self, request, obj, post_url_continue=None):
        messages.success(request, "Nastavení výdeje bylo uloženo.")
        return HttpResponseRedirect(reverse("admin:provoz_jidelny_provoznidashboard_changelist"))

    def response_change(self, request, obj):
        messages.success(request, "Nastavení výdeje bylo upraveno.")
        return HttpResponseRedirect(reverse("admin:provoz_jidelny_provoznidashboard_changelist"))
