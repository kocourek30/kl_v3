from django.contrib import admin
from django.urls import path
from django.template.response import TemplateResponse

from .models import PrepoctyDummy


@admin.register(PrepoctyDummy)
class PrepoctyDummyAdmin(admin.ModelAdmin):
    # nedělej žádné dotazy na model
    def get_queryset(self, request):
        from django.db.models.query import EmptyQuerySet
        return PrepoctyDummy.objects.none()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "",
                self.admin_site.admin_view(self.dashboard_view),
                name="prepocty_prepoctydummy_changelist",
            ),
        ]
        return custom + urls

    def dashboard_view(self, request):
        context = dict(
            self.admin_site.each_context(request),
            title="Přepočty",
        )
        return TemplateResponse(
            request,
            "admin/prepocty/prepocty_dummy_dashboard.html",
            context,
        )
