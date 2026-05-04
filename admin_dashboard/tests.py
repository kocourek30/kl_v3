from django.contrib.auth.models import Group
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.template import Context
from django.test.client import RequestFactory
from django.contrib import admin
from jazzmin.templatetags import jazzmin as jazzmin_tags

from .models import AdminRoleMenuVisibility, AdminViewAccess, AppModuleToggle, DashboardTask, TaskRun
from .services import (
    build_permissions_matrix,
    get_blocked_admin_area_for_user_path,
    get_hidden_app_labels_for_user,
    get_hidden_area_slugs_for_user,
    get_hidden_menu_item_keys_for_user,
    get_restricted_admin_app_labels_for_user,
    is_menu_link_visible_for_user,
    run_dashboard_task,
    sync_admin_view_accesses,
    sync_managed_modules,
    sync_operational_admin_roles,
    sync_registered_tasks,
    update_permissions_matrix,
    update_role_menu_visibility,
)


class AdminDashboardTaskTests(TestCase):
    def test_sync_registered_tasks_creates_seeded_registry(self):
        sync_registered_tasks()

        self.assertTrue(DashboardTask.objects.filter(slug="reset-monthly-accounts").exists())
        self.assertTrue(DashboardTask.objects.filter(slug="menu-import-link", is_quick_link=True).exists())

    def test_run_dashboard_task_records_successful_audit(self):
        sync_registered_tasks()
        task = DashboardTask.objects.get(slug="mark-unpicked-orders")
        user = get_user_model().objects.create_superuser(
            username="admin-runner",
            password="test12345",
            email="admin@example.com",
        )

        run = run_dashboard_task(task, triggered_by=user)

        self.assertEqual(run.status, TaskRun.STATUS_SUCCESS)
        self.assertEqual(run.task, task)
        self.assertEqual(run.triggered_by, user)
        self.assertTrue(TaskRun.objects.filter(task=task).exists())

    def test_quick_link_task_is_logged_as_skipped(self):
        sync_registered_tasks()
        task = DashboardTask.objects.get(slug="menu-import-link")

        run = run_dashboard_task(task)

        self.assertEqual(run.status, TaskRun.STATUS_SKIPPED)

    def test_sync_managed_modules_creates_default_toggles(self):
        sync_managed_modules()

        self.assertTrue(AppModuleToggle.objects.filter(slug="sklad").exists())
        self.assertTrue(AppModuleToggle.objects.filter(slug="pokladna").exists())

    def test_sync_admin_view_accesses_creates_seeded_areas(self):
        sync_admin_view_accesses()

        self.assertTrue(AdminViewAccess.objects.filter(slug="menu-admin").exists())
        self.assertTrue(AdminViewAccess.objects.filter(slug="orders-admin").exists())
        orders_access = AdminViewAccess.objects.get(slug="orders-admin")

        self.assertTrue(orders_access.view_groups.exists())
        self.assertTrue(orders_access.write_groups.exists())
        self.assertTrue(orders_access.control_groups.exists())

    def test_restricted_admin_labels_respect_django_groups(self):
        sync_admin_view_accesses()
        orders_access = AdminViewAccess.objects.get(slug="orders-admin")
        orders_access.view_groups.clear()
        orders_access.write_groups.clear()
        orders_access.control_groups.clear()
        view_group = Group.objects.create(name="Objednavky nahled")
        orders_access.view_groups.add(view_group)

        User = get_user_model()
        blocked_user = User.objects.create_user(
            username="blocked-admin",
            password="test12345",
            is_staff=True,
        )
        allowed_user = User.objects.create_user(
            username="allowed-admin",
            password="test12345",
            is_staff=True,
        )
        allowed_user.groups.add(view_group)

        blocked_labels = get_restricted_admin_app_labels_for_user(blocked_user)
        allowed_labels = get_restricted_admin_app_labels_for_user(allowed_user)

        self.assertIn("objednavky", blocked_labels)
        self.assertNotIn("objednavky", allowed_labels)

    def test_middleware_blocks_staff_user_without_required_group(self):
        sync_admin_view_accesses()
        orders_access = AdminViewAccess.objects.get(slug="orders-admin")
        orders_access.view_groups.clear()
        orders_access.write_groups.clear()
        orders_access.control_groups.clear()
        allowed_group = Group.objects.create(name="Objednavky manager")
        orders_access.control_groups.add(allowed_group)

        user = get_user_model().objects.create_user(
            username="staff-no-orders",
            password="test12345",
            is_staff=True,
        )
        self.client.force_login(user)

        response = self.client.get("/admin/objednavky/")

        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            "Nemáte přístup do této admin oblasti" in response.content.decode("utf-8")
            or "Administrace je po skončení licence" in response.content.decode("utf-8")
        )

    def test_write_group_gets_change_permissions_but_not_delete(self):
        sync_admin_view_accesses()
        orders_access = AdminViewAccess.objects.get(slug="orders-admin")
        write_group = orders_access.write_groups.first()

        permission_codenames = set(write_group.permissions.values_list("codename", flat=True))

        self.assertIn("view_order", permission_codenames)
        self.assertIn("change_order", permission_codenames)
        self.assertIn("add_order", permission_codenames)
        self.assertNotIn("delete_order", permission_codenames)

    def test_operational_role_group_is_created_and_mapped(self):
        sync_admin_view_accesses()
        sync_operational_admin_roles()

        role_group = Group.objects.get(name="Role • Vedoucí jídelny")
        menu_access = AdminViewAccess.objects.get(slug="menu-admin")
        reports_access = AdminViewAccess.objects.get(slug="reports-admin")
        surveys_access = AdminViewAccess.objects.get(slug="surveys-admin")
        licensing_access = AdminViewAccess.objects.get(slug="licensing-admin")

        self.assertTrue(menu_access.control_groups.filter(pk=role_group.pk).exists())
        self.assertTrue(reports_access.control_groups.filter(pk=role_group.pk).exists())
        self.assertTrue(surveys_access.control_groups.filter(pk=role_group.pk).exists())
        self.assertTrue(licensing_access.view_groups.filter(pk=role_group.pk).exists())

    def test_sync_admin_view_accesses_does_not_overwrite_custom_role_assignments(self):
        sync_admin_view_accesses()
        role_group = Group.objects.get(name="Role • Vedoucí jídelny")
        reports_access = AdminViewAccess.objects.get(slug="reports-admin")
        finance_access = AdminViewAccess.objects.get(slug="finance-admin")

        reports_access.write_groups.remove(role_group)
        reports_access.control_groups.add(role_group)
        finance_access.write_groups.remove(role_group)
        finance_access.control_groups.add(role_group)

        sync_admin_view_accesses()

        reports_access.refresh_from_db()
        finance_access.refresh_from_db()
        self.assertTrue(reports_access.control_groups.filter(pk=role_group.pk).exists())
        self.assertTrue(finance_access.control_groups.filter(pk=role_group.pk).exists())

    def test_update_permissions_matrix_updates_role_assignments(self):
        sync_admin_view_accesses()

        changed = update_permissions_matrix(
            {
                "matrix__vydej-jidel__canteen-operations-admin": "control",
                "matrix__vydej-jidel__issuance-admin": "write",
                "matrix__vydej-jidel__reports-admin": "view",
            }
        )

        self.assertGreaterEqual(changed, 1)
        role_group = Group.objects.get(name="Role • Výdej jídel")
        operations_access = AdminViewAccess.objects.get(slug="canteen-operations-admin")
        issuance_access = AdminViewAccess.objects.get(slug="issuance-admin")
        reports_access = AdminViewAccess.objects.get(slug="reports-admin")
        self.assertTrue(operations_access.control_groups.filter(pk=role_group.pk).exists())
        self.assertTrue(issuance_access.write_groups.filter(pk=role_group.pk).exists())
        self.assertTrue(reports_access.view_groups.filter(pk=role_group.pk).exists())

    def test_permissions_matrix_page_renders(self):
        user = get_user_model().objects.create_superuser(
            username="matrix-admin",
            password="test12345",
            email="matrix@example.com",
        )
        self.client.force_login(user)

        response = self.client.get("/admin/admin_dashboard/dashboardtask/permissions-matrix/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Centrální správa přístupů")

    def test_permissions_matrix_can_reset_recommended_roles(self):
        sync_admin_view_accesses()
        role_group = Group.objects.get(name="Role • Pokladní")
        reports_access = AdminViewAccess.objects.get(slug="reports-admin")
        cashdesk_access = AdminViewAccess.objects.get(slug="cashdesk-admin")
        reports_access.view_groups.remove(role_group)
        reports_access.control_groups.add(role_group)
        cashdesk_access.control_groups.remove(role_group)
        cashdesk_access.view_groups.add(role_group)

        user = get_user_model().objects.create_superuser(
            username="matrix-reset",
            password="test12345",
            email="matrix-reset@example.com",
        )
        self.client.force_login(user)

        response = self.client.post(
            "/admin/admin_dashboard/dashboardtask/permissions-matrix/",
            {"matrix_action": "reset_defaults"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        reports_access.refresh_from_db()
        cashdesk_access.refresh_from_db()
        self.assertTrue(reports_access.view_groups.filter(pk=role_group.pk).exists())
        self.assertTrue(cashdesk_access.control_groups.filter(pk=role_group.pk).exists())

    def test_menu_link_visibility_respects_module_toggle(self):
        sync_managed_modules()
        finance_module = AppModuleToggle.objects.get(slug="finance")
        finance_module.enabled = False
        finance_module.save(update_fields=["enabled"])

        user = get_user_model().objects.create_user(
            username="finance-hidden",
            password="test12345",
            is_staff=True,
        )

        self.assertFalse(is_menu_link_visible_for_user(user, "/admin/finance/financnidashboard/"))
        self.assertTrue(is_menu_link_visible_for_user(user, "/admin/reporty/reportdummy/"))

    def test_top_menu_hides_restricted_links(self):
        sync_managed_modules()
        sync_admin_view_accesses()
        finance_module = AppModuleToggle.objects.get(slug="finance")
        finance_module.enabled = False
        finance_module.save(update_fields=["enabled"])

        reports_access = AdminViewAccess.objects.get(slug="reports-admin")
        reports_access.view_groups.clear()
        reports_access.write_groups.clear()
        reports_access.control_groups.clear()
        allowed_group = Group.objects.create(name="Reporty nahled")
        reports_access.view_groups.add(allowed_group)

        user = get_user_model().objects.create_user(
            username="restricted-topmenu",
            password="test12345",
            is_staff=True,
        )

        top_menu = jazzmin_tags.get_top_menu(user, admin_site="admin")
        top_menu_names = [item["name"] for item in top_menu]

        self.assertNotIn("Reporty", top_menu_names)
        self.assertNotIn("Finance", top_menu_names)

    def test_vedouci_role_hides_auth_and_selected_user_entries(self):
        sync_admin_view_accesses(force_role_defaults=True)
        role_group = Group.objects.get(name="Role • Vedoucí jídelny")
        user = get_user_model().objects.create_user(
            username="vedouci-sidebar",
            password="test12345",
            is_staff=True,
        )
        user.groups.add(role_group)

        request = RequestFactory().get("/admin/")
        request.user = user

        app_list = admin.site.get_app_list(request)
        users_app = next(app for app in app_list if app.get("app_label") == "users")
        users_model_names = [model["name"] for model in users_app["models"]]

        self.assertIn("Uživatelé", users_model_names)
        self.assertIn("Vklady na konta", users_model_names)
        self.assertNotIn("Stravovací skupiny", users_model_names)

        ctx = Context({"available_apps": app_list, "request": request, "user": user})
        side_menu = jazzmin_tags.get_side_menu(ctx)
        users_side = next(item for item in side_menu if item.get("app_label") == "users")
        users_side_names = [item["name"] for item in users_side["models"]]
        side_app_labels = [item.get("app_label") for item in side_menu]

        self.assertNotIn("Nulování kont", users_side_names)
        self.assertNotIn("auth", side_app_labels)

    def test_update_role_menu_visibility_hides_area_in_menu_only(self):
        sync_admin_view_accesses(force_role_defaults=True)
        changed = update_role_menu_visibility(
            {
                "visibility__vedouci-jidelny__reports-admin": "",
                "app_visibility__vedouci-jidelny__reporty": "on",
                "visibility__vedouci-jidelny__menu-admin": "on",
                "visibility__vedouci-jidelny__orders-admin": "on",
                "app_visibility__vedouci-jidelny__users": "on",
                "item_visibility__vedouci-jidelny__model:users.customuser": "on",
            }
        )

        self.assertGreaterEqual(changed, 1)
        role_group = Group.objects.get(name="Role • Vedoucí jídelny")
        profile = AdminRoleMenuVisibility.objects.get(role_group=role_group)
        self.assertIn("reports-admin", profile.hidden_area_slugs)

        user = get_user_model().objects.create_user(
            username="vedouci-visibility",
            password="test12345",
            is_staff=True,
        )
        user.groups.add(role_group)

        hidden = get_hidden_area_slugs_for_user(user)
        self.assertIn("reports-admin", hidden)
        self.assertFalse(is_menu_link_visible_for_user(user, "/admin/reporty/reportdummy/"))

    def test_menu_visibility_does_not_block_area_resolution(self):
        sync_admin_view_accesses(force_role_defaults=True)
        update_permissions_matrix(
            {
                "matrix__vedouci-jidelny__reports-admin": "control",
            }
        )
        update_role_menu_visibility(
            {
                "visibility__vedouci-jidelny__reports-admin": "",
                "visibility__vedouci-jidelny__menu-admin": "on",
                "visibility__vedouci-jidelny__orders-admin": "on",
                "visibility__vedouci-jidelny__issuance-admin": "on",
                "visibility__vedouci-jidelny__canteen-settings-admin": "on",
                "visibility__vedouci-jidelny__surveys-admin": "on",
                "visibility__vedouci-jidelny__pricing-admin": "on",
                "visibility__vedouci-jidelny__finance-admin": "on",
                "visibility__vedouci-jidelny__billing-admin": "on",
                "visibility__vedouci-jidelny__users-admin": "on",
                "visibility__vedouci-jidelny__licensing-admin": "on",
                "visibility__vedouci-jidelny__cashdesk-admin": "on",
                "visibility__vedouci-jidelny__warehouse-admin": "on",
                "visibility__vedouci-jidelny__canteen-operations-admin": "on",
                "app_visibility__vedouci-jidelny__reporty": "on",
            }
        )
        role_group = Group.objects.get(name="Role • Vedoucí jídelny")
        user = get_user_model().objects.create_user(
            username="vedouci-no-block",
            password="test12345",
            is_staff=True,
        )
        user.groups.add(role_group)

        self.assertFalse(is_menu_link_visible_for_user(user, "/admin/reporty/reportdummy/"))
        self.assertIsNone(get_blocked_admin_area_for_user_path(user, "/admin/reporty/reportdummy/"))

    def test_permissions_matrix_page_saves_visibility_preferences(self):
        user = get_user_model().objects.create_superuser(
            username="matrix-visibility",
            password="test12345",
            email="matrix-visibility@example.com",
        )
        self.client.force_login(user)

        response = self.client.post(
            "/admin/admin_dashboard/dashboardtask/permissions-matrix/",
            {
                "matrix_action": "save_visibility",
                "visibility__vedouci-jidelny__reports-admin": "",
                "app_visibility__vedouci-jidelny__reporty": "on",
                "visibility__vedouci-jidelny__menu-admin": "on",
                "visibility__vedouci-jidelny__orders-admin": "on",
                "app_visibility__vedouci-jidelny__users": "on",
                "item_visibility__vedouci-jidelny__model:users.customuser": "on",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        role_group = Group.objects.get(name="Role • Vedoucí jídelny")
        profile = AdminRoleMenuVisibility.objects.get(role_group=role_group)
        self.assertIn("reports-admin", profile.hidden_area_slugs)

    def test_role_menu_visibility_can_hide_single_model_inside_visible_app(self):
        sync_admin_view_accesses(force_role_defaults=True)
        changed = update_role_menu_visibility(
            {
                "app_visibility__vedouci-jidelny__users": "on",
                "item_visibility__vedouci-jidelny__model:users.customuser": "on",
                "item_visibility__vedouci-jidelny__model:users.stravovaciskupina": "",
                "item_visibility__vedouci-jidelny__custom:users:/admin/users/vklad/nulovani-konta/": "",
            }
        )

        self.assertGreaterEqual(changed, 1)
        role_group = Group.objects.get(name="Role • Vedoucí jídelny")
        profile = AdminRoleMenuVisibility.objects.get(role_group=role_group)
        self.assertNotIn("users", profile.hidden_app_labels)
        self.assertIn("model:users.stravovaciskupina", profile.hidden_menu_item_keys)
        self.assertIn("custom:users:/admin/users/vklad/nulovani-konta/", profile.hidden_menu_item_keys)

        user = get_user_model().objects.create_user(
            username="vedouci-hidden-user-items",
            password="test12345",
            is_staff=True,
        )
        user.groups.add(role_group)

        self.assertIn("model:users.stravovaciskupina", get_hidden_menu_item_keys_for_user(user))
        self.assertFalse(is_menu_link_visible_for_user(user, "/admin/users/stravovaciskupina/"))
        self.assertTrue(is_menu_link_visible_for_user(user, "/admin/users/customuser/"))

    def test_role_menu_visibility_can_hide_whole_app(self):
        sync_admin_view_accesses(force_role_defaults=True)
        update_role_menu_visibility(
            {
                "app_visibility__vedouci-jidelny__ankety": "",
                "app_visibility__vedouci-jidelny__users": "on",
                "item_visibility__vedouci-jidelny__model:users.customuser": "on",
            }
        )
        role_group = Group.objects.get(name="Role • Vedoucí jídelny")
        user = get_user_model().objects.create_user(
            username="vedouci-hidden-surveys",
            password="test12345",
            is_staff=True,
        )
        user.groups.add(role_group)

        self.assertIn("ankety", get_hidden_app_labels_for_user(user))
        self.assertFalse(is_menu_link_visible_for_user(user, "/admin/ankety/hodnocenijidla/"))
