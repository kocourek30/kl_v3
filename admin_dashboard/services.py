from datetime import timedelta
from io import StringIO
from time import perf_counter
from urllib.parse import urlparse

from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.db.models import Count
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from jidelnicek.models import Jidelnicek
from objednavky.models import Order
from users.models import CustomUser

from .access_registry import ADMIN_VIEW_AREAS
from .models import AdminViewAccess, AppModuleToggle, DashboardTask, TaskRun
from .module_registry import MANAGED_MODULES
from .registry import REGISTERED_TASKS
from .role_registry import OPERATIONAL_ADMIN_ROLES

ADMIN_ACCESS_LEVELS = (
    ("view", "Náhled"),
    ("write", "Správa"),
    ("control", "Plná kontrola"),
)


def sync_registered_tasks():
    task_map = {}
    for item in REGISTERED_TASKS:
        defaults = {
            "name": item["name"],
            "category": item.get("category", "system"),
            "command_name": item.get("command_name", ""),
            "description": item.get("description", ""),
            "expected_interval_hours": item.get("expected_interval_hours"),
            "default_options": item.get("default_options", {}),
            "allow_manual_run": item.get("allow_manual_run", True),
            "is_quick_link": item.get("is_quick_link", False),
            "target_url_name": item.get("target_url_name", ""),
            "target_url": item.get("target_url", ""),
        }
        task, _ = DashboardTask.objects.update_or_create(
            slug=item["slug"],
            defaults=defaults,
        )
        task_map[task.slug] = task
    return task_map


def sync_managed_modules():
    module_map = {}
    for item in MANAGED_MODULES:
        module, _ = AppModuleToggle.objects.update_or_create(
            slug=item["slug"],
            defaults={
                "name": item["name"],
                "description": item.get("description", ""),
                "app_labels": item.get("app_labels", []),
                "route_prefixes": item.get("route_prefixes", []),
            },
        )
        module_map[module.slug] = module
    return module_map


def sync_admin_view_accesses():
    access_map = {}
    for item in ADMIN_VIEW_AREAS:
        access, _ = AdminViewAccess.objects.update_or_create(
            slug=item["slug"],
            defaults={
                "name": item["name"],
                "description": item.get("description", ""),
                "app_labels": item.get("app_labels", []),
                "route_prefixes": item.get("route_prefixes", []),
            },
        )
        ensure_default_admin_groups(access)
        access_map[access.slug] = access
    sync_operational_admin_roles(access_map)
    sync_admin_access_permissions()
    return access_map


def get_disabled_admin_app_labels():
    try:
        return {
            label
            for module in AppModuleToggle.objects.filter(enabled=False)
            for label in (module.app_labels or [])
        }
    except Exception:
        return set()


def user_has_admin_area_access(user, area):
    return get_user_admin_access_level(user, area) is not None


def get_user_admin_access_level(user, area):
    if not getattr(user, "is_authenticated", False):
        return None
    if getattr(user, "is_superuser", False):
        return "control"
    if not getattr(user, "is_staff", False):
        return None
    if not area.has_group_restrictions():
        return "control"

    user_group_ids = set(user.groups.values_list("id", flat=True))
    if set(area.control_groups.values_list("id", flat=True)) & user_group_ids:
        return "control"
    if set(area.write_groups.values_list("id", flat=True)) & user_group_ids:
        return "write"
    if set(area.view_groups.values_list("id", flat=True)) & user_group_ids:
        return "view"
    return None


def get_restricted_admin_app_labels_for_user(user):
    try:
        labels = set()
        for area in AdminViewAccess.objects.prefetch_related("view_groups", "write_groups", "control_groups").all():
            if not area.has_group_restrictions():
                continue
            if user_has_admin_area_access(user, area):
                continue
            labels.update(area.app_labels or [])
        return labels
    except Exception:
        return set()


def get_blocked_module_for_path(path):
    try:
        for module in AppModuleToggle.objects.filter(enabled=False):
            for prefix in module.route_prefixes or []:
                if path.startswith(prefix):
                    return module
    except Exception:
        return None
    return None


