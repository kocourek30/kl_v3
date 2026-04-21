from django.core.management.base import BaseCommand
from users.models import CustomUser, Vklad
from dotace.models import SkupinoveNastaveni
from decimal import Decimal
from users.group_utils import get_first_group_setting


class Command(BaseCommand):
    help = 'Nuluje konta zákazníků v debetu na konci měsíce'

    def handle(self, *args, **options):
        nulovano = 0
        
        for user in CustomUser.objects.filter(is_active=True):
            nastaveni = get_first_group_setting(user)
            
            # Pouze uživatelé s povoleným debetem
            if not nastaveni or not nastaveni.cerpani_debit:
                continue
            
            zustatek = user.aktualni_zustatek
            
            # Pouze pokud je zůstatek záporný
            if zustatek < 0:
                castka = Decimal('-1') * Decimal(zustatek)
                
                Vklad.objects.create(
                    uzivatel=user,
                    castka=castka,
                    status='nulovani_konta',
                    poznamka="Automatické nulování konta na konci měsíce"
                )
                
                nulovano += 1
                self.stdout.write(
                    f'  → {user.username} ({user.first_name} {user.last_name}): +{castka} Kč'
                )
        
        if nulovano == 0:
            self.stdout.write(
                self.style.SUCCESS('✅ Žádné účty k nulování.')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'✅ Nulování provedeno pro {nulovano} zákazníků.'
                )
            )
