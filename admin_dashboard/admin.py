from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.html import format_html

from .models import AdminViewAccess, AppModuleToggle, DashboardTask, TaskRun
from .services import (
    build_permissions_matrix,
    dashboard_overview,
    resolve_task_link,
    run_dashboard_task,
    sync_admin_access_permissions,
    sync_managed_modules,
    sync_admin_view_accesses,
    sync_registered_tasks,
    update_permissions_matrix,
    update_role_menu_visibility,
)


@admin.register(DashboardTask)
class DashboardTaskAdmin(admin.ModelAdmin):
    change_list_template = "admin/admin_dashboard/dashboardtask/change_list.html"
    list_display = (
        "name",
        "category",
        "last_run_status",
        "last_run_at",
        "expected_interval_hours",
        "manual_controls",
    )
    list_filter = ("category", "is_enabled", "allow_manual_run", "is_quick_link")
    search_fields = ("name", "slug", "command_name", "description")
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        (
            "Základ",
            {
                "fields": (
                    "slug",
                    "name",
                    "category",
                    "description",
                    "command_name",
                    "expected_interval_hours",
                )
            },
        ),
        (
            "Chování",
            {
                "fields": (
                    "default_options",
                    "is_enabled",
                    "allow_manual_run",
                    "is_quick_link",
                    "target_url_name",
                    "target_url",
                    "notes",
                )
            },
        ),
        ("Audit", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Poslední stav")
    def last_run_status(self, obj):
        run = obj.latest_run
        if not run:
            return format_html('<span class="dash-badge neutral">Ještě neběželo</span>')
        tone = {
            TaskRun.STATUS_SUCCESS: "good",
            TaskRun.STATUS_FAILED: "danger",
            TaskRun.STATUS_RUNNING: "warning",
            TaskRun.STATUS_SKIPPED: "neutral",
        }.get(run.status, "neutral")
        return format_html(
            '<span class="dash-badge {}">{}</span>',
            tone,
            run.get_status_display(),
        )

    @admin.display(description="Poslední běh")
    def last_run_at(self, obj):
        run = obj.latest_run
        if not run:
            return "Nikdy"
        return run.started_at.strftime("%d.%m.%Y %H:%M")

    @admin.display(description="Akce")
    def manual_controls(self, obj):
        if obj.is_quick_link:
            url = resolve_task_link(obj)
            if url:
                return format_html('<a class="button" href="{}">Otevřít</a>', url)
            return "Rychlý odkaz"
        if not obj.allow_manual_run or not obj.is_enabled:
            return "Zakázáno"
        url = reverse("admin:admin_dashboard_run_task", args=[obj.pk])
        return format_html('<a class="button default" href="{}">Spustit</a>', url)

    def changelist_view(self, request, extra_context=None):
        sync_registered_tasks()
        sync_managed_modules()
        sync_admin_view_accesses()
        context = {
            **self.admin_site.each_context(request),
            **dashboard_overview(),
            "title": "Admin dashboard",
            "opts": self.model._meta,
            "has_view_permission": self.has_view_permission(request),
            "cl": None,
            "media": self.media,
        }
        if extra_context:
            context.update(extra_context)
        return render(request, self.change_list_template, context)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "permissions-matrix/",
                self.admin_site.admin_view(self.permissions_matrix_view),
                name="admin_dashboard_permissions_matrix",
            ),
            path(
                "run/<int:task_id>/",
                self.admin_site.admin_view(self.run_task_view),
                name="admin_dashboard_run_task",
            ),
            path(
                "toggle-module/<int:module_id>/",
                self.admin_site.admin_view(self.toggle_module_view),
                name="admin_dashboard_toggle_module",
            ),
            path(
                "access/<int:access_id>/",
                self.admin_site.admin_view(self.edit_access_view),
                name="admin_dashboard_edit_access",
            ),
        ]
        return custom_urls + urls

    def run_task_view(self, request, task_id):
        task = get_object_or_404(DashboardTask, pk=task_id)
        if task.is_quick_link:
            target = resolve_task_link(task) or reverse("admin:admin_dashboard_dashboardtask_changelist")
            return HttpResponseRedirect(target)

        run = run_dashboard_task(task, triggered_by=request.user)
        if run.status == TaskRun.STATUS_SUCCESS:
            self.message_user(
                request,
                f"Úloha „{task.name}“ proběhla úspěšně. {run.summary}",
                level=messages.SUCCESS,
            )
        elif run.status == TaskRun.STATUS_SKIPPED:
            self.message_user(request, run.summary, level=messages.WARNING)
        else:
            self.message_user(
                request,
                f"Úloha „{task.name}“ skončila chybou. {run.summary}",
                level=messages.ERROR,
            )
        return HttpResponseRedirect(reverse("admin:admin_dashboard_dashboardtask_changelist"))

    def permissions_matrix_view(self, request):
        if request.method == "POST":
            if request.POST.get("matrix_action") == "reset_defaults":
                sync_admin_view_accesses(force_role_defaults=True)
                self.message_user(
                    request,
                    "Bylo obnoveno doporučené bezpečné nastavení provozních rolí.",
                    level=messages.SUCCESS,
                )
                return HttpResponseRedirect(reverse("admin:admin_dashboard_permissions_matrix"))

            if request.POST.get("matrix_action") == "save_visibility":
                changed = update_role_menu_visibility(request.POST)
                self.message_user(
                    request,
                    f"Viditelnost admin položek byla uložena. Upraveno bylo {changed} nastavení.",
                    level=messages.SUCCESS,
                )
                return HttpResponseRedirect(reverse("admin:admin_dashboard_permissions_matrix"))

            changed = update_permissions_matrix(request.POST)
            self.message_user(
                request,
                f"Matice rolí a oprávnění byla uložena. Upraveno bylo {changed} nastavení.",
                level=messages.SUCCESS,
            )
            return HttpResponseRedirect(reverse("admin:admin_dashboard_permissions_matrix"))

        context = {
            **self.admin_site.each_context(request),
            **build_permissions_matrix(),
            "title": "Role a oprávnění",
            "opts": self.model._meta,
            "media": self.media,
        }
        return render(request, "admin/admin_dashboard/permissions_matrix.html", context)

    def toggle_module_view(self, request, module_id):
        module = get_object_or_404(AppModuleToggle, pk=module_id)
        module.enabled = not module.enabled
        module.save(update_fields=["enabled", "updated_at"])
        state = "povolen" if module.enabled else "zakázán"
        self.message_user(
            request,
            f"Modul „{module.name}“ je nyní {state}.",
            level=messages.SUCCESS,
        )
        return HttpResponseRedirect(reverse("admin:admin_dashboard_dashboardtask_changelist"))

    def edit_access_view(self, request, access_id):
        access = get_object_or_404(AdminViewAccess, pk=access_id)
        return HttpResponseRedirect(reverse("admin:admin_dashboard_adminviewaccess_change", args=[access.pk]))