def get_blocked_admin_area_for_user_path(user, path):
    try:
        if not path.startswith("/admin/") and not path.startswith("/pokladna/") and not path.startswith("/ankety/"):
            return None
        for area in AdminViewAccess.objects.prefetch_related("view_groups", "write_groups", "control_groups").all():
            for prefix in area.route_prefixes or []:
                if path.startswith(prefix) and not user_has_admin_area_access(user, area):
                    return area
    except Exception:
        return None
    return None


def is_menu_link_visible_for_user(user, url):
    if not url:
        return True

    parsed_path = urlparse(url).path or url
    if not parsed_path.startswith("/"):
        return True

    if get_blocked_module_for_path(parsed_path):
        return False

    if get_blocked_admin_area_for_user_path(user, parsed_path):
        return False

    return True


def get_blocked_admin_area_for_request(request):
    return get_blocked_admin_area_for_user_path(request.user, request.path)


def resolve_task_link(task):
    if task.target_url:
        return task.target_url
    if task.target_url_name:
        try:
            return reverse(task.target_url_name)
        except NoReverseMatch:
            return ""
    return ""


def summarize_output(stdout_text, stderr_text, status):
    if stderr_text.strip():
        return stderr_text.strip().splitlines()[-1][:255]
    if stdout_text.strip():
        return stdout_text.strip().splitlines()[-1][:255]
    if status == TaskRun.STATUS_SUCCESS:
        return "Úloha dokončena bez textového výstupu."
    return "Úloha skončila bez podrobností."


def run_dashboard_task(task, triggered_by=None, trigger_source=TaskRun.SOURCE_MANUAL):
    if task.is_quick_link:
        return TaskRun.objects.create(
            task=task,
            command_name="",
            status=TaskRun.STATUS_SKIPPED,
            trigger_source=trigger_source,
            triggered_by=triggered_by,
            finished_at=timezone.now(),
            summary="Tato položka je pouze rychlý odkaz, nikoli spustitelná úloha.",
        )

    if not task.command_name:
        return TaskRun.objects.create(
            task=task,
            command_name="",
            status=TaskRun.STATUS_FAILED,
            trigger_source=trigger_source,
            triggered_by=triggered_by,
            finished_at=timezone.now(),
            summary="Úloha nemá navázaný management command.",
        )

    run = TaskRun.objects.create(
        task=task,
        command_name=task.command_name,
        status=TaskRun.STATUS_RUNNING,
        trigger_source=trigger_source,
        triggered_by=triggered_by,
        metadata={"default_options": task.default_options},
    )

    stdout_buffer = StringIO()
    stderr_buffer = StringIO()
    start = perf_counter()
    status = TaskRun.STATUS_SUCCESS

    try:
        call_command(
            task.command_name,
            stdout=stdout_buffer,
            stderr=stderr_buffer,
            **(task.default_options or {}),
        )
    except Exception as exc:
        status = TaskRun.STATUS_FAILED
        stderr_buffer.write(f"{type(exc).__name__}: {exc}")

    duration = perf_counter() - start
    stdout_text = stdout_buffer.getvalue()
    stderr_text = stderr_buffer.getvalue()

    run.status = status
    run.finished_at = timezone.now()
    run.duration_seconds = duration
    run.stdout = stdout_text
    run.stderr = stderr_text
    run.summary = summarize_output(stdout_text, stderr_text, status)
    run.save(
        update_fields=[
            "status",
            "finished_at",
            "duration_seconds",
            "stdout",
            "stderr",
            "summary",
        ]
    )
    return run


