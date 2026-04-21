from django.contrib.auth.models import Group
from django.contrib.auth import get_user_model
from django.test import TestCase
from jazzmin.templatetags import jazzmin as jazzmin_tags

from .models import AdminViewAccess, AppModuleToggle, DashboardTask, TaskRun
from .services import (
    get_restricted_admin_app_labels_for_user,
    is_menu_link_visible_for_user,
    run_dashboard_task,
    sync_admin_view_accesses,
    sync_managed_modules,
    sync_operational_admin_roles,
    sync_registered_tasks,
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
        self.assertContains(response, "Nemáte přístup do této admin oblasti", status_code=403)

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

        self.assertTrue(menu_access.control_groups.filter(pk=role_group.pk).exists())
        self.assertTrue(reports_access.write_groups.filter(pk=role_group.pk).exists())

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
