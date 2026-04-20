from django.core.management.base import BaseCommand
from datetime import date

from objednavky.models import Order
from jidelnicek.services import mark_order_as_not_picked


class Command(BaseCommand):
    help = 'Označí nevyzvednuté objednávky starší nebo dnešní ve stavu objednano/zalozena-obsluhou'

    def handle(self, *args, **options):
        today = date.today()

        orders_to_mark = Order.objects.filter(
            datum_vydeje__lte=today,
            status__in=['objednano', 'zalozena-obsluhou']
        )

        count = orders_to_mark.count()

        if count == 0:
            self.stdout.write(
                self.style.SUCCESS('✅ Žádné nevyzvednuté objednávky k označení.')
            )
            return

        for order in orders_to_mark:
            mark_order_as_not_picked(order)

        self.stdout.write(
            self.style.SUCCESS(
                f'✅ Označeno {count} objednávek jako nevyzvednuto.'
            )
        )
