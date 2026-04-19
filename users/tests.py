from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase

from dotace.models import SkupinoveNastaveni

from .models import Vklad


class VkladDebitValidationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="debetni.student", password="x")
        self.group = Group.objects.create(name="Debetní skupina")
        SkupinoveNastaveni.objects.create(
            skupina=self.group,
            cerpani_debit=True,
            debit_limit=Decimal("-500.00"),
        )
        self.user.groups.add(self.group)

    def test_standardni_kladny_vklad_debetnimu_uzivateli_neprojde(self):
        with self.assertRaises(ValidationError):
            Vklad.objects.create(
                uzivatel=self.user,
                castka=Decimal("100.00"),
                poznamka="Ruční dobití",
            )

    def test_cerpani_z_konta_debetnimu_uzivateli_projde(self):
        pohyb = Vklad.objects.create(
            uzivatel=self.user,
            castka=Decimal("-45.00"),
            poznamka="Čerpání z konta",
        )

        self.assertEqual(pohyb.castka, Decimal("-45.00"))

    def test_systemove_nulovani_debetu_projde(self):
        pohyb = Vklad.objects.create(
            uzivatel=self.user,
            castka=Decimal("120.00"),
            status="nulovani_konta",
            poznamka="Automatické nulování konta",
        )

        self.assertEqual(pohyb.status, "nulovani_konta")
