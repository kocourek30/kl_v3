import serial
from django.urls import path, reverse
from django.http import JsonResponse
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.db.models import Sum
from decimal import Decimal
from django.shortcuts import render, redirect
from django.contrib import messages

from import_export.admin import ExportMixin, ImportMixin
from import_export import resources
from import_export.formats.base_formats import CSV

from django.contrib.auth.models import Group
from dotace.models import DotacniPolitika
from django.utils.html import format_html

from .models import CustomUser, Vklad, StravovaciSkupina
from objednavky.models import OrderItem, Order
from sklad.models import ToleranceSpotrebnihoKose



class ToleranceSKInline(admin.TabularInline):
    model = ToleranceSpotrebnihoKose
    extra = 0
    min_num = 0
    verbose_name = "Tolerance skupiny SK"
    verbose_name_plural = "Tolerance spotřebního koše"
    fields = ("skupina_sk", "min_pct", "max_pct")



class CustomCSV(CSV):
    def create_dataset(self, in_stream, **kwargs):
        kwargs['delimiter'] = ';'
        return super().create_dataset(in_stream, **kwargs)


class CustomUserResource(resources.ModelResource):
    class Meta:
        model = CustomUser
        exclude = ('last_login', 'date_joined')
        import_id_fields = ('username',)

    def before_import_row(self, row, **kwargs):
        username = row.get('username')
        if not username:
            return
        try:
            user = CustomUser.objects.get(username=username)
            admin_group = Group.objects.get(name='admin')
            if admin_group in user.groups.all():
                raise Exception("skip")
        except CustomUser.DoesNotExist:
            pass
        except Group.DoesNotExist:
            pass

    def before_save_instance(self, instance, row, **kwargs):
        if instance.osobni_cislo:
            instance.set_password(instance.osobni_cislo)

    def import_row(self, row, instance_loader, **kwargs):
        instance = instance_loader.get_instance(row)
        if instance:
            kwargs['force_update'] = True
        else:
            kwargs['force_insert'] = True
        return super().import_row(row, instance_loader, **kwargs)


def read_rfid_code():
    try:
        ser = serial.Serial('COM3', 9600, timeout=3)
        code = ser.readline().decode('utf-8').strip()
        ser.close()
        return code
    except Exception:
        return None


@admin.register(CustomUser)
class CustomUserAdmin(ExportMixin, ImportMixin, UserAdmin):
    resource_class = CustomUserResource

    fieldsets = (
        UserAdmin.fieldsets[0],
        (("Osobní údaje"), {
            "fields": (
                "first_name",
                "last_name",
                "email",
                "identifikacni_medium",
                "osobni_cislo",
                "stravovaci_skupina",  # ← sem doplněno
            )
        }),
        (("Oprávnění"), {"fields": ("is_active", "is_staff", "is_superuser", "groups")}),
        (("Alergeny"), {"fields": ("alergeny",)}),
        (("Důležitá data"), {"fields": ("last_login", "date_joined")}),
    )

    list_display = (
        'username',
        'first_name',
        'last_name',
        'email',
        'colored_zustatek',
        'osobni_cislo',
        'debit_limit',
        'cerpa_debit',
        'ma_nutnost_dobit',
        'stravovaci_skupina',  # ← doplněno
    )

    search_fields = ('username', 'first_name', 'last_name', 'email', 'osobni_cislo')

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": (
                "username", "password1", "password2",
                "first_name", "last_name", "email",
                "identifikacni_medium", "osobni_cislo",
                "stravovaci_skupina",          # ← i do add formuláře
                "alergeny", "is_staff", "is_active", "groups",
            ),
        }),
    )
    filter_horizontal = ('alergeny', 'groups')

    change_form_template = "admin/customuser_change_form.html"
    change_list_template = "admin/users/customuser/change_list.html"

    @admin.display(description="Zůstatek")
    def colored_zustatek(self, obj):
        zustatek = obj.aktualni_zustatek
        formatted = f"{zustatek:.2f} Kč"
        if zustatek < 0:
            return format_html('<span style="color:#c0392b;font-weight:bold;">{}</span>', formatted)
        return formatted

    def get_user_group(self, obj):
        return obj.groups.first()

    @admin.display(description="Debet limit")
    def debit_limit(self, obj):
        skupina = self.get_user_group(obj)
        if not skupina:
            return "-"
        try:
            nastaveni = skupina.nastaveni
            return f"{nastaveni.debit_limit:.2f} Kč"
        except SkupinoveNastaveni.DoesNotExist:
            return "-"

    def cerpa_debit(self, obj):
        skupina = self.get_user_group(obj)
        if not skupina:
            return None
        try:
            nastaveni = skupina.nastaveni
            return nastaveni.cerpani_debit
        except SkupinoveNastaveni.DoesNotExist:
            return None
    cerpa_debit.boolean = True
    cerpa_debit.short_description = "Čerpá debet"

    def ma_nutnost_dobit(self, obj):
        skupina = self.get_user_group(obj)
        if not skupina:
            return None
        try:
            nastaveni = skupina.nastaveni
            return nastaveni.nutnost_dobit
        except SkupinoveNastaveni.DoesNotExist:
            return None
    ma_nutnost_dobit.boolean = True
    ma_nutnost_dobit.short_description = "Nutnost vložit peníze"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('read-rfid/', self.admin_site.admin_view(self.read_rfid_view), name='read-rfid'),
        ]
        return custom_urls + urls

    def read_rfid_view(self, request):
        code = read_rfid_code()
        if code:
            return JsonResponse({'success': True, 'code': code})
        else:
            return JsonResponse({'success': False, 'error': 'Error reading RFID'})

    def render_change_form(self, request, context, *args, **kwargs):
        context['read_rfid_url'] = reverse('admin:read-rfid')
        return super().render_change_form(request, context, *args, **kwargs)


