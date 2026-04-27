from django.utils.html import format_html
from django.contrib import admin
from decimal import Decimal, InvalidOperation
from django.db.models import Count, Prefetch, Q, Sum
from django.db.utils import OperationalError, ProgrammingError
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse
from urllib.parse import urlencode

from django import forms
from django.urls import path
from django.forms.models import BaseInlineFormSet

from .models import (
    Alergen,
    DruhJidla,
    Jidelnicek,
    Jidlo,
    JidloPhotoProposal,
    MenuImportRun,
    PolozkaJidelnicku,
    vychozi_ikona_druhu_jidla,
    vychozi_ikona_jidla,
)
from dotace.models import DotacniPolitika, DotaceProJidelniskouSkupinu
from sklad.admin import RecepturaPolozkaInline, JidloKomponentaInline
from pokladna.models import PLUPolozka, DPHSkupina, PLUKategorie
from .ai_photos import apply_photo_proposal, generate_food_photo_proposal, reject_photo_proposal


class JidloAdminForm(forms.ModelForm):
    class Meta:
        model = Jidlo
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["druh"].help_text = (
            "Katalogový druh jídla. Použije se jako zdroj pravdy při zařazení do jídelníčku "
            "a ovlivňuje ceny po dotacích, limity i viditelnost."
        )

    def clean_druh(self):
        druh = self.cleaned_data.get("druh")
        if druh is None and self.instance.pk and self.instance.polozkajidelnicku_set.exists():
            raise ValidationError(
                "Jídlo už je použité v jídelníčku, proto mu nelze odebrat druh jídla."
            )
        return druh


class PolozkaJidelnickuAdminForm(forms.ModelForm):
    class Meta:
        model = PolozkaJidelnicku
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["jidlo"].widget = forms.Select(attrs={"class": "menu-builder-food-select"})
        druh_jidla = self._resolve_slot_kind()
        jidlo_qs = Jidlo.objects.none()
        if druh_jidla:
            jidlo_qs = Jidlo.objects.filter(druh_id=druh_jidla).order_by("nazev")

        selected_jidlo_id = self._resolve_selected_jidlo_id()
        if selected_jidlo_id:
            selected_qs = Jidlo.objects.filter(pk=selected_jidlo_id)
            jidlo_qs = (jidlo_qs | selected_qs).distinct().order_by("nazev")

        self.fields["jidlo"].queryset = jidlo_qs
        self.fields["jidlo"].widget.choices = self.fields["jidlo"].choices
        self.fields["jidlo"].help_text = (
            "Vyber jídlo z katalogu. Nabídka je omezená jen na jídla odpovídající tomuto druhu."
        )
        self.fields["druh_jidla"].help_text = (
            "Slot jídelníčku. Po prvním uložení se pro jednotlivé druhy předpřipraví řádky automaticky."
        )
        if self.instance.pk and self.instance.jidlo_id and self.instance.jidlo.druh_id:
            self.fields["druh_jidla"].initial = self.instance.jidlo.druh_id
        if self.instance.pk or self.initial.get("druh_jidla"):
            self.fields["druh_jidla"].disabled = True

    def _resolve_slot_kind(self):
        bound_value = self.data.get(self.add_prefix("druh_jidla"))
        if bound_value:
            try:
                return int(bound_value)
            except (TypeError, ValueError):
                return None
        initial_value = self.initial.get("druh_jidla")
        if hasattr(initial_value, "pk"):
            return initial_value.pk
        if initial_value:
            try:
                return int(initial_value)
            except (TypeError, ValueError):
                return None
        if self.instance.pk and self.instance.druh_jidla_id:
            return self.instance.druh_jidla_id
        return None

    def _resolve_selected_jidlo_id(self):
        bound_value = self.data.get(self.add_prefix("jidlo"))
        if bound_value:
            try:
                return int(bound_value)
            except (TypeError, ValueError):
                return None
        if self.instance.pk and self.instance.jidlo_id:
            return self.instance.jidlo_id
        return None

    def clean(self):
        cleaned_data = super().clean()
        jidlo = cleaned_data.get("jidlo")
        druh_jidla = cleaned_data.get("druh_jidla")
        if jidlo and not jidlo.druh_id:
            self.add_error("jidlo", "Vybrané jídlo nemá v katalogu nastavený druh jídla.")
        elif jidlo and druh_jidla and jidlo.druh_id != druh_jidla.id:
            self.add_error(
                "jidlo",
                (
                    f"Vybrané jídlo patří do druhu „{jidlo.druh}“, "
                    f"ale tento slot je určený pro „{druh_jidla}“."
                ),
            )
        return cleaned_data


class PolozkaJidelnickuInlineFormSet(BaseInlineFormSet):
    def __init__(self, *args, **kwargs):
        instance = kwargs.get("instance")
        if instance and instance.pk and not kwargs.get("initial"):
            existing_ids = set(
                instance.polozky.values_list("druh_jidla_id", flat=True)
            )
            kwargs["initial"] = [
                {"druh_jidla": druh.pk}
                for druh in DruhJidla.objects.exclude(pk__in=existing_ids).order_by("poradi", "nazev")
            ]
        super().__init__(*args, **kwargs)