def build_dashboard_health():
    now = timezone.now()
    today = timezone.localdate()
    week_ahead = today + timedelta(days=7)

    tasks = DashboardTask.objects.prefetch_related("runs").all()
    stale_tasks = [task for task in tasks if task.is_enabled and not task.is_quick_link and task.is_stale()]

    future_menu_count = Jidelnicek.objects.filter(
        platnost_do__gte=today,
        platnost_od__lte=week_ahead,
    ).count()

    users_without_groups = CustomUser.objects.filter(is_active=True, groups__isnull=True).count()
    overdue_orders = Order.objects.filter(
        datum_vydeje__lt=today,
        status__in=["objednano", "zalozena-obsluhou"],
    ).count()
    recent_failures = TaskRun.objects.filter(
        status=TaskRun.STATUS_FAILED,
        started_at__gte=now - timedelta(days=7),
    ).count()

    negative_balances = sum(
        1 for user in CustomUser.objects.filter(is_active=True)
        if getattr(user, "aktualni_zustatek", 0) < 0
    )

    return {
        "cards": [
            {
                "label": "Úlohy po termínu",
                "value": len(stale_tasks),
                "tone": "warning" if stale_tasks else "good",
                "hint": "Úlohy, které dlouho neběžely úspěšně.",
            },
            {
                "label": "Chyby za 7 dní",
                "value": recent_failures,
                "tone": "danger" if recent_failures else "good",
                "hint": "Neúspěšné běhy dashboard úloh.",
            },
            {
                "label": "Budoucí jídelníčky",
                "value": future_menu_count,
                "tone": "warning" if future_menu_count == 0 else "good",
                "hint": "Počet jídelníčků v následujících 7 dnech.",
            },
            {
                "label": "Nevyzvednuté k řešení",
                "value": overdue_orders,
                "tone": "warning" if overdue_orders else "good",
                "hint": "Objednávky po termínu ve stavu objednáno/založena obsluhou.",
            },
            {
                "label": "Uživatelé bez skupiny",
                "value": users_without_groups,
                "tone": "warning" if users_without_groups else "good",
                "hint": "Aktivní účty bez přímé Django skupiny.",
            },
            {
                "label": "Záporná konta",
                "value": negative_balances,
                "tone": "warning" if negative_balances else "good",
                "hint": "Počet aktivních uživatelů se záporným zůstatkem.",
            },
        ],
        "alerts": [
            {
                "title": "Úlohy po termínu",
                "items": [task.name for task in stale_tasks[:8]],
                "empty": "Žádná dashboard úloha není po termínu.",
            },
            {
                "title": "Doporučené další automatizace",
                "items": [
                    "Synchronizace uživatelů a skupin vůči docházce",
                    "Audit chyb RFID bridge a výdejních terminálů",
                    "Hlídání jídelníčku bez budoucího pokrytí",
                    "Kontrola uživatelů bez skupiny nebo s konfliktní konfigurací",
                ],
                "empty": "",
            },
        ],
    }


def dashboard_overview():
    sync_registered_tasks()
    sync_managed_modules()
    sync_admin_view_accesses()
    tasks = (
        DashboardTask.objects.prefetch_related("runs")
        .annotate(run_count=Count("runs"))
        .order_by("category", "name")
    )
    quick_links = [task for task in tasks if task.is_quick_link]
    runnable_tasks = [task for task in tasks if not task.is_quick_link]
    recent_runs = TaskRun.objects.select_related("task", "triggered_by")[:12]
    modules = AppModuleToggle.objects.order_by("name")
    admin_accesses = AdminViewAccess.objects.prefetch_related("view_groups", "write_groups", "control_groups").order_by("name")
    operational_roles = get_operational_roles_overview()
    return {
        "tasks": runnable_tasks,
        "quick_links": quick_links,
        "recent_runs": recent_runs,
        "modules": modules,
        "admin_accesses": admin_accesses,
        "operational_roles": operational_roles,
        "health": build_dashboard_health(),
    }


def get_default_admin_group_name(access, level):
    labels = {slug: label for slug, label in ADMIN_ACCESS_LEVELS}
    return f"Admin • {access.name} • {labels[level]}"


def ensure_default_admin_groups(access):
    group_fields = {
        "view": access.view_groups,
        "write": access.write_groups,
        "control": access.control_groups,
    }
    for level, manager in group_fields.items():
        if manager.exists():
            continue
        group, _ = Group.objects.get_or_create(name=get_default_admin_group_name(access, level))
        manager.add(group)