@admin.register(Vklad)
class VkladAdmin(admin.ModelAdmin):
    list_display = ('uzivatel', 'castka', 'datum', 'status', 'poznamka')
    search_fields = ('uzivatel__username', 'uzivatel__osobni_cislo')
    list_filter = ('datum', 'status', 'uzivatel')

    actions = ['nulovat_konta']

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('nulovani-konta/', self.admin_site.admin_view(self.nulovani_konta_view), name='users_vklad_nulovani_konta'),
        ]
        return custom_urls + urls

    def nulovani_konta_view(self, request):
        from users.models import CustomUser
        from dotace.models import SkupinoveNastaveni

        if request.method == 'POST':
            user_ids = request.POST.getlist('users')
            if not user_ids:
                messages.error(request, "Nevybrali jste žádné uživatele ke zpracování.")
                return redirect('admin:users_vklad_changelist')

            users = CustomUser.objects.filter(id__in=user_ids, is_active=True).prefetch_related('groups__nastaveni')
            nulovano = 0
            for user in users:
                skupina = user.groups.first()
                nastaveni = getattr(skupina, 'nastaveni', None)
                if not nastaveni or not nastaveni.cerpani_debit:
                    continue
                zustatek = user.aktualni_zustatek
                if zustatek < 0:
                    castka = Decimal('-1') * Decimal(zustatek)
                    Vklad.objects.create(
                        uzivatel=user,
                        castka=castka,
                        status='nulovani_konta',
                        poznamka="Automatické nulování konta"
                    )
                    nulovano += 1
            messages.success(request, f"Nulování účtu provedeno pro {nulovano} zákazníků.")
            return redirect('admin:users_vklad_changelist')
        else:
            users = CustomUser.objects.filter(is_active=True).prefetch_related('groups__nastaveni')
            users = [u for u in users if u.groups.first() and getattr(u.groups.first(), 'nastaveni', None) and u.groups.first().nastaveni.cerpani_debit]
            context = dict(
                self.admin_site.each_context(request),
                users=users,
            )
            return render(request, 'admin/nulovani_konta_form.html', context)

    def nulovat_konta(self, request, queryset=None):
        from users.models import CustomUser
        from dotace.models import SkupinoveNastaveni

        nulovano = 0
        for user in CustomUser.objects.filter(is_active=True):
            skupina = user.groups.first()
            nastaveni = getattr(skupina, 'nastaveni', None)
            if not nastaveni or not nastaveni.cerpani_debit:
                continue
            zustatek = user.aktualni_zustatek
            if zustatek < 0:
                castka = Decimal('-1') * Decimal(zustatek)
                Vklad.objects.create(
                    uzivatel=user,
                    castka=castka,
                    status='nulovani_konta',
                    poznamka="Automatické nulování konta"
                )
                nulovano += 1
        self.message_user(request, f"Nulování účtu provedeno pro {nulovano} zákazníků s povoleným debetem.", level='success')

    nulovat_konta.short_description = "Nulovat konta zákazníků v debetu (hromadně)"

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)


@admin.register(StravovaciSkupina)
class StravovaciSkupinaAdmin(admin.ModelAdmin):
    list_display = ("kod", "nazev", "typ_vzdelavani", "django_group")
    list_filter = ("typ_vzdelavani",)
    search_fields = ("kod", "nazev")
    inlines = [ToleranceSKInline]  # ← DOPLNIT