# ====== PŮVODNÍ ADMIN + AUTO‑PLU ======


@admin.register(DruhJidla)
class DruhJidlaAdmin(admin.ModelAdmin):
    list_display = ('nazev', 'poradi', 'visible_for_groups', 'icon_preview')
    list_editable = ('poradi',)
    search_fields = ('nazev',)
    ordering = ('poradi', 'nazev')
    fields = ('nazev', 'poradi', 'ikona', 'viditelne_pro_skupiny')
    filter_horizontal = ('viditelne_pro_skupiny',)
    actions = ("doplnit_ikony_druhu_jidel",)

    def icon_preview(self, obj):
        if hasattr(obj, 'ikona') and obj.ikona:
            return format_html('<i class="{}"></i>', obj.ikona)
        return ""
    icon_preview.short_description = 'Ikona'
    icon_preview.admin_order_field = 'ikona'

    @admin.display(description="Uvidí")
    def visible_for_groups(self, obj):
        groups = list(obj.viditelne_pro_skupiny.values_list("name", flat=True))
        if not groups:
            return "Všichni"
        return ", ".join(groups)

    def save_model(self, request, obj, form, change):
        if not obj.ikona:
            obj.ikona = vychozi_ikona_druhu_jidla(obj.nazev)
        super().save_model(request, obj, form, change)

    @admin.action(description="Doplnit výchozí ikony podle názvu druhu")
    def doplnit_ikony_druhu_jidel(self, request, queryset):
        aktualizovano = 0
        for druh in queryset:
            if druh.ikona:
                continue
            druh.ikona = vychozi_ikona_druhu_jidla(druh.nazev)
            druh.save(update_fields=["ikona"])
            aktualizovano += 1
        self.message_user(request, f"Doplněno ikon u druhů jídel: {aktualizovano}.")


