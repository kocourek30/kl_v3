from django import forms

from .models import CustomUser, Vklad


class VkladForm(forms.ModelForm):
    """Formulář pro ruční vklad na konto.

    Uživatelé s povoleným čerpáním do debetu jsou schválně vynechaní, protože
    jejich konto se vyrovnává systémovým nulováním.
    """

    class Meta:
        model = Vklad
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["uzivatel"].queryset = CustomUser.objects.exclude(
            groups__nastaveni__cerpani_debit=True
        ).distinct()
