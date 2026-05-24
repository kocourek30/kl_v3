from django.urls import path

from . import views

app_name = "knowledge_base"

urlpatterns = [
    path("", views.index, name="index"),
    path("<path:doc_path>/", views.document, name="document"),
]