@admin.register(Jidlo)
class JidloAdmin(admin.ModelAdmin):
    form = JidloAdminForm
    change_form_template = "admin/jidelnicek/jidlo/change_form.html"
    change_list_template = "admin/jidelnicek/jidlo/change_list.html"
    list_display = ('nahled', 'nazev', 'druh', 'cena', 'alergeny_list', 'ceny_po_dotacich', 'ma_komponenty')
    search_fields = ('nazev',)
    list_filter = (
        'druh',
        'sk_rybi_pokrm',
        'sk_bezmasy_pokrm',
        'sk_sladky_pokrm',
        'sk_slazeny_napoj',
    )
    filter_horizontal = ('alergeny',)
    inlines = [JidloKomponentaInline, RecepturaPolozkaInline]
    actions = ["vygenerovat_plu_pro_jidla", "doplnit_ikony_jidel"]
    list_per_page = 30
    fieldsets = (
        (
            "Základní údaje",
            {
                "fields": (
                    "nazev",
                    "druh",
                    "cena",
                    "alergeny",
                    "ikona",
                    "foto",
                ),
            },
        ),
        (
            "Nutriční údaje",
            {
                "classes": ("collapse",),
                "fields": ("kcal", "bílkoviny", "tuky", "sacharidy"),
            },
        ),
        (
            "Spotřební koš 2025",
            {
                "fields": (
                    "sk_rybi_pokrm",
                    "sk_bezmasy_pokrm",
                    "sk_bile_maso",
                    "sk_cervene_maso",
                    "sk_sladky_pokrm",
                    "sk_jemne_pecivo",
                    "sk_dezert_s_volnym_cukrem",
                    "sk_slazeny_napoj",
                ),
            },
        ),
    )

    class Media:
        css = {"all": ("jidelnicek/css/food_list_admin.css", "jidelnicek/css/food_form_admin.css")}

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path(
                "bulk-apply/",
                self.admin_site.admin_view(self.bulk_apply_view),
                name="jidelnicek_jidlo_bulk_apply",
            ),
        ]
        return my_urls + urls

    def _parse_decimal_param(self, value):
        raw = (value or "").strip().replace(",", ".")
        if not raw:
            return None
        try:
            return Decimal(raw)
        except (InvalidOperation, TypeError):
            return None

    def bulk_apply_view(self, request):
        changelist_url = reverse("admin:jidelnicek_jidlo_changelist")
        query_string = request.GET.urlencode()

        if request.method != "POST":
            return HttpResponseRedirect(f"{changelist_url}?{query_string}" if query_string else changelist_url)

        if not self.has_change_permission(request):
            self.message_user(request, "Nemáš oprávnění pro hromadné operace.")
            return HttpResponseRedirect(f"{changelist_url}?{query_string}" if query_string else changelist_url)

        operation = request.POST.get("bulk_operation", "").strip()
        queryset = self.get_queryset(request)

        if operation == "sync_plu":
            count = queryset.count()
            for jidlo in queryset:
                self._ensure_plu_for_jidlo(jidlo)
            self.message_user(request, f"Hotovo: PLU synchronizováno u {count} jídel.")
        elif operation == "fill_icons":
            updated = 0
            for jidlo in queryset.select_related("druh"):
                if jidlo.ikona:
                    continue
                jidlo.ikona = vychozi_ikona_jidla(
                    jidlo.nazev,
                    jidlo.druh.nazev if jidlo.druh_id and jidlo.druh else "",
                )
                jidlo.save(update_fields=["ikona"])
                updated += 1
            self.message_user(request, f"Hotovo: doplněno ikon u {updated} jídel.")
        elif operation == "fill_photos_ai":
            updated = 0
            skipped = 0
            failed = 0

            for jidlo in queryset.select_related("druh"):
                result = generate_food_photo_proposal(jidlo, overwrite=False, dry_run=False, timeout=90)
                if result.status == "updated":
                    updated += 1
                elif result.status == "failed":
                    failed += 1
                else:
                    skipped += 1

            self.message_user(
                request,
                (
                    "Hotovo: AI návrh fotky vytvořen u "
                    f"{updated} jídel, přeskočeno {skipped}, selhalo {failed}. "
                    "Návrhy čekají na schválení v sekci „Návrhy AI fotek jídel“. Pro běh je potřeba OPENAI_API_KEY."
                ),
            )
        else:
            self.message_user(request, "Vyber hromadnou operaci, kterou chceš provést.")

        return HttpResponseRedirect(f"{changelist_url}?{query_string}" if query_string else changelist_url)

    @admin.display(description="Náhled")
    def nahled(self, obj):
        if obj.foto:
            return format_html(
                '<img src="{}" style="width:36px;height:36px;object-fit:cover;border-radius:8px;" alt="">',
                obj.foto.url,
            )
        return format_html('<i class="{}" style="font-size:22px;color:#54ae43;"></i>', obj.vychozi_ikona)

    def get_queryset(self, request):
        queryset = (
            super()
            .get_queryset(request)
            .select_related("druh")
            .prefetch_related(
                "alergeny",
                "komponenty_jidla",
                "receptura",
                "komponenty_jidla__komponenta__suroviny",
                Prefetch(
                    "polozkajidelnicku_set",
                    queryset=PolozkaJidelnicku.objects.select_related("jidelnicek", "druh_jidla").order_by(
                        "-jidelnicek__platnost_od",
                        "-jidelnicek__platnost_do",
                    ),
                ),
            )
            .annotate(
                usage_count=Count("polozkajidelnicku", distinct=True),
                allergens_count=Count("alergeny", distinct=True),
            )
        )

        selected_kind = request.GET.get("druh", "").strip()
        photo_state = request.GET.get("foto", "").strip()
        usage_state = request.GET.get("pouziti", "").strip()
        readiness_state = request.GET.get("pripravenost", "").strip()
        allergen_state = request.GET.get("alergeny", "").strip()
        min_price = self._parse_decimal_param(request.GET.get("cena_od", ""))
        max_price = self._parse_decimal_param(request.GET.get("cena_do", ""))

        if selected_kind:
            try:
                queryset = queryset.filter(druh_id=int(selected_kind))
            except (TypeError, ValueError):
                queryset = queryset.none()

        if photo_state == "with":
            queryset = queryset.exclude(Q(foto__isnull=True) | Q(foto=""))
        elif photo_state == "without":
            queryset = queryset.filter(Q(foto__isnull=True) | Q(foto=""))

        if usage_state == "used":
            queryset = queryset.filter(polozkajidelnicku__isnull=False).distinct()
        elif usage_state == "unused":
            queryset = queryset.filter(polozkajidelnicku__isnull=True)

        if allergen_state == "with":
            queryset = queryset.filter(alergeny__isnull=False)
        elif allergen_state == "without":
            queryset = queryset.filter(alergeny__isnull=True)

        complete_q = (
            ~Q(foto__isnull=True)
            & ~Q(foto="")
            & (Q(kcal__isnull=False) | Q(bílkoviny__isnull=False) | Q(tuky__isnull=False) | Q(sacharidy__isnull=False))
            & Q(komponenty_jidla__isnull=False)
            & (Q(receptura__isnull=False) | Q(komponenty_jidla__komponenta__suroviny__isnull=False))
            & Q(alergeny__isnull=False)
        )
        if readiness_state == "ready":
            queryset = queryset.filter(complete_q)
        elif readiness_state == "incomplete":
            queryset = queryset.exclude(complete_q)

        if min_price is not None:
            queryset = queryset.filter(cena__gte=min_price)
        if max_price is not None:
            queryset = queryset.filter(cena__lte=max_price)

        queryset = queryset.distinct()
        return queryset.distinct()

    def ma_komponenty(self, obj):
        return obj.komponenty_jidla.exists()
    ma_komponenty.boolean = True
    ma_komponenty.short_description = "Komponenty?"

    def render_change_form(self, request, context, *args, **kwargs):
        obj = context.get("original")
        component_count = 0
        ingredient_count = 0
        has_components = False
        has_ingredients = False

        if obj and obj.pk:
            obj = (
                Jidlo.objects.filter(pk=obj.pk)
                .prefetch_related(
                    "receptura",
                    "komponenty_jidla__komponenta__suroviny",
                )
                .first()
            )
            if obj:
                components = list(obj.komponenty_jidla.all())
                component_count = len(components)
                component_ingredients_count = sum(
                    len(list(component.komponenta.suroviny.all()))
                    for component in components
                    if getattr(component, "komponenta_id", None)
                )
                ingredient_count = len(list(obj.receptura.all())) + component_ingredients_count
                has_components = component_count > 0
                has_ingredients = ingredient_count > 0

        context.update(
            {
                "food_form_summary": {
                    "has_components": has_components,
                    "components_count": component_count,
                    "has_ingredients": has_ingredients,
                    "ingredients_count": ingredient_count,
                }
            }
        )
        return super().render_change_form(request, context, *args, **kwargs)

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        response = super().changelist_view(request, extra_context=extra_context)
        if not hasattr(response, "context_data"):
            return response

        cl = response.context_data["cl"]
        search_query = request.GET.get("q", "").strip()
        selected_kind = request.GET.get("druh", "").strip()
        photo_state = request.GET.get("foto", "").strip()
        usage_state = request.GET.get("pouziti", "").strip()
        readiness_state = request.GET.get("pripravenost", "").strip()
        allergen_state = request.GET.get("alergeny", "").strip()
        min_price_raw = request.GET.get("cena_od", "").strip()
        max_price_raw = request.GET.get("cena_do", "").strip()

        food_cards = []
        page_photo_total = 0
        page_nutrition_total = 0
        page_component_total = 0
        page_ingredient_total = 0

        for food in cl.result_list:
            usages = list(food.polozkajidelnicku_set.all())
            has_nutrition = any(
                value is not None for value in (food.kcal, food.bílkoviny, food.tuky, food.sacharidy)
            )
            components = list(food.komponenty_jidla.all())
            recipe_items = list(food.receptura.all())
            component_names = []
            ingredient_names = []
            component_ingredients_count = sum(
                len(list(component.komponenta.suroviny.all()))
                for component in components
                if getattr(component, "komponenta_id", None)
            )
            ingredients_count = len(recipe_items) + component_ingredients_count
            for component in components:
                if getattr(component, "komponenta", None):
                    component_names.append(component.komponenta.nazev)
                    for raw in component.komponenta.suroviny.all():
                        if getattr(raw, "surovina", None):
                            ingredient_names.append(raw.surovina.nazev)
            for recipe_item in recipe_items:
                if getattr(recipe_item, "surovina", None):
                    ingredient_names.append(recipe_item.surovina.nazev)

            unique_component_names = list(dict.fromkeys(component_names))
            unique_ingredient_names = list(dict.fromkeys(ingredient_names))

            page_photo_total += 1 if food.foto else 0
            page_nutrition_total += 1 if has_nutrition else 0
            page_component_total += 1 if components else 0
            page_ingredient_total += 1 if ingredients_count else 0

            allergens = list(food.alergeny.all())
            visible_allergens = allergens[:6]
            readiness_total = 5
            readiness_filled = sum(
                1
                for ready in (
                    bool(food.foto),
                    has_nutrition,
                    bool(components),
                    ingredients_count > 0,
                    bool(allergens),
                )
                if ready
            )
            readiness_percent = int((readiness_filled / readiness_total) * 100)
            food_cards.append(
                {
                    "obj": food,
                    "preview_icon": food.vychozi_ikona,
                    "edit_url": reverse("admin:jidelnicek_jidlo_change", args=[food.pk]),
                    "kind_name": food.druh.nazev if food.druh_id and food.druh else "Bez druhu",
                    "kind_icon": food.druh.vychozi_ikona if food.druh_id and food.druh else "",
                    "price_label": f"{food.cena:.2f} Kč",
                    "allergens": visible_allergens,
                    "allergens_count": food.allergens_count or len(allergens),
                    "allergens_extra_count": max(len(allergens) - len(visible_allergens), 0),
                    "usage_count": food.usage_count or 0,
                    "has_photo": bool(food.foto),
                    "has_nutrition": has_nutrition,
                    "has_components": bool(components),
                    "components_count": len(components),
                    "components_preview": unique_component_names[:4],
                    "components_extra_count": max(len(unique_component_names) - 4, 0),
                    "has_ingredients": ingredients_count > 0,
                    "ingredients_count": ingredients_count,
                    "ingredients_preview": unique_ingredient_names[:7],
                    "ingredients_extra_count": max(len(unique_ingredient_names) - 7, 0),
                    "nutrition_label": self._nutrition_summary(food),
                    "readiness_filled": readiness_filled,
                    "readiness_total": readiness_total,
                    "readiness_percent": readiness_percent,
                }
            )

        response.context_data.update(
            {
                "food_cards": food_cards,
                "food_total_count": cl.result_count,
                "food_cards_on_page": len(food_cards),
                "food_page_photo_total": page_photo_total,
                "food_page_nutrition_total": page_nutrition_total,
                "food_page_component_total": page_component_total,
                "food_page_ingredient_total": page_ingredient_total,
                "food_search_query": search_query,
                "food_selected_kind": selected_kind,
                "food_photo_state": photo_state,
                "food_usage_state": usage_state,
                "food_readiness_state": readiness_state,
                "food_allergen_state": allergen_state,
                "food_min_price": min_price_raw,
                "food_max_price": max_price_raw,
                "food_kind_options": DruhJidla.objects.order_by("poradi", "nazev"),
                "food_filters_active": bool(
                    search_query
                    or selected_kind
                    or photo_state
                    or usage_state
                    or readiness_state
                    or allergen_state
                    or min_price_raw
                    or max_price_raw
                ),
                "food_bulk_querystring": request.GET.urlencode(),
            }
        )
        return response

    def _nutrition_summary(self, obj):
        values = []
        if obj.kcal is not None:
            values.append(f"{obj.kcal:.0f} kcal")
        if obj.bílkoviny is not None:
            values.append(f"B {obj.bílkoviny:.1f} g")
        if obj.tuky is not None:
            values.append(f"T {obj.tuky:.1f} g")
        if obj.sacharidy is not None:
            values.append(f"S {obj.sacharidy:.1f} g")
        return " • ".join(values[:3])

    def get_search_results(self, request, queryset, search_term):
        queryset, use_distinct = super().get_search_results(request, queryset, search_term)
        druh_jidla = request.GET.get("druh_jidla")
        if druh_jidla:
            try:
                queryset = queryset.filter(druh_id=int(druh_jidla))
            except (TypeError, ValueError):
                queryset = queryset.none()
        return queryset, use_distinct

    def alergeny_list(self, obj):
        return ", ".join([a.nazev for a in obj.alergeny.all()])
    alergeny_list.short_description = 'Alergeny'

    def ceny_po_dotacich(self, obj):
        ceny = []
        politiky = DotacniPolitika.objects.select_related('skupina').all()

        for politika in politiky:
            try:
                prepis = DotaceProJidelniskouSkupinu.objects.get(
                    dotacni_politika=politika,
                    jidelniskova_skupina=obj.druh
                )
                procento = (prepis.procento if prepis.procento is not None else politika.procento) / 100
                castka = prepis.castka if prepis.castka is not None else politika.castka
            except DotaceProJidelniskouSkupinu.DoesNotExist:
                procento = politika.procento / 100
                castka = politika.castka

            cena_sleva = obj.cena * (1 - procento) - castka
            if cena_sleva < 0:
                cena_sleva = 0
            ceny.append({
                'skupina': politika.skupina.name,
                'cena': f"{cena_sleva:.2f} Kč"
            })

        rows_html = "".join(
            f"<tr>"
            f"<td style='padding: 2px 6px; border: 1px solid #ddd; font-size: 11px;'>{c['skupina']}</td>"
            f"<td style='padding: 2px 6px; border: 1px solid #ddd; text-align: right; font-weight: 600; font-size: 11px;'>{c['cena']}</td>"
            f"</tr>"
            for c in ceny
        )
        table_html = f"""
        <table style='border-collapse: collapse; width: 100%; border: 1px solid #ccc; font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;'>
            <thead>
                <tr>
                    <th style='border: 1px solid #ccc; font-size: 11px; padding: 4px 6px; background: #f4f6f9;'>Skupina</th>
                    <th style='border: 1px solid #ccc; font-size: 11px; padding: 4px 6px; background: #f4f6f9; text-align: right;'>Cena po dotaci</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
        """
        return format_html(table_html)

    ceny_po_dotacich.short_description = "Ceny po dotacích"

    @admin.action(description="Doplnit výchozí ikony podle názvu a druhu jídla")
    def doplnit_ikony_jidel(self, request, queryset):
        aktualizovano = 0
        for jidlo in queryset.select_related("druh"):
            if jidlo.ikona:
                continue
            jidlo.ikona = vychozi_ikona_jidla(
                jidlo.nazev,
                jidlo.druh.nazev if jidlo.druh_id else "",
            )
            jidlo.save(update_fields=["ikona"])
            aktualizovano += 1
        self.message_user(request, f"Doplněno ikon u jídel: {aktualizovano}.")

    # ==== AUTO‑PLU ====

    def _ensure_plu_for_jidlo(self, jidlo):
        # DPH 12 %
        dph_12, _ = DPHSkupina.objects.get_or_create(
            sazba=12,
            defaults={"nazev": "Jídlo 12 %"},
        )
        # výchozí kategorie
        kategorie_menu, _ = PLUKategorie.objects.get_or_create(
            nazev="Jídelna"
        )

        plu, created = PLUPolozka.objects.get_or_create(
            jidlo=jidlo,
            defaults={
                "nazev": jidlo.nazev,
                "cena": jidlo.cena,
                "dph_skupina": dph_12,
                "kategorie": kategorie_menu,
                "typ": PLUPolozka.TYP_RECEPTURA,
                "aktivni": True,
            },
        )

        if not created:
            plu.nazev = jidlo.nazev
            plu.cena = jidlo.cena
            plu.dph_skupina = dph_12
            if plu.kategorie is None:
                plu.kategorie = kategorie_menu
            plu.aktivni = True
            plu.save(
                update_fields=["nazev", "cena", "dph_skupina", "kategorie", "aktivni"]
            )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        self._ensure_plu_for_jidlo(obj)

    @admin.action(description="Vygenerovat / aktualizovat PLU pro vybraná jídla")
    def vygenerovat_plu_pro_jidla(self, request, queryset):
        for jidlo in queryset:
            self._ensure_plu_for_jidlo(jidlo)


