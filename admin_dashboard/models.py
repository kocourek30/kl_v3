from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models
from django.utils import timezone


class DashboardTask(models.Model):
    CATEGORY_CHOICES = (
        ("users", "Uživatelé"),
        ("orders", "Objednávky"),
        ("menu", "Jídelníček"),
        ("billing", "Fakturace"),
        ("warehouse", "Sklad"),
        ("system", "Systém"),
        ("demo", "Demo / seed"),
    )

    slug = models.SlugField(unique=True, verbose_name="Slug úlohy")
    name = models.CharField(max_length=200, verbose_name="Název")
    category = models.CharField(
        max_length=40,
        choices=CATEGORY_CHOICES,
        default="system",
        verbose_name="Kategorie",
    )
    command_name = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Management command",
    )
    description = models.TextField(blank=True, verbose_name="Popis")
    expected_interval_hours = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Doporučený interval (h)",
    )
    default_options = models.JSONField(default=dict, blank=True, verbose_name="Výchozí parametry")
    is_enabled = models.BooleanField(default=True, verbose_name="Aktivní")
    allow_manual_run = models.BooleanField(default=True, verbose_name="Lze spustit ručně")
    is_quick_link = models.BooleanField(default=False, verbose_name="Pouze rychlý odkaz")
    target_url_name = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Cílový URL name",
    )
    target_url = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Cílová URL",
    )
    notes = models.TextField(blank=True, verbose_name="Poznámky")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Úloha dashboardu"
        verbose_name_plural = "Úlohy dashboardu"
        ordering = ("category", "name")

    def __str__(self):
        return self.name

    @property
    def latest_run(self):
        return self.runs.order_by("-started_at").first()

    @property
    def last_successful_run(self):
        return self.runs.filter(status=TaskRun.STATUS_SUCCESS).order_by("-started_at").first()

    def is_stale(self):
        if not self.expected_interval_hours:
            return False
        latest_success = self.last_successful_run
        if not latest_success:
            return True
        return latest_success.finished_at < timezone.now() - timedelta(hours=self.expected_interval_hours)


class TaskRun(models.Model):
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"
    STATUS_CHOICES = (
        (STATUS_RUNNING, "Spuštěno"),
        (STATUS_SUCCESS, "Úspěch"),
        (STATUS_FAILED, "Chyba"),
        (STATUS_SKIPPED, "Přeskočeno"),
    )

    SOURCE_MANUAL = "manual"
    SOURCE_SYSTEM = "system"
    SOURCE_CHOICES = (
        (SOURCE_MANUAL, "Ruční"),
        (SOURCE_SYSTEM, "Systémová"),
    )

    task = models.ForeignKey(
        DashboardTask,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
        verbose_name="Úloha",
    )
    command_name = models.CharField(max_length=200, blank=True, verbose_name="Command")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    trigger_source = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        default=SOURCE_MANUAL,
        verbose_name="Zdroj spuštění",
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Spustil",
    )
    started_at = models.DateTimeField(default=timezone.now, verbose_name="Začátek")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="Konec")
    duration_seconds = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name="Doba běhu (s)",
    )
    summary = models.CharField(max_length=255, blank=True, verbose_name="Shrnutí")
    stdout = models.TextField(blank=True, verbose_name="Standardní výstup")
    stderr = models.TextField(blank=True, verbose_name="Chyby / stderr")
    metadata = models.JSONField(default=dict, blank=True, verbose_name="Metadata")

    class Meta:
        verbose_name = "Běh úlohy"
        verbose_name_plural = "Běhy úloh"
        ordering = ("-started_at",)

    def __str__(self):
        task_name = self.task.name if self.task else self.command_name or "Neznámá úloha"
        return f"{task_name} • {self.get_status_display()} • {self.started_at:%d.%m.%Y %H:%M}"


class AppModuleToggle(models.Model):
    slug = models.SlugField(unique=True, verbose_name="Slug modulu")
    name = models.CharField(max_length=200, verbose_name="Název modulu")
    description = models.TextField(blank=True, verbose_name="Popis")
    app_labels = models.JSONField(default=list, blank=True, verbose_name="Admin app labels")
    route_prefixes = models.JSONField(default=list, blank=True, verbose_name="Route prefixy")
    enabled = models.BooleanField(default=True, verbose_name="Povoleno")
    notes = models.TextField(blank=True, verbose_name="Poznámky")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Přepínač modulu"
        verbose_name_plural = "Přepínače modulů"
        ordering = ("name",)

    def __str__(self):
        return self.name


class AdminViewAccess(models.Model):
    slug = models.SlugField(unique=True, verbose_name="Slug oblasti")
    name = models.CharField(max_length=200, verbose_name="Název oblasti")
    description = models.TextField(blank=True, verbose_name="Popis")
    app_labels = models.JSONField(default=list, blank=True, verbose_name="Admin app labels")
    route_prefixes = models.JSONField(default=list, blank=True, verbose_name="Route prefixy")
    view_groups = models.ManyToManyField(
        Group,
        blank=True,
        verbose_name="Skupiny pro náhled",
        related_name="admin_view_accesses_view",
    )
    write_groups = models.ManyToManyField(
        Group,
        blank=True,
        verbose_name="Skupiny pro správu",
        related_name="admin_view_accesses_write",
    )
    control_groups = models.ManyToManyField(
        Group,
        blank=True,
        verbose_name="Skupiny pro plnou kontrolu",
        related_name="admin_view_accesses_control",
    )
    notes = models.TextField(blank=True, verbose_name="Poznámky")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Přístup do admin oblasti"
        verbose_name_plural = "Přístupy do admin oblastí"
        ordering = ("name",)

    def __str__(self):
        return self.name

    def has_group_restrictions(self):
        return self.view_groups.exists() or self.write_groups.exists() or self.control_groups.exists()
