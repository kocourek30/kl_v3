from django.urls import path
from . import views

app_name = "pokladna"

urlpatterns = [
    path("login/", views.pokladna_login, name="pokladna_login"),
    path("<int:pokladna_id>/prehled/", views.pokladna_prehled, name="pokladna_prehled"),
    path("<int:pokladna_id>/", views.pokladna_view, name="pokladna_view"),
    path("<int:pokladna_id>/ucet/", views.pokladna_ucet, name="pokladna_ucet"),
    path("<int:pokladna_id>/vklad/", views.pokladna_vklad_konto, name="pokladna_vklad_konto"),
    path("<int:pokladna_id>/uzavrene-ucty/", views.pokladna_uzavrene_ucty, name="pokladna_uzavrene_ucty"),
    path("<int:pokladna_id>/financni-report/", views.pokladna_financni_report, name="pokladna_financni_report"),
    path("<int:pokladna_id>/uzaverka/", views.pokladna_uzaverka, name="pokladna_uzaverka"),
    path("<int:pokladna_id>/uzaverka/<int:uzaverka_id>/", views.pokladna_uzaverka_detail, name="pokladna_uzaverka_detail"),
    path("<int:pokladna_id>/doklad/<int:doklad_id>/", views.pokladna_doklad_detail, name="pokladna_doklad_detail"),
    path("<int:pokladna_id>/doklad/<int:doklad_id>/storno/", views.pokladna_stornovat_doklad, name="pokladna_stornovat_doklad"),
    path("<int:pokladna_id>/qr/<int:doklad_id>/", views.pokladna_qr_platba, name="pokladna_qr_platba"),
    path(
        "<int:pokladna_id>/search-zakaznik/",
        views.pokladna_zakaznik_search,
        name="pokladna_zakaznik_search",
    ),
]
