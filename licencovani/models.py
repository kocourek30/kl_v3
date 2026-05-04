import uuid

from django.conf import settings
from django.db import models


class LicenseConfig(models.Model):
    STATUS_MISSING = "missing"
    STATUS_ACTIVE = "active"
    STATUS_GRACE = "grace"
    STATUS_SUPPORT = "support"
    STATUS_INVALID = "invalid"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = (
        (STATUS_MISSING, "Bez licence"),
        (STATUS_ACTIVE, "Aktivní"),
        (STATUS_GRACE, "V ochranné lhůtě"),
        (STATUS_SUPPORT, "Aktivní bez podpory"),
        (STATUS_INVALID, "Neplatná"),
        (STATUS_EXPIRED, "Propadlá"),
    )

    singleton_key = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    instance_id = models.UUIDField(default=uuid.uuid4, editable=False, verbose_name="ID instalace")
    license_blob = models.TextField(blank=True, verbose_name="Podepsaná licence")
    license_id = models.CharField(max_length=120, blank=True, verbose_name="ID licence")
    customer_name = models.CharField(max_length=255, blank=True, verbose_name="Zákazník")
    organization_name = models.CharField(max_length=255, blank=True, verbose_name="Organizace")
    license_type = models.CharField(max_length=60, blank=True, verbose_name="Typ licence")
    valid_from = models.DateField(null=True, blank=True, verbose_name="Platná od")
    valid_until = models.DateField(null=True, blank=True, verbose_name="Platná do")
    support_until = models.DateField(null=True, blank=True, verbose_name="Podpora do")
    last_verified_at = models.DateTimeField(null=True, blank=True, verbose_name="Poslední ověření")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_MISSING,
        verbose_name="Stav licence",
    )
    status_message = models.TextField(blank=True, verbose_name="Stavová zpráva")
    cached_payload = models.JSONField(default=dict, blank=True, verbose_name="Obsah licence")
    activated_at = models.DateTimeField(null=True, blank=True, verbose_name="Aktivováno")
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name="Aktivoval",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Licence aplikace"
        verbose_name_plural = "Licence aplikace"

    def __str__(self):
        if self.customer_name:
            return f"Licence • {self.customer_name}"
        return "Licence aplikace"


class LicenseEvent(models.Model):
    EVENT_VERIFY = "verify"
    EVENT_ACTIVATE = "activate"
    EVENT_REJECT = "reject"
    EVENT_STATUS = "status"
    EVENT_CHOICES = (
        (EVENT_VERIFY, "Ověření"),
        (EVENT_ACTIVATE, "Aktivace"),
        (EVENT_REJECT, "Odmítnutí"),
        (EVENT_STATUS, "Stav"),
    )

    config = models.ForeignKey(
        LicenseConfig,
        on_delete=models.CASCADE,
        related_name="events",
        verbose_name="Licence",
    )
    event_type = models.CharField(max_length=20, choices=EVENT_CHOICES, verbose_name="Typ")
    status = models.CharField(max_length=20, blank=True, verbose_name="Stav")
    message = models.CharField(max_length=255, blank=True, verbose_name="Zpráva")
    details = models.JSONField(default=dict, blank=True, verbose_name="Detaily")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name="Uživatel",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Vytvořeno")

    class Meta:
        verbose_name = "Událost licence"
        verbose_name_plural = "Události licence"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.get_event_type_display()} • {self.created_at:%d.%m.%Y %H:%M}"

