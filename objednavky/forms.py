from django import forms

from jidelnicek.models import PolozkaJidelnicku

from .models import Order, OrderItem, OrderValidator


class ObjednavkaForm(forms.ModelForm):
    """
    Kompatibilní formulář nad aktuálním modelem Order.

    Původní verze odkazovala na odstraněné modely Objednavka/PolozkaObjednavky,
    takže pouhý import formulářů mohl spadnout. Název třídy necháváme kvůli
    případným starším importům.
    """

    menu_items = forms.ModelMultipleChoiceField(
        label="Jídla k objednání",
        required=False,
        queryset=PolozkaJidelnicku.objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Order
        fields = ['user', 'datum_vydeje', 'status']
        widgets = {
            'datum_vydeje': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        datum = self.data.get('datum_vydeje') or getattr(self.instance, 'datum_vydeje', None)

        if datum:
            self.fields['menu_items'].queryset = (
                PolozkaJidelnicku.objects
                .filter(
                    jidelnicek__platnost_od__lte=datum,
                    jidelnicek__platnost_do__gte=datum,
                )
                .select_related('jidlo', 'druh_jidla')
                .order_by('druh_jidla__poradi', 'druh_jidla__nazev', 'jidlo__nazev')
            )

        if self.instance.pk:
            self.initial['menu_items'] = list(
                self.instance.items.values_list('menu_item_id', flat=True)
            )

    def save(self, commit=True):
        order = super().save(commit=False)
        if not commit:
            return order

        order.save()
        order.items.all().delete()

        items_to_create = []
        for menu_item in self.cleaned_data.get('menu_items', []):
            items_to_create.append(
                OrderItem(
                    order=order,
                    menu_item=menu_item,
                    quantity=1,
                    cena=OrderValidator.get_price_for_user(order.user, menu_item),
                )
            )

        if items_to_create:
            OrderItem.objects.bulk_create(items_to_create)

        return order
