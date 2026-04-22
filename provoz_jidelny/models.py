from vydej.models import VydejSettings


class ProvozniDashboard(VydejSettings):
    class Meta:
        proxy = True
        app_label = "provoz_jidelny"
        verbose_name = "Provozní dashboard"
        verbose_name_plural = "Provozní dashboard"


class NastaveniVydaje(VydejSettings):
    class Meta:
        proxy = True
        app_label = "provoz_jidelny"
        verbose_name = "Nastavení výdeje"
        verbose_name_plural = "Nastavení výdeje"

