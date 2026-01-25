from django.db import models
from jidelnicek.models import DruhJidla

class VydajiciCas(models.Model):
    druh_jidla = models.ForeignKey(DruhJidla, on_delete=models.CASCADE, related_name="vydajici_casy")
    cas_od = models.TimeField(verbose_name="Čas od")
    cas_do = models.TimeField(verbose_name="Čas do")

    class Meta:
        unique_together = ("druh_jidla", "cas_od", "cas_do")
        verbose_name = "Výdejní čas"
        verbose_name_plural = "Výdejní časy"

    def __str__(self):
        return f"{self.druh_jidla} {self.cas_od.strftime('%H:%M')}–{self.cas_do.strftime('%H:%M')}"


class VydejSettings(models.Model):
    timeout_seconds = models.PositiveIntegerField(
        default=20,
        verbose_name="Timeout výdeje (sekundy)",
        help_text="Po kolika sekundách od nalezení objednávky se automaticky vydá."
    )

    class Meta:
        verbose_name = "Nastavení výdeje"
        verbose_name_plural = "Nastavení výdeje"

    def __str__(self):
        return f"Timeout: {self.timeout_seconds}s"