class PolozkaJidelnickuInline(admin.StackedInline):
    model = PolozkaJidelnicku
    form = PolozkaJidelnickuAdminForm
    formset = PolozkaJidelnickuInlineFormSet
    extra = 0
    fields = ("druh_jidla", "jidlo", "menu_item_summary")
    readonly_fields = ("menu_item_summary",)
    classes = ("menu-builder-inline",)

    def get_extra(self, request, obj=None, **kwargs):
        if not obj or not obj.pk:
            return 0
        existing_ids = set(obj.polozky.values_list("druh_jidla_id", flat=True))
        return DruhJidla.objects.exclude(pk__in=existing_ids).count()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "druh_jidla":
            kwargs["queryset"] = DruhJidla.objects.order_by("poradi", "nazev")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description="Souhrn vybraného jídla")
    def menu_item_summary(self, obj):
        if not obj or not getattr(obj, "jidlo_id", None):
            return format_html(
                '<div class="menu-builder-empty">Vyber jídlo a souhrn ceny, alergenů a viditelnosti se doplní automaticky.</div>'
            )

        jidlo = obj.jidlo
        druh = jidlo.druh.nazev if jidlo.druh_id else "Bez druhu"
        alergeny = ", ".join(jidlo.alergeny.values_list("nazev", flat=True)) or "Bez alergenů"
        visible_groups = ", ".join(
            jidlo.druh.viditelne_pro_skupiny.values_list("name", flat=True)
        ) if jidlo.druh_id and jidlo.druh.viditelne_pro_skupiny.exists() else "Všichni"
        return format_html(
            '<div class="menu-builder-summary" data-menu-builder-summary>'
            '<span class="menu-builder-pill kind">Druh: {}</span>'
            '<span class="menu-builder-pill price">Cena: {} Kč</span>'
            '<span class="menu-builder-pill allergens">Alergeny: {}</span>'
            '<span class="menu-builder-pill groups">Uvidí: {}</span>'
            '</div>',
            druh,
            f"{jidlo.cena:.2f}",
            alergeny,
            visible_groups,
        )


