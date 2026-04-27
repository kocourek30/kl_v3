import serial
from django.urls import path, reverse
from django.http import HttpResponseRedirect, JsonResponse
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.db.models import Q, Sum
from datetime import date
from decimal import Decimal, InvalidOperation
from django.shortcuts import render, redirect
from django.contrib import messages

from import_export.admin import ExportMixin, ImportMixin
from import_export import resources
from import_export.formats.base_formats import CSV

from django.contrib.auth.models import Group
from dotace.models import DotacniPolitika, SkupinoveNastaveni
from django.utils.html import format_html

from .models import CustomUser, Vklad, StravovaciSkupina
from .forms import VkladForm
from objednavky.models import OrderItem, Order
from sklad.models import ToleranceSpotrebnihoKose
from .group_utils import get_effective_user_groups, get_first_group_setting, get_primary_effective_group



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
            if any(group.pk == admin_group.pk for group in get_effective_user_groups(user)):
                raise Exception("skip")
        except CustomUser.DoesNotExist:
            pass
        except Group.DoesNotExist:
            pass

    def before_save_instance(self, instance, row, **kwargs):
        if instance.osobni_cislo:
            instance.set_password(instance.osobni_cislo)
            instance.must_change_password = True
            instance.password_changed_at = None

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
        (("Bezpečnost"), {"fields": ("must_change_password", "password_changed_at")}),
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
        'must_change_password',
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
    import_export_change_list_template = "admin/users/customuser/change_list.html"

    class Media:
        css = {"all": ("users/css/user_list_admin.css",)}

    @admin.display(description="Zůstatek")
    def colored_zustatek(self, obj):
        zustatek = obj.aktualni_zustatek
        formatted = f"{zustatek:.2f} Kč"
        if zustatek < 0:
            return format_html('<span style="color:#c0392b;font-weight:bold;">{}</span>', formatted)
        return formatted

    def get_user_group(self, obj):
        return get_primary_effective_group(obj)

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
            path("bulk-apply/", self.admin_site.admin_view(self.bulk_apply_view), name="users_customuser_bulk_apply"),
        ]
        return custom_urls + urls

    def bulk_apply_view(self, request):
        changelist_url = reverse("admin:users_customuser_changelist")
        query_string = request.GET.urlencode()
        redirect_url = f"{changelist_url}?{query_string}" if query_string else changelist_url

        if request.method != "POST":
            return HttpResponseRedirect(redirect_url)

        if not self.has_change_permission(request):
            self.message_user(request, "Nemáš oprávnění pro hromadné operace.")
            return HttpResponseRedirect(redirect_url)

        operation = request.POST.get("bulk_operation", "").strip()
        queryset = self.get_queryset(request)

        if operation == "activate":
            updated = queryset.exclude(is_active=True).update(is_active=True)
            self.message_user(request, f"Hotovo: aktivováno {updated} uživatelů.")
        elif operation == "deactivate":
            filtered = queryset.exclude(is_superuser=True)
            updated = filtered.exclude(is_active=False).update(is_active=False)
            self.message_user(request, f"Hotovo: deaktivováno {updated} uživatelů (superadmini zůstali aktivní).")
        elif operation == "reset_password_personal":
            updated = 0
            skipped = 0
            for user in queryset:
                personal = (user.osobni_cislo or "").strip()
                if not personal:
                    skipped += 1
                    continue
                user.set_password(personal)
                user.must_change_password = True
                user.password_changed_at = None
                user.save(update_fields=["password", "must_change_password", "password_changed_at"])
                updated += 1
            self.message_user(
                request,
                f"Hotovo: reset hesla podle osobního čísla u {updated} uživatelů."
                + (f" Přeskočeno bez osobního čísla: {skipped}." if skipped else ""),
            )
        else:
            self.message_user(request, "Vyber hromadnou operaci, kterou chceš provést.")

        return HttpResponseRedirect(redirect_url)

    def get_queryset(self, request):
        qs = (
            super()
            .get_queryset(request)
            .select_related("stravovaci_skupina")
            .prefetch_related("groups")
        )
        role_group_id = request.GET.get("role_group", "").strip()
        strav_group_id = request.GET.get("strav_group", "").strip()
        medium_state = request.GET.get("medium", "").strip()
        active_state = request.GET.get("active", "").strip()
        balance_state = request.GET.get("balance", "").strip()

        if role_group_id:
            try:
                qs = qs.filter(groups__id=int(role_group_id))
            except (TypeError, ValueError):
                qs = qs.none()

        if strav_group_id:
            try:
                qs = qs.filter(stravovaci_skupina_id=int(strav_group_id))
            except (TypeError, ValueError):
                qs = qs.none()

        if medium_state == "with":
            qs = qs.exclude(Q(identifikacni_medium__isnull=True) | Q(identifikacni_medium__exact=""))
        elif medium_state == "without":
            qs = qs.filter(Q(identifikacni_medium__isnull=True) | Q(identifikacni_medium__exact=""))

        if active_state == "yes":
            qs = qs.filter(is_active=True)
        elif active_state == "no":
            qs = qs.filter(is_active=False)

        qs = qs.distinct()

        if balance_state in {"positive", "negative", "zero"}:
            filtered_ids = []
            for u in qs:
                z = u.aktualni_zustatek
                if balance_state == "positive" and z > 0:
                    filtered_ids.append(u.pk)
                elif balance_state == "negative" and z < 0:
                    filtered_ids.append(u.pk)
                elif balance_state == "zero" and z == 0:
                    filtered_ids.append(u.pk)
            qs = qs.filter(pk__in=filtered_ids)

        return qs

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        response = super().changelist_view(request, extra_context=extra_context)
        if not hasattr(response, "context_data"):
            return response

        cl = response.context_data["cl"]
        users_on_page = list(cl.result_list)

        def _has_debt_enabled(user):
            setting = get_first_group_setting(user)
            return bool(setting and setting.cerpani_debit)

        active_on_page = sum(1 for u in users_on_page if u.is_active)
        with_medium_on_page = sum(
            1 for u in users_on_page if (u.identifikacni_medium or "").strip()
        )
        negative_balance_on_page = sum(1 for u in users_on_page if u.aktualni_zustatek < 0)
        debt_enabled_on_page = sum(
            1
            for u in users_on_page
            if _has_debt_enabled(u)
        )

        response.context_data.update(
            {
                "user_cards": users_on_page,
                "user_total_count": cl.result_count,
                "users_on_page_count": len(users_on_page),
                "users_active_on_page": active_on_page,
                "users_with_medium_on_page": with_medium_on_page,
                "users_negative_balance_on_page": negative_balance_on_page,
                "users_debt_enabled_on_page": debt_enabled_on_page,
                "user_role_group_options": Group.objects.order_by("name"),
                "user_strav_group_options": StravovaciSkupina.objects.order_by("nazev"),
                "user_filter_role_group": request.GET.get("role_group", "").strip(),
                "user_filter_strav_group": request.GET.get("strav_group", "").strip(),
                "user_filter_medium": request.GET.get("medium", "").strip(),
                "user_filter_active": request.GET.get("active", "").strip(),
                "user_filter_balance": request.GET.get("balance", "").strip(),
                "user_filter_q": request.GET.get("q", "").strip(),
                "user_filters_active": bool(
                    request.GET.get("q", "").strip()
                    or request.GET.get("role_group", "").strip()
                    or request.GET.get("strav_group", "").strip()
                    or request.GET.get("medium", "").strip()
                    or request.GET.get("active", "").strip()
                    or request.GET.get("balance", "").strip()
                ),
                "user_bulk_querystring": request.GET.urlencode(),
            }
        )
        return response

    def read_rfid_view(self, request):
        code = read_rfid_code()
        if code:
            return JsonResponse({'success': True, 'code': code})
        else:
            return JsonResponse({'success': False, 'error': 'Nepodařilo se přečíst RFID čip.'})

    def render_change_form(self, request, context, *args, **kwargs):
        context['read_rfid_url'] = reverse('admin:read-rfid')
        return super().render_change_form(request, context, *args, **kwargs)


