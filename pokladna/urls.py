from django.urls import path
from . import views

app_name = "pokladna"

urlpatterns = [
    path("<int:pokladna_id>/", views.pokladna_view, name="pokladna_view"),
    path(
        "<int:pokladna_id>/search-zakaznik/",
        views.pokladna_zakaznik_search,
        name="pokladna_zakaznik_search",
    ),
]