@admin.register(Jidelnicek)
class JidelnicekAdmin(admin.ModelAdmin):
    change_form_template = "admin/jidelnicek/jidelnicek/change_form.html"
    change_list_template = "admin/jidelnicek/jidelnicek/change_list.html"
    list_display = ('platnost_od', 'platnost_do', 'obsah_jidelnicku')
    search_fields = ("polozky__jidlo__nazev", "polozky__druh_jidla__nazev")
    ordering = ("-platnost_od", "-platnost_do")
    list_per_page = 20
    inlines = [PolozkaJidelnickuInline]

    class Media:
        css = {"all": ("jidelnicek/css/menu_builder_admin.css", "jidelnicek/css/menu_list_admin.css")}
        js = ("jidelnicek/js/menu_builder_admin.js",)

    @admin.display(description='Obsah jídelníčku')
    def obsah_jidelnicku(self, obj):
        polozky = obj.polozky.select_related('druh_jidla', 'jidlo').all()
        if not polozky:
            return "-"
        rows = ""
        for p in polozky:
            ikonovy_html = ""
            if p.druh_jidla.ikona:
                ikonovy_html = f'<i class="{p.druh_jidla.ikona}" style="margin-right:5px;"></i>'
            rows += f"<tr><td>{ikonovy_html}{p.druh_jidla}</td><td>{p.jidlo}</td></tr>"

        table_html = f"""
        <table style="border-collapse: collapse; border: 1px solid #ddd;">
            <thead>
                <tr>
                    <th style="border: 1px solid #ddd; padding: 2px 5px;">Druh jídla</th>
                    <th style="border: 1px solid #ddd; padding: 2px 5px;">Jídlo</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
        """
        return format_html(table_html)

    def get_queryset(self, request):
        queryset = (
            super()
            .get_queryset(request)
            .prefetch_related(
                "polozky__druh_jidla",
                "polozky__jidlo",
            )
            .annotate(items_count=Count("polozky", distinct=True))
        )
        food_lookup = request.GET.get("q", "").strip() or request.GET.get("food_lookup", "").strip()
        selected_kind = request.GET.get("druh", "").strip()
        content_state = request.GET.get("obsah", "").strip()
        date_from = request.GET.get("od", "").strip()
        date_to = request.GET.get("do", "").strip()

        if food_lookup:
            queryset = queryset.filter(polozky__jidlo__nazev__icontains=food_lookup).distinct()
        if selected_kind:
            try:
                queryset = queryset.filter(polozky__druh_jidla_id=int(selected_kind))
            except (TypeError, ValueError):
                queryset = queryset.none()
        if content_state == "empty":
            queryset = queryset.filter(items_count=0)
        elif content_state == "with":
            queryset = queryset.filter(items_count__gt=0)
        if date_from:
            queryset = queryset.filter(platnost_do__gte=date_from)
        if date_to:
            queryset = queryset.filter(platnost_od__lte=date_to)
        return queryset

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        response = super().changelist_view(request, extra_context=extra_context)
        if not hasattr(response, "context_data"):
            return response

        cl = response.context_data["cl"]
        food_lookup = request.GET.get("q", "").strip() or request.GET.get("food_lookup", "").strip()
        selected_kind = request.GET.get("druh", "").strip()
        content_state = request.GET.get("obsah", "").strip()
        date_from = request.GET.get("od", "").strip()
        date_to = request.GET.get("do", "").strip()
        menus = []
        total_items = 0
        matched_occurrences = 0

        for menu in cl.result_list:
            grouped_items = []
            for item in menu.polozky.all():
                is_match = bool(food_lookup and food_lookup.lower() in item.jidlo.nazev.lower())
                if is_match:
                    matched_occurrences += 1
                grouped_items.append(
                    {
                        "kind": item.druh_jidla.nazev,
                        "kind_icon": item.druh_jidla.ikona,
                        "food": item.jidlo.nazev,
                        "is_match": is_match,
                    }
                )

            visible_items = grouped_items
            if food_lookup:
                visible_items = [entry for entry in grouped_items if entry["is_match"]]

            total_items += len(grouped_items)
            menus.append(
                {
                    "obj": menu,
                    "edit_url": reverse("admin:jidelnicek_jidelnicek_change", args=[menu.pk]),
                    "items": visible_items,
                    "all_items_count": len(grouped_items),
                    "items_count": len(grouped_items),
                    "kind_count": len({entry["kind"] for entry in grouped_items}),
                    "matched_count": sum(1 for entry in grouped_items if entry["is_match"]),
                    "day_label": menu.platnost_od.strftime("%d.%m.%Y"),
                    "range_label": (
                        menu.platnost_od.strftime("%d.%m.%Y")
                        if menu.platnost_od == menu.platnost_do
                        else f"{menu.platnost_od.strftime('%d.%m.%Y')} - {menu.platnost_do.strftime('%d.%m.%Y')}"
                    ),
                }
            )

        response.context_data.update(
            {
                "food_lookup": food_lookup,
                "search_query": food_lookup,
                "menu_cards": menus,
                "menu_total_count": cl.result_count,
                "menu_total_items": total_items,
                "menu_matched_occurrences": matched_occurrences,
                "menu_active_filters": {
                    "food_lookup": food_lookup,
                },
                "menu_selected_kind": selected_kind,
                "menu_content_state": content_state,
                "menu_date_from": date_from,
                "menu_date_to": date_to,
                "menu_kind_options": DruhJidla.objects.order_by("poradi", "nazev"),
                "menu_filters_active": bool(
                    food_lookup or selected_kind or content_state or date_from or date_to
                ),
                "last_menu_import": self._get_last_menu_import_safe(),
            }
        )
        return response

    def _get_last_menu_import_safe(self):
        try:
            return MenuImportRun.objects.order_by("-started_at").first()
        except (ProgrammingError, OperationalError):
            return None

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path(
                "jidlo-meta/<int:jidlo_id>/",
                self.admin_site.admin_view(self.jidlo_meta_api),
                name="jidelnicek_jidlo_meta",
            ),
            path(
                "bulk-apply/",
                self.admin_site.admin_view(self.bulk_apply_view),
                name="jidelnicek_jidelnicek_bulk_apply",
            ),
        ]
        return my_urls + urls

    def bulk_apply_view(self, request):
        changelist_url = reverse("admin:jidelnicek_jidelnicek_changelist")
        query_string = request.GET.urlencode()

        if request.method != "POST":
            return HttpResponseRedirect(f"{changelist_url}?{query_string}" if query_string else changelist_url)

        if not self.has_delete_permission(request):
            self.message_user(request, "Nemáš oprávnění pro hromadné operace jídelníčků.")
            return HttpResponseRedirect(f"{changelist_url}?{query_string}" if query_string else changelist_url)

        operation = request.POST.get("bulk_operation", "").strip()
        queryset = self.get_queryset(request)

        if operation == "delete_empty":
            empty_qs = queryset.filter(polozky__isnull=True).distinct()
            deleted_count = empty_qs.count()
            if deleted_count:
                empty_qs.delete()
                self.message_user(request, f"Hotovo: smazáno {deleted_count} prázdných jídelníčků.")
            else:
                self.message_user(request, "V aktuálním výběru nejsou žádné prázdné jídelníčky.")
        else:
            self.message_user(request, "Vyber hromadnou operaci, kterou chceš provést.")

        return HttpResponseRedirect(f"{changelist_url}?{query_string}" if query_string else changelist_url)

    def render_change_form(self, request, context, *args, **kwargs):
        context["jidlo_meta_url_template"] = reverse(
            "admin:jidelnicek_jidlo_meta", args=[0]
        )
        return super().render_change_form(request, context, *args, **kwargs)

    def jidlo_meta_api(self, request, jidlo_id):
        try:
            jidlo = Jidlo.objects.select_related("druh").prefetch_related("alergeny", "druh__viditelne_pro_skupiny").get(pk=jidlo_id)
        except Jidlo.DoesNotExist:
            return JsonResponse({"error": "not_found"}, status=404)

        if not jidlo.druh_id:
            return JsonResponse(
                {
                    "id": jidlo.pk,
                    "nazev": jidlo.nazev,
                    "druh_id": None,
                    "druh": "",
                    "cena": f"{jidlo.cena:.2f}",
                    "alergeny": list(jidlo.alergeny.values_list("nazev", flat=True)),
                    "visible_groups": [],
                }
            )

        return JsonResponse(
            {
                "id": jidlo.pk,
                "nazev": jidlo.nazev,
                "druh_id": jidlo.druh_id,
                "druh": jidlo.druh.nazev,
                "cena": f"{jidlo.cena:.2f}",
                "alergeny": list(jidlo.alergeny.values_list("nazev", flat=True)),
                "visible_groups": list(jidlo.druh.viditelne_pro_skupiny.values_list("name", flat=True)),
            }
        )