@admin.register(Vklad)
class VkladAdmin(admin.ModelAdmin):
    form = VkladForm
    list_display = ('uzivatel', 'castka', 'zpusob_uhrady', 'pokladna', 'datum', 'status', 'poznamka')
    search_fields = ('uzivatel__username', 'uzivatel__osobni_cislo')
    list_filter = ('datum', 'status', 'zpusob_uhrady', 'pokladna', 'uzivatel')
    change_list_template = "admin/users/vklad/change_list.html"

    actions = ['nulovat_konta']

    class Media:
        css = {"all": ("users/css/vklad_list_admin.css",)}

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('nulovani-konta/', self.admin_site.admin_view(self.nulovani_konta_view), name='users_vklad_nulovani_konta'),
            path("bulk-apply/", self.admin_site.admin_view(self.bulk_apply_view), name="users_vklad_bulk_apply"),
        ]
        return custom_urls + urls

    def _parse_decimal_param(self, value):
        raw = (value or "").strip().replace(",", ".")
        if not raw:
            return None
        try:
            return Decimal(raw)
        except (InvalidOperation, TypeError):
            return None

    def _parse_date_param(self, value):
        raw = (value or "").strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None

    def bulk_apply_view(self, request):
        changelist_url = reverse("admin:users_vklad_changelist")
        query_string = request.GET.urlencode()
        redirect_url = f"{changelist_url}?{query_string}" if query_string else changelist_url

        if request.method != "POST":
            return HttpResponseRedirect(redirect_url)

        if not self.has_change_permission(request):
            self.message_user(request, "Nemáš oprávnění pro hromadné operace vkladů.")
            return HttpResponseRedirect(redirect_url)

        operation = request.POST.get("bulk_operation", "").strip()
        queryset = self.get_queryset(request)

        if operation == "set_cash":
            updated = queryset.update(zpusob_uhrady=Vklad.ZPUSOB_HOTOVOST)
            self.message_user(request, f"Hotovo: u {updated} vkladů nastaveno 'Hotově'.")
        elif operation == "set_card":
            updated = queryset.update(zpusob_uhrady=Vklad.ZPUSOB_KARTA)
            self.message_user(request, f"Hotovo: u {updated} vkladů nastaveno 'Kartou'.")
        elif operation == "set_qr":
            updated = queryset.update(zpusob_uhrady=Vklad.ZPUSOB_QR)
            self.message_user(request, f"Hotovo: u {updated} vkladů nastaveno 'QR platbou'.")
        else:
            self.message_user(request, "Vyber hromadnou operaci, kterou chceš provést.")

        return HttpResponseRedirect(redirect_url)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("uzivatel", "pokladna")

        status = request.GET.get("stav", "").strip()
        payment = request.GET.get("uhrada", "").strip()
        pokladna_id = request.GET.get("pokladna", "").strip()
        date_from = self._parse_date_param(request.GET.get("od", ""))
        date_to = self._parse_date_param(request.GET.get("do", ""))
        user_lookup = request.GET.get("uzivatel", "").strip()
        amount_from = self._parse_decimal_param(request.GET.get("castka_od", ""))
        amount_to = self._parse_decimal_param(request.GET.get("castka_do", ""))

        if status:
            qs = qs.filter(status=status)
        if payment:
            qs = qs.filter(zpusob_uhrady=payment)
        if pokladna_id:
            try:
                qs = qs.filter(pokladna_id=int(pokladna_id))
            except (TypeError, ValueError):
                qs = qs.none()
        if date_from is not None:
            qs = qs.filter(datum__date__gte=date_from)
        if date_to is not None:
            qs = qs.filter(datum__date__lte=date_to)
        if user_lookup:
            qs = qs.filter(
                Q(uzivatel__username__icontains=user_lookup)
                | Q(uzivatel__first_name__icontains=user_lookup)
                | Q(uzivatel__last_name__icontains=user_lookup)
                | Q(uzivatel__osobni_cislo__icontains=user_lookup)
            )
        if amount_from is not None:
            qs = qs.filter(castka__gte=amount_from)
        if amount_to is not None:
            qs = qs.filter(castka__lte=amount_to)

        return qs

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        response = super().changelist_view(request, extra_context=extra_context)
        if not hasattr(response, "context_data"):
            return response

        cl = response.context_data["cl"]
        rows = list(cl.result_list)
        amount_sum = sum((r.castka for r in rows), Decimal("0"))

        status_counts = {
            Vklad.STATUS_STANDARD: 0,
            Vklad.STATUS_NULOVANI_KONTA: 0,
            Vklad.STATUS_PLATBA_UCTU: 0,
        }
        for row in rows:
            if row.status in status_counts:
                status_counts[row.status] += 1

        pokladna_options = (
            Vklad.objects.exclude(pokladna__isnull=True)
            .values("pokladna_id", "pokladna__nazev")
            .order_by("pokladna__nazev")
            .distinct()
        )

        response.context_data.update(
            {
                "vklad_rows": rows,
                "vklad_total_count": cl.result_count,
                "vklad_rows_on_page": len(rows),
                "vklad_amount_sum_on_page": amount_sum,
                "vklad_status_standard": status_counts[Vklad.STATUS_STANDARD],
                "vklad_status_nulovani": status_counts[Vklad.STATUS_NULOVANI_KONTA],
                "vklad_status_platba": status_counts[Vklad.STATUS_PLATBA_UCTU],
                "vklad_filter_status": request.GET.get("stav", "").strip(),
                "vklad_filter_payment": request.GET.get("uhrada", "").strip(),
                "vklad_filter_pokladna": request.GET.get("pokladna", "").strip(),
                "vklad_filter_date_from": request.GET.get("od", "").strip(),
                "vklad_filter_date_to": request.GET.get("do", "").strip(),
                "vklad_filter_user": request.GET.get("uzivatel", "").strip(),
                "vklad_filter_amount_from": request.GET.get("castka_od", "").strip(),
                "vklad_filter_amount_to": request.GET.get("castka_do", "").strip(),
                "vklad_filters_active": bool(
                    request.GET.get("stav", "").strip()
                    or request.GET.get("uhrada", "").strip()
                    or request.GET.get("pokladna", "").strip()
                    or request.GET.get("od", "").strip()
                    or request.GET.get("do", "").strip()
                    or request.GET.get("uzivatel", "").strip()
                    or request.GET.get("castka_od", "").strip()
                    or request.GET.get("castka_do", "").strip()
                ),
                "vklad_bulk_querystring": request.GET.urlencode(),
                "vklad_status_choices": Vklad.STATUS_CHOICES,
                "vklad_payment_choices": Vklad.ZPUSOBY_UHRADY,
                "vklad_pokladna_options": list(pokladna_options),
            }
        )
        return response

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
                nastaveni = get_first_group_setting(user)
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
            users = [
                u for u in users
                if (get_first_group_setting(u) and get_first_group_setting(u).cerpani_debit)
            ]
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
            nastaveni = get_first_group_setting(user)
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