@admin.register(TaskRun)
class TaskRunAdmin(admin.ModelAdmin):
    list_display = (
        "started_at",
        "task",
        "status_badge",
        "trigger_source",
        "triggered_by",
        "duration_seconds",
        "summary",
    )
    list_filter = ("status", "trigger_source", "task__category", "task")
    search_fields = ("task__name", "command_name", "summary", "stdout", "stderr")
    readonly_fields = (
        "task",
        "command_name",
        "status",
        "trigger_source",
        "triggered_by",
        "started_at",
        "finished_at",
        "duration_seconds",
        "summary",
        "stdout",
        "stderr",
        "metadata",
    )

    @admin.display(description="Stav")
    def status_badge(self, obj):
        tone = {
            TaskRun.STATUS_SUCCESS: "good",
            TaskRun.STATUS_FAILED: "danger",
            TaskRun.STATUS_RUNNING: "warning",
            TaskRun.STATUS_SKIPPED: "neutral",
        }.get(obj.status, "neutral")
        return format_html(
            '<span class="dash-badge {}">{}</span>',
            tone,
            obj.get_status_display(),
        )


@admin.register(AppModuleToggle)
class AppModuleToggleAdmin(admin.ModelAdmin):
    list_display = ("name", "enabled", "route_prefixes_preview", "app_labels_preview")
    list_editable = ("enabled",)
    search_fields = ("name", "slug", "description")
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Prefixy")
    def route_prefixes_preview(self, obj):
        return ", ".join(obj.route_prefixes or [])

    @admin.display(description="App labels")
    def app_labels_preview(self, obj):
        return ", ".join(obj.app_labels or [])


@admin.register(AdminViewAccess)
class AdminViewAccessAdmin(admin.ModelAdmin):
    list_display = ("name", "view_groups_summary", "write_groups_summary", "control_groups_summary")
    search_fields = ("name", "slug", "description")
    filter_horizontal = ("view_groups", "write_groups", "control_groups")
    readonly_fields = ("created_at", "updated_at", "app_labels_preview", "route_prefixes_preview")

    fieldsets = (
        (
            "Oblast",
            {
                "fields": ("slug", "name", "description", "notes"),
            },
        ),
        (
            "Role podle úrovně",
            {
                "fields": ("view_groups", "write_groups", "control_groups"),
            },
        ),
        (
            "Technické mapování",
            {
                "fields": ("app_labels_preview", "route_prefixes_preview"),
            },
        ),
        ("Audit", {"fields": ("created_at", "updated_at")}),
    )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        sync_admin_access_permissions()

    @admin.display(description="Náhled")
    def view_groups_summary(self, obj):
        groups = list(obj.view_groups.values_list("name", flat=True))
        if not groups:
            return "-"
        return ", ".join(groups)

    @admin.display(description="Správa")
    def write_groups_summary(self, obj):
        groups = list(obj.write_groups.values_list("name", flat=True))
        if not groups:
            return "-"
        return ", ".join(groups)

    @admin.display(description="Plná kontrola")
    def control_groups_summary(self, obj):
        groups = list(obj.control_groups.values_list("name", flat=True))
        if not groups:
            return "-"
        return ", ".join(groups)

    @admin.display(description="App labels")
    def app_labels_preview(self, obj):
        return ", ".join(obj.app_labels or [])

    @admin.display(description="Route prefixy")
    def route_prefixes_preview(self, obj):
        return ", ".join(obj.route_prefixes or [])
