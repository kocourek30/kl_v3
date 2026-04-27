from django.urls import path

from . import views

app_name = "ankety"

urlpatterns = [
    path("", views.moje_ankety, name="moje_ankety"),
    path("mesicni-volba/", views.mesicni_volba, name="mesicni_volba"),
    path("hodnotit/<int:order_item_id>/", views.hodnotit_jidlo, name="hodnotit_jidlo"),
]
