from datetime import timedelta
from functools import lru_cache
from io import StringIO
from time import perf_counter
from urllib.parse import urlparse

from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.db.models import Count
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from jidelnicek.models import Jidelnicek
from objednavky.models import Order
from users.models import CustomUser

from .access_registry import ADMIN_VIEW_AREAS
from .models import AdminRoleMenuVisibility, AdminViewAccess, AppModuleToggle, DashboardTask, TaskRun
from .module_registry import MANAGED_MODULES
from .registry import REGISTERED_TASKS
from .role_registry import OPERATIONAL_ADMIN_ROLES

ADMIN_ACCESS_LEVELS = (
    ("view", "Náhled"),
    ("write", "Správa"),
    ("control", "Plná kontrola"),
)
ADMIN_ACCESS_LEVEL_LABELS = dict(ADMIN_ACCESS_LEVELS)
ADMIN_ACCESS_LEVEL_CHOICES_WITH_NONE = (("", "Bez přístupu"),) + ADMIN_ACCESS_LEVELS
ADMIN_ACCESS_LEVEL_TONES = {
    "": "neutral",
    "view": "neutral",
    "write": "warning",
    "control": "good",
}
GLOBAL_HIDDEN_ADMIN_APP_LABELS = {"frontend"}


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


def sync_admin_view_accesses(sync_role_defaults=True, force_role_defaults=False):
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
    if sync_role_defaults:
        sync_operational_admin_roles(access_map, force=force_role_defaults)
    sync_admin_access_permissions()
    return access_map


def get_disabled_admin_app_labels():
    try:
        labels = {
            label
            for module in AppModuleToggle.objects.filter(enabled=False)
            for label in (module.app_labels or [])
        }
        from licencovani.services import LICENSABLE_MODULE_SLUGS, is_module_licensed

        for module in AppModuleToggle.objects.exclude(slug__isnull=True):
            if module.slug in LICENSABLE_MODULE_SLUGS and not is_module_licensed(module.slug):
                labels.update(module.app_labels or [])
        return labels
    except Exception:
        return set()


def get_admin_area_for_path(path):
    try:
        for area in AdminViewAccess.objects.all():
            for prefix in area.route_prefixes or []:
                if path.startswith(prefix):
                    return area
    except Exception:
        return None
    return None


def ensure_role_menu_visibility_profiles():
    profiles = {}
    for role in OPERATIONAL_ADMIN_ROLES:
        group, _ = Group.objects.get_or_create(name=get_operational_role_group_name(role))
        profile, _ = AdminRoleMenuVisibility.objects.get_or_create(role_group=group)
        profiles[group.id] = profile
    return profiles


@lru_cache(maxsize=1)
def build_admin_menu_catalog():
    hidden_models = set(settings.JAZZMIN_SETTINGS.get("hide_models", []))
    catalog_by_app = {}

    for model in admin.site._registry:
        opts = model._meta
        model_key = f"{opts.app_label}.{opts.object_name}"
        if model_key in hidden_models:
            continue
        try:
            changelist_url = reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")
        except NoReverseMatch:
            changelist_url = ""

        app_config = apps.get_app_config(opts.app_label)
        app_entry = catalog_by_app.setdefault(
            opts.app_label,
            {
                "app_label": opts.app_label,
                "app_name": app_config.verbose_name,
                "items": [],
            },
        )
        app_entry["items"].append(
            {
                "key": f"model:{opts.app_label}.{opts.model_name}",
                "name": str(opts.verbose_name_plural).capitalize(),
                "path": changelist_url,
                "item_type": "model",
                "hint": "Model nebo přehled položek v adminu.",
            }
        )

    for app_label, links in settings.JAZZMIN_SETTINGS.get("custom_links", {}).items():
        app_name = catalog_by_app.get(app_label, {}).get("app_name")
        if not app_name:
            try:
                app_name = apps.get_app_config(app_label).verbose_name
            except LookupError:
                app_name = app_label
        app_entry = catalog_by_app.setdefault(
            app_label,
            {
                "app_label": app_label,
                "app_name": app_name,
                "items": [],
            },
        )
        for link in links:
            target = link.get("url", "")
            try:
                path = reverse(target) if target and not target.startswith("/") else target
            except NoReverseMatch:
                path = target
            if not path:
                continue
            item_key = f"custom:{app_label}:{path}"
            if any(existing["key"] == item_key for existing in app_entry["items"]):
                continue
            app_entry["items"].append(
                {
                    "key": item_key,
                    "name": link.get("name", "Rychlá akce"),
                    "path": path,
                    "item_type": "custom_link",
                    "hint": "Vlastní admin odkaz nebo rychlá akce.",
                }
            )

    order = settings.JAZZMIN_SETTINGS.get("order_with_respect_to", [])
    order_index = {label: index for index, label in enumerate(order)}
    catalog = sorted(
        catalog_by_app.values(),
        key=lambda item: (order_index.get(item["app_label"], 999), item["app_name"].lower()),
    )
    for app_entry in catalog:
        app_entry["items"] = sorted(app_entry["items"], key=lambda item: item["name"].lower())
        app_entry["app_path"] = next((item["path"] for item in app_entry["items"] if item["path"]), "")
    return catalog


