from django import forms
from django.utils import timezone
from users.models import StravovaciSkupina


class SpotrebniKosForm(forms.Form):
    PERIOD_MONTH = "month"
    PERIOD_RANGE = "range"

    PERIOD_CHOICES = [
        (PERIOD_MONTH, "Podle měsíce"),
        (PERIOD_RANGE, "Vlastní období"),
    ]

    period_type = forms.ChoiceField(
        label="Typ období",
        choices=PERIOD_CHOICES,
        initial=PERIOD_MONTH,
        widget=forms.RadioSelect,
    )

    year = forms.IntegerField(
        label="Rok",
        initial=timezone.now().year,
        min_value=2000,
        max_value=2100,
    )

    month = forms.IntegerField(
        label="Měsíc",
        min_value=1,
        max_value=12,
        initial=timezone.now().month,
    )

    date_from = forms.DateField(
        label="Od data",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    date_to = forms.DateField(
        label="Do data",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    stravovaci_skupina = forms.ModelChoiceField(
        label="Stravovací skupina",
        queryset=StravovaciSkupina.objects.all(),
        required=False,
        help_text="Volitelné – bez výběru se počítá za všechny skupiny.",
    )

    def clean(self):
        cleaned = super().clean()
        period_type = cleaned.get("period_type")

        if period_type == self.PERIOD_RANGE:
            d_from = cleaned.get("date_from")
            d_to = cleaned.get("date_to")
            if not d_from or not d_to:
                raise forms.ValidationError(
                    "Pro vlastní období je nutné vyplnit Od i Do."
                )
            if d_from > d_to:
                raise forms.ValidationError(
                    "Datum 'Od' nesmí být po datu 'Do'."
                )

        return cleaned

    def get_period(self):
        """
        Vrátí tuple (date_from, date_to, label) podle zvoleného typu období.
        """
        from datetime import date
        import calendar

        period_type = self.cleaned_data["period_type"]

        if period_type == self.PERIOD_MONTH:
            year = self.cleaned_data["year"]
            month = self.cleaned_data["month"]
            last_day = calendar.monthrange(year, month)[1]
            d_from = date(year, month, 1)
            d_to = date(year, month, last_day)
            label = f"{month:02d}/{year}"
        else:
            d_from = self.cleaned_data["date_from"]
            d_to = self.cleaned_data["date_to"]
            label = f"{d_from.strftime('%d.%m.%Y')} – {d_to.strftime('%d.%m.%Y')}"

        return d_from, d_to, label
