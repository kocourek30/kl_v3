from datetime import date, datetime, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from dotace.services import vypocet_dotovane_ceny
from jidelnicek.models import DruhJidla, Jidelnicek, Jidlo, PolozkaJidelnicku
from objednavky.models import Order, OrderItem
from users.models import CustomUser


class Command(BaseCommand):
    help = "Vytvoří historickou konzumaci pro vybraného zákazníka, aby šly testovat finanční reporty."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="testuser", help="Username zákazníka.")
        parser.add_argument("--months", type=int, default=3, help="Počet plných minulých měsíců k vygenerování.")

    def handle(self, *args, **options):
        username = options["username"]
        months = max(1, int(options["months"] or 1))

        try:
            user = CustomUser.objects.get(username=username)
        except CustomUser.DoesNotExist as exc:
            raise CommandError(f"Uživatel {username!r} neexistuje.") from exc

        meal_type_names = ["Oběd", "Snídaně", "1. Svačina"]
        meal_types = {
            meal_type.nazev: meal_type
            for meal_type in DruhJidla.objects.filter(nazev__in=meal_type_names)
        }
        if "Oběd" not in meal_types:
            raise CommandError("Pro seed je potřeba alespoň druh jídla 'Oběd'.")

        meals_by_type = {}
        for type_name, meal_type in meal_types.items():
            meals = list(Jidlo.objects.filter(druh=meal_type).order_by("id"))
            if meals:
                meals_by_type[type_name] = meals
        if "Oběd" not in meals_by_type:
            raise CommandError("V katalogu nejsou dostupná žádná jídla typu 'Oběd'.")

        today = timezone.localdate()
        current_month_start = today.replace(day=1)
        start_month = current_month_start
        for _ in range(months):
            start_month = (start_month - timedelta(days=1)).replace(day=1)
        end_date = current_month_start - timedelta(days=1)

        created_orders = 0
        updated_orders = 0
        created_items = 0
        updated_items = 0

        with transaction.atomic():
            current_day = start_month
            weekday_index = 0

            while current_day <= end_date:
                if current_day.weekday() < 5:
                    created, updated, item_created, item_updated = self._seed_day(
                        user=user,
                        target_date=current_day,
                        weekday_index=weekday_index,
                        meal_types=meal_types,
                        meals_by_type=meals_by_type,
                    )
                    created_orders += created
                    updated_orders += updated
                    created_items += item_created
                    updated_items += item_updated
                    weekday_index += 1
                current_day += timedelta(days=1)

        self.stdout.write(
            self.style.SUCCESS(
                f"Historická konzumace připravena pro {username}: "
                f"objednávky vytvořeny {created_orders}, upraveny {updated_orders}, "
                f"položky vytvořeny {created_items}, upraveny {updated_items}."
            )
        )

    def _seed_day(self, user, target_date, weekday_index, meal_types, meals_by_type):
        jidelnicek, _ = Jidelnicek.objects.get_or_create(
            platnost_od=target_date,
            platnost_do=target_date,
        )

        selected_pairs = [("Oběd", meals_by_type["Oběd"][weekday_index % len(meals_by_type["Oběd"])])]
        if "Snídaně" in meals_by_type and target_date.weekday() in (0, 2):
            selected_pairs.append(("Snídaně", meals_by_type["Snídaně"][weekday_index % len(meals_by_type["Snídaně"])]))
        if "1. Svačina" in meals_by_type and target_date.weekday() == 4:
            selected_pairs.append(("1. Svačina", meals_by_type["1. Svačina"][weekday_index % len(meals_by_type["1. Svačina"])]) )

        order_status = "nevyzvednuto" if weekday_index % 9 == 0 else "vydano"
        order, order_created = Order.objects.get_or_create(
            user=user,
            datum_vydeje=target_date,
            defaults={
                "status": order_status,
                "datum_vydani": timezone.make_aware(datetime.combine(target_date, time(11, 45))) if order_status == "vydano" else None,
            },
        )
        if not order_created:
            order.status = order_status
            order.datum_vydani = timezone.make_aware(datetime.combine(target_date, time(11, 45))) if order_status == "vydano" else None
            order.save(update_fields=["status", "datum_vydani", "updated_at"])

        created_items = 0
        updated_items = 0

        for type_name, meal in selected_pairs:
            meal_type = meal_types[type_name]
            menu_item, _ = PolozkaJidelnicku.objects.get_or_create(
                jidelnicek=jidelnicek,
                druh_jidla=meal_type,
                defaults={"jidlo": meal},
            )
            if menu_item.jidlo_id != meal.id:
                menu_item.jidlo = meal
                menu_item.save(update_fields=["jidlo"])

            subsidized_price = vypocet_dotovane_ceny(
                user,
                menu_item,
                target_date=target_date,
                quantity=1,
            )

            order_item, item_created = OrderItem.objects.update_or_create(
                order=order,
                menu_item=menu_item,
                defaults={
                    "quantity": 1,
                    "cena": subsidized_price,
                    "vydano": order_status == "vydano",
                    "datum_vydani": timezone.make_aware(datetime.combine(target_date, time(11, 45))) if order_status == "vydano" else None,
                },
            )
            if item_created:
                created_items += 1
            else:
                updated_items += 1

        return int(order_created), int(not order_created), created_items, updated_items
