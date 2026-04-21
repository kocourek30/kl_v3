from django.apps import AppConfig


class AdminDashboardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "admin_dashboard"
    verbose_name = "Admin dashboard"

    def ready(self):
        from .admin_patches import patch_admin_site

        patch_admin_site()
