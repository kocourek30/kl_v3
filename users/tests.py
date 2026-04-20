from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

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


class UserProfileViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="student",
            password="Str0ng-pass-2026",
            email="student@example.com",
            first_name="Jan",
            last_name="Student",
        )

    def test_profile_requires_login(self):
        response = self.client.get(reverse("users:user-profile"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_invalid_email_is_not_saved(self):
        self.client.login(username="student", password="Str0ng-pass-2026")

        response = self.client.post(reverse("users:user-profile"), {
            "email": "neni-email",
        })

        self.user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.user.email, "student@example.com")
        self.assertContains(response, "Zadejte prosím platný email.")

    def test_profile_email_update(self):
        self.client.login(username="student", password="Str0ng-pass-2026")

        response = self.client.post(reverse("users:user-profile"), {
            "email": "novy.student@example.com",
        })

        self.user.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.user.email, "novy.student@example.com")