@admin.register(PolozkaJidelnicku)
class PolozkaJidelnickuAdmin(admin.ModelAdmin):
    form = PolozkaJidelnickuAdminForm
    list_display = ("jidelnicek", "druh_jidla", "jidlo")
    list_filter = ("jidelnicek", "druh_jidla")
    search_fields = ("jidlo__nazev",)


@admin.register(MenuImportRun)
class MenuImportRunAdmin(admin.ModelAdmin):
    list_display = (
        "started_at",
        "source",
        "status",
        "menu_days",
        "menus_created",
        "items_created",
        "foods_created",
        "triggered_by",
    )
    list_filter = ("source", "status", "dry_run")
    search_fields = ("summary", "error_message", "triggered_by__username")
    readonly_fields = (
        "source",
        "status",
        "started_at",
        "finished_at",
        "triggered_by",
        "dry_run",
        "rows_read",
        "rows_after_merge",
        "menu_days",
        "menus_created",
        "foods_created",
        "items_created",
        "summary",
        "error_message",
    )
    ordering = ("-started_at",)

    def has_add_permission(self, request):
        return False


@admin.register(JidloPhotoProposal)
class JidloPhotoProposalAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "jidlo",
        "status_badge",
        "preview",
        "model_name",
        "reviewed_at",
        "reviewed_by",
    )
    list_filter = ("status", "created_at", "model_name")
    search_fields = ("jidlo__nazev", "prompt", "error_message")
    readonly_fields = (
        "jidlo",
        "image",
        "status",
        "prompt",
        "model_name",
        "error_message",
        "created_at",
        "reviewed_at",
        "reviewed_by",
        "preview_large",
    )
    actions = ("approve_and_apply", "reject_selected")
    ordering = ("-created_at",)

    @admin.display(description="Stav")
    def status_badge(self, obj):
        colors = {
            JidloPhotoProposal.STATUS_PENDING: "#2f8f2f",
            JidloPhotoProposal.STATUS_APPROVED: "#1f7a8c",
            JidloPhotoProposal.STATUS_REJECTED: "#b02a37",
            JidloPhotoProposal.STATUS_APPLIED: "#6c757d",
        }
        color = colors.get(obj.status, "#6c757d")
        return format_html(
            '<span style="display:inline-block;padding:3px 10px;border-radius:999px;background:{}22;color:{};font-weight:700;">{}</span>',
            color,
            color,
            obj.get_status_display(),
        )

    @admin.display(description="Náhled")
    def preview(self, obj):
        if not obj.image:
            return "—"
        return format_html(
            '<img src="{}" style="width:48px;height:48px;object-fit:cover;border-radius:10px;border:1px solid #d9e2d1;" alt="">',
            obj.image.url,
        )

    @admin.display(description="Velký náhled")
    def preview_large(self, obj):
        if not obj.image:
            return "Návrh neobsahuje obrázek."
        return format_html(
            '<img src="{}" style="max-width:420px;width:100%;height:auto;border-radius:12px;border:1px solid #d9e2d1;" alt="">',
            obj.image.url,
        )

    @admin.action(description="Schválit a propsat do fotky jídla")
    def approve_and_apply(self, request, queryset):
        updated = 0
        failed = 0
        skipped = 0
        for proposal in queryset.select_related("jidlo"):
            if proposal.status == JidloPhotoProposal.STATUS_REJECTED:
                skipped += 1
                continue
            result = apply_photo_proposal(proposal, reviewed_by=request.user)
            if result.status == "updated":
                updated += 1
            elif result.status == "failed":
                failed += 1
            else:
                skipped += 1

        self.message_user(
            request,
            f"Schváleno a propsáno: {updated}, přeskočeno: {skipped}, chyby: {failed}.",
        )

    @admin.action(description="Zamítnout vybrané návrhy")
    def reject_selected(self, request, queryset):
        changed = 0
        skipped = 0
        for proposal in queryset:
            if proposal.status == JidloPhotoProposal.STATUS_APPLIED:
                skipped += 1
                continue
            reject_photo_proposal(proposal, reviewed_by=request.user)
            changed += 1
        self.message_user(
            request,
            f"Zamítnuto: {changed}, přeskočeno (už použité): {skipped}.",
        )

    def has_add_permission(self, request):
        return False
