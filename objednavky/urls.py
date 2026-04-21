"""
Legacy shim pro případné staré odkazy.

Aktivní uživatelský tok objednávání obsluhuje modul `jidelnicek`.
`objednavky` zůstává doménová a administrativní vrstva.
"""
from django.urls import path
from jidelnicek import views as jidelnicek_views

urlpatterns = [
    path('order-create/', jidelnicek_views.order_create_view, name='order_create'),
    path('order-delete/', jidelnicek_views.order_delete_view, name='order_delete'),
]