@lru_cache(maxsize=1)
def get_admin_menu_item_map():
    item_map = {}
    app_by_path = {}
    for app_entry in build_admin_menu_catalog():
        if app_entry.get("app_path"):
            app_by_path[app_entry["app_path"]] = app_entry["app_label"]
        for item in app_entry["items"]:
            if item.get("path"):
                item_map[item["path"]] = item
                app_by_path[item["path"]] = app_entry["app_label"]
    return item_map, app_by_path


def get_admin_menu_item_for_path(path):
    item_map, _ = get_admin_menu_item_map()
    return item_map.get(path)


def get_admin_app_label_for_path(path):
    _, app_by_path = get_admin_menu_item_map()
    return app_by_path.get(path)


def get_hidden_area_slugs_for_user(user):
    if not getattr(user, "is_authenticated", False) or getattr(user, "is_superuser", False):
        return set()
    ensure_role_menu_visibility_profiles()
    group_ids = list(user.groups.values_list("id", flat=True))
    if not group_ids:
        return set()
    hidden = set()
    for profile in AdminRoleMenuVisibility.objects.filter(role_group_id__in=group_ids):
        hidden.update(profile.hidden_area_slugs or [])
    return hidden


def get_hidden_app_labels_for_user(user):
    if not getattr(user, "is_authenticated", False) or getattr(user, "is_superuser", False):
        return set()
    ensure_role_menu_visibility_profiles()
    group_ids = list(user.groups.values_list("id", flat=True))
    if not group_ids:
        return set()
    hidden = set()
    for profile in AdminRoleMenuVisibility.objects.filter(role_group_id__in=group_ids):
        hidden.update(profile.hidden_app_labels or [])
    return hidden


def get_hidden_menu_item_keys_for_user(user):
    if not getattr(user, "is_authenticated", False) or getattr(user, "is_superuser", False):
        return set()
    ensure_role_menu_visibility_profiles()
    group_ids = list(user.groups.values_list("id", flat=True))
    if not group_ids:
        return set()
    hidden = set()
    for profile in AdminRoleMenuVisibility.objects.filter(role_group_id__in=group_ids):
        hidden.update(profile.hidden_menu_item_keys or [])
    return hidden


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
        from licencovani.services import LICENSABLE_MODULE_SLUGS, is_module_licensed

        for module in AppModuleToggle.objects.all():
            if module.slug not in LICENSABLE_MODULE_SLUGS:
                continue
            for prefix in module.route_prefixes or []:
                if path.startswith(prefix) and not is_module_licensed(module.slug):
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

    item = get_admin_menu_item_for_path(parsed_path)
    if item and item["key"] in get_hidden_menu_item_keys_for_user(user):
        return False

    app_label = get_admin_app_label_for_path(parsed_path)
    if app_label and app_label in get_hidden_app_labels_for_user(user):
        return False

    area = get_admin_area_for_path(parsed_path)
    if area and area.slug in get_hidden_area_slugs_for_user(user):
        return False

    if get_blocked_admin_area_for_user_path(user, parsed_path):
        return False

    return True


def build_visible_custom_links_for_app(user, app_label):
    links = []
    for link in settings.JAZZMIN_SETTINGS.get("custom_links", {}).get(app_label, []):
        target = link.get("url", "")
        try:
            resolved_url = reverse(target) if target and not target.startswith("/") else target
        except NoReverseMatch:
            resolved_url = target
        if not resolved_url or not is_menu_link_visible_for_user(user, resolved_url):
            continue
        links.append(
            {
                "name": link.get("name", "Rychlá akce"),
                "object_name": link.get("name", "Rychlá akce"),
                "perms": {},
                "admin_url": resolved_url,
                "add_url": None,
                "view_only": True,
                "icon": link.get("icon", "fas fa-link"),
                "custom": True,
            }
        )
    return links