def get_permissions_for_area(access, level):
    permissions = Permission.objects.filter(content_type__app_label__in=(access.app_labels or [])).select_related("content_type")
    if level == "control":
        return list(permissions)

    allowed_prefixes = {"view_"}
    if level == "write":
        allowed_prefixes.update({"add_", "change_"})

    return [
        permission
        for permission in permissions
        if any(permission.codename.startswith(prefix) for prefix in allowed_prefixes)
    ]


def sync_admin_access_permissions():
    areas = list(
        AdminViewAccess.objects.prefetch_related("view_groups", "write_groups", "control_groups").all()
    )
    if not areas:
        return

    managed_group_ids = set()
    managed_permission_ids = set()
    permissions_by_group_id = {}

    for area in areas:
        area_permission_ids = {
            permission.id
            for permission in Permission.objects.filter(content_type__app_label__in=(area.app_labels or []))
        }
        managed_permission_ids.update(area_permission_ids)
        level_groups = {
            "view": list(area.view_groups.all()),
            "write": list(area.write_groups.all()),
            "control": list(area.control_groups.all()),
        }
        for level, groups in level_groups.items():
            level_permission_ids = {permission.id for permission in get_permissions_for_area(area, level)}
            for group in groups:
                managed_group_ids.add(group.id)
                permissions_by_group_id.setdefault(group.id, set()).update(level_permission_ids)

    if not managed_group_ids or not managed_permission_ids:
        return

    managed_permissions = Permission.objects.filter(id__in=managed_permission_ids)
    permission_lookup = Permission.objects.in_bulk(managed_permission_ids)
    groups = Group.objects.filter(id__in=managed_group_ids).prefetch_related("permissions")

    for group in groups:
        group.permissions.remove(*managed_permissions)
        final_permissions = [
            permission_lookup[permission_id]
            for permission_id in permissions_by_group_id.get(group.id, set())
            if permission_id in permission_lookup
        ]
        if final_permissions:
            group.permissions.add(*final_permissions)


def get_operational_role_group_name(role):
    return f"Role • {role['name']}"


def sync_operational_admin_roles(access_map=None):
    if access_map is None:
        access_map = {access.slug: access for access in AdminViewAccess.objects.all()}

    area_managers = {
        "view": "view_groups",
        "write": "write_groups",
        "control": "control_groups",
    }
    synced_groups = []

    for role in OPERATIONAL_ADMIN_ROLES:
        group, _ = Group.objects.get_or_create(name=get_operational_role_group_name(role))
        synced_groups.append(group)

        for area in access_map.values():
            area.view_groups.remove(group)
            area.write_groups.remove(group)
            area.control_groups.remove(group)

        for area_slug, level in role.get("assignments", {}).items():
            area = access_map.get(area_slug)
            if not area:
                continue
            getattr(area, area_managers[level]).add(group)

    return synced_groups


def get_operational_roles_overview():
    areas_by_slug = {
        area["slug"]: area["name"]
        for area in ADMIN_VIEW_AREAS
    }
    groups_by_name = {
        group.name: group
        for group in Group.objects.filter(
            name__in=[get_operational_role_group_name(role) for role in OPERATIONAL_ADMIN_ROLES]
        )
    }
    labels = {slug: label for slug, label in ADMIN_ACCESS_LEVELS}
    overview = []
    for role in OPERATIONAL_ADMIN_ROLES:
        group_name = get_operational_role_group_name(role)
        group = groups_by_name.get(group_name)
        overview.append(
            {
                "name": role["name"],
                "description": role["description"],
                "best_for": role.get("best_for", ""),
                "scope_note": role.get("scope_note", ""),
                "group": group,
                "group_name": group_name,
                "assignments": [
                    {
                        "area_name": areas_by_slug.get(area_slug, area_slug),
                        "level": labels.get(level, level),
                        "tone": {
                            "view": "neutral",
                            "write": "warning",
                            "control": "good",
                        }.get(level, "neutral"),
                    }
                    for area_slug, level in role.get("assignments", {}).items()
                ],
            }
        )
    return overview