def filter_admin_app_items_for_user(user, app_list):
    hidden_app_labels = get_hidden_app_labels_for_user(user) | GLOBAL_HIDDEN_ADMIN_APP_LABELS
    filtered_apps = []
    for app in app_list:
        if app.get("app_label") in hidden_app_labels:
            continue
        models = []
        for model in app.get("models", []):
            target_url = model.get("admin_url") or model.get("url") or model.get("add_url") or ""
            if target_url and not is_menu_link_visible_for_user(user, target_url):
                continue
            models.append(model)

        app_url = app.get("app_url", "")
        app_label = app.get("app_label")
        if app_url and not is_menu_link_visible_for_user(user, app_url):
            if not models:
                continue

        if app_label and not models:
            continue

        filtered_apps.append({**app, "models": models})
    return filtered_apps


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
    last_menu_import = None
    try:
        from jidelnicek.models import MenuImportRun

        last_menu_import = MenuImportRun.objects.order_by("-started_at").first()
    except Exception:
        last_menu_import = None

    try:
        from licencovani.services import get_license_summary_cards

        license_summary = get_license_summary_cards()
    except Exception:
        license_summary = {
            "status": "invalid",
            "status_label": "Neplatná",
            "message": "Licenci se nepodařilo vyhodnotit.",
            "modules": [],
            "payload": {},
            "is_operational": False,
            "instance_id": "-",
            "enforced": False,
        }

    if license_summary["status"] == "active":
        license_tone = "good"
    elif license_summary["status"] in {"grace", "support"}:
        license_tone = "warning"
    else:
        license_tone = "danger"

    import_tone = "warning"
    import_value = "Žádný běh"
    import_hint = "Import jídelníčku ještě nebyl spuštěn."
    if last_menu_import:
        if last_menu_import.status == "success":
            import_tone = "good"
        elif last_menu_import.status == "failed":
            import_tone = "danger"
        import_value = f"{last_menu_import.get_source_display()} • {last_menu_import.get_status_display()}"
        import_hint = (
            f"{timezone.localtime(last_menu_import.started_at):%d.%m.%Y %H:%M}"
            + (f" • {last_menu_import.summary}" if last_menu_import.summary else "")
        )

    return {
        "cards": [
            {
                "label": "Poslední import jídelníčku",
                "value": import_value,
                "tone": import_tone,
                "hint": import_hint,
            },
            {
                "label": "Licence",
                "value": license_summary["status_label"],
                "tone": license_tone,
                "hint": license_summary["message"],
            },
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
            {
                "title": "Licenční stav",
                "items": [
                    f"Stav: {license_summary['status_label']}",
                    f"Zpráva: {license_summary['message']}",
                    f"Licencované moduly: {', '.join(license_summary['modules']) if license_summary['modules'] else 'žádné'}",
                    f"Vynucování: {'zapnuté' if license_summary['enforced'] else 'vypnuté'}",
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
    return f"Admin • {access.name} • {ADMIN_ACCESS_LEVEL_LABELS[level]}"


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


def get_group_level_for_access(group, access):
    if not group:
        return ""
    if access.control_groups.filter(pk=group.pk).exists():
        return "control"
    if access.write_groups.filter(pk=group.pk).exists():
        return "write"
    if access.view_groups.filter(pk=group.pk).exists():
        return "view"
    return ""


def set_group_level_for_access(group, access, level):
    access.view_groups.remove(group)
    access.write_groups.remove(group)
    access.control_groups.remove(group)
    if level == "view":
        access.view_groups.add(group)
    elif level == "write":
        access.write_groups.add(group)
    elif level == "control":
        access.control_groups.add(group)


def sync_operational_admin_roles(access_map=None, force=False):
    if access_map is None:
        access_map = {access.slug: access for access in AdminViewAccess.objects.all()}
    synced_groups = []

    for role in OPERATIONAL_ADMIN_ROLES:
        group, _ = Group.objects.get_or_create(name=get_operational_role_group_name(role))
        synced_groups.append(group)
        has_existing_assignments = any(
            get_group_level_for_access(group, area) for area in access_map.values()
        )
        if has_existing_assignments and not force:
            continue

        for area in access_map.values():
            set_group_level_for_access(group, area, "")

        for area_slug, level in role.get("assignments", {}).items():
            area = access_map.get(area_slug)
            if area:
                set_group_level_for_access(group, area, level)

    return synced_groups


def get_operational_roles_overview():
    access_by_slug = {
        access.slug: access
        for access in AdminViewAccess.objects.prefetch_related("view_groups", "write_groups", "control_groups").all()
    }
    groups_by_name = {
        group.name: group
        for group in Group.objects.filter(
            name__in=[get_operational_role_group_name(role) for role in OPERATIONAL_ADMIN_ROLES]
        )
    }
    overview = []
    for role in OPERATIONAL_ADMIN_ROLES:
        group_name = get_operational_role_group_name(role)
        group = groups_by_name.get(group_name)
        assignments = []
        for access in access_by_slug.values():
            level = get_group_level_for_access(group, access)
            if not level:
                continue
            assignments.append(
                {
                    "area_name": access.name,
                    "level": ADMIN_ACCESS_LEVEL_LABELS.get(level, level),
                    "tone": ADMIN_ACCESS_LEVEL_TONES.get(level, "neutral"),
                }
            )
        overview.append(
            {
                "name": role["name"],
                "description": role["description"],
                "best_for": role.get("best_for", ""),
                "scope_note": role.get("scope_note", ""),
                "group": group,
                "group_name": group_name,
                "assignments": assignments,
            }
        )
    return overview


def build_permissions_matrix():
    sync_registered_tasks()
    sync_managed_modules()
    sync_admin_view_accesses()
    ensure_role_menu_visibility_profiles()

    areas = list(
        AdminViewAccess.objects.prefetch_related("view_groups", "write_groups", "control_groups").order_by("name")
    )
    groups_by_name = {
        group.name: group
        for group in Group.objects.filter(
            name__in=[get_operational_role_group_name(role) for role in OPERATIONAL_ADMIN_ROLES]
        ).prefetch_related("user_set")
    }
    menu_catalog = build_admin_menu_catalog()
    rows = []
    for role in OPERATIONAL_ADMIN_ROLES:
        group_name = get_operational_role_group_name(role)
        group = groups_by_name.get(group_name)
        hidden_area_slugs = set()
        hidden_app_labels = set()
        hidden_menu_item_keys = set()
        if group and hasattr(group, "admin_menu_visibility"):
            hidden_area_slugs = set(group.admin_menu_visibility.hidden_area_slugs or [])
            hidden_app_labels = set(group.admin_menu_visibility.hidden_app_labels or [])
            hidden_menu_item_keys = set(group.admin_menu_visibility.hidden_menu_item_keys or [])
        assignments = []
        for area in areas:
            level = get_group_level_for_access(group, area)
            assignments.append(
                {
                    "area_slug": area.slug,
                    "area_name": area.name,
                    "level": level,
                    "level_label": ADMIN_ACCESS_LEVEL_LABELS.get(level, "Bez přístupu"),
                    "tone": ADMIN_ACCESS_LEVEL_TONES.get(level, "neutral"),
                    "field_name": f"matrix__{role['slug']}__{area.slug}",
                    "visibility_field_name": f"visibility__{role['slug']}__{area.slug}",
                    "visible": area.slug not in hidden_area_slugs,
                }
            )
        visibility_apps = []
        for app_entry in menu_catalog:
            item_rows = []
            for item in app_entry["items"]:
                item_rows.append(
                    {
                        **item,
                        "field_name": f"item_visibility__{role['slug']}__{item['key']}",
                        "visible": item["key"] not in hidden_menu_item_keys,
                    }
                )
            visibility_apps.append(
                {
                    "app_label": app_entry["app_label"],
                    "app_name": app_entry["app_name"],
                    "field_name": f"app_visibility__{role['slug']}__{app_entry['app_label']}",
                    "visible": app_entry["app_label"] not in hidden_app_labels,
                    "items": item_rows,
                }
            )
        user_count = group.user_set.count() if group else 0
        users_preview = (
            list(group.user_set.order_by("username").values_list("username", flat=True)[:5]) if group else []
        )
        rows.append(
            {
                "slug": role["slug"],
                "name": role["name"],
                "description": role["description"],
                "best_for": role.get("best_for", ""),
                "scope_note": role.get("scope_note", ""),
                "group_name": group_name,
                "group": group,
                "user_count": user_count,
                "users_preview": users_preview,
                "assignments": assignments,
                "visibility_apps": visibility_apps,
            }
        )

    return {
        "areas": areas,
        "rows": rows,
        "choices": ADMIN_ACCESS_LEVEL_CHOICES_WITH_NONE,
        "summary_cards": [
            {
                "label": "Oblasti systému",
                "value": len(areas),
                "tone": "good",
                "hint": "Samostatně řízené části adminu a provozu.",
            },
            {
                "label": "Provozní role",
                "value": len(rows),
                "tone": "good",
                "hint": "Přednastavené role připravené k přiřazení uživatelům.",
            },
            {
                "label": "Uživatelé v rolích",
                "value": sum(row["user_count"] for row in rows),
                "tone": "neutral",
                "hint": "Počet uživatelů, kteří už některou provozní roli používají.",
            },
        ],
        "visibility_summary_cards": [
            {
                "label": "Viditelné appky",
                "value": sum(1 for row in rows for app in row["visibility_apps"] if app["visible"]),
                "tone": "good",
                "hint": "Kolik app sekcí se po přihlášení role v adminu opravdu ukáže.",
            },
            {
                "label": "Skryté položky",
                "value": sum(1 for row in rows for app in row["visibility_apps"] for item in app["items"] if not item["visible"]),
                "tone": "neutral",
                "hint": "Jemně skryté modely a rychlé odkazy v rámci povolených appek.",
            },
            {
                "label": "Režim",
                "value": "Pouze UI",
                "tone": "warning",
                "hint": "Skrytí položky neblokuje URL ani běh aplikace. Jde jen o čistotu adminu.",
            },
        ],
    }


def update_permissions_matrix(post_data):
    matrix = build_permissions_matrix()
    areas_by_slug = {area.slug: area for area in matrix["areas"]}
    changed = 0

    for role in OPERATIONAL_ADMIN_ROLES:
        group, _ = Group.objects.get_or_create(name=get_operational_role_group_name(role))
        for area_slug, area in areas_by_slug.items():
            field_name = f"matrix__{role['slug']}__{area_slug}"
            new_level = (post_data.get(field_name) or "").strip()
            if new_level not in {"", "view", "write", "control"}:
                continue
            current_level = get_group_level_for_access(group, area)
            if current_level == new_level:
                continue
            set_group_level_for_access(group, area, new_level)
            changed += 1

    sync_admin_access_permissions()
    return changed


def update_role_menu_visibility(post_data):
    matrix = build_permissions_matrix()
    changed = 0

    for role_row in matrix["rows"]:
        role_slug = role_row["slug"]
        role = next(role for role in OPERATIONAL_ADMIN_ROLES if role["slug"] == role_slug)
        group, _ = Group.objects.get_or_create(name=get_operational_role_group_name(role))
        profile, _ = AdminRoleMenuVisibility.objects.get_or_create(role_group=group)
        hidden_area_slugs = set(profile.hidden_area_slugs or [])
        hidden_app_labels = set(profile.hidden_app_labels or [])
        hidden_menu_item_keys = set(profile.hidden_menu_item_keys or [])
        area_field_names = {f"visibility__{role_slug}__{area.slug}" for area in matrix["areas"]}
        if any(field_name in post_data for field_name in area_field_names):
            new_hidden_area_slugs = set()
            for area in matrix["areas"]:
                field_name = f"visibility__{role_slug}__{area.slug}"
                is_visible = post_data.get(field_name) == "on"
                if not is_visible:
                    new_hidden_area_slugs.add(area.slug)
        else:
            new_hidden_area_slugs = set(hidden_area_slugs)

        new_hidden_app_labels = set()
        new_hidden_menu_item_keys = set()
        for app_entry in role_row["visibility_apps"]:
            app_visible = post_data.get(app_entry["field_name"]) == "on"
            if not app_visible:
                new_hidden_app_labels.add(app_entry["app_label"])
            for item in app_entry["items"]:
                item_visible = post_data.get(item["field_name"]) == "on"
                if not item_visible:
                    new_hidden_menu_item_keys.add(item["key"])

        if (
            hidden_area_slugs != new_hidden_area_slugs
            or hidden_app_labels != new_hidden_app_labels
            or hidden_menu_item_keys != new_hidden_menu_item_keys
        ):
            profile.hidden_area_slugs = sorted(new_hidden_area_slugs)
            profile.hidden_app_labels = sorted(new_hidden_app_labels)
            profile.hidden_menu_item_keys = sorted(new_hidden_menu_item_keys)
            profile.save(
                update_fields=[
                    "hidden_area_slugs",
                    "hidden_app_labels",
                    "hidden_menu_item_keys",
                    "updated_at",
                ]
            )
            changed += 1

    return changed
