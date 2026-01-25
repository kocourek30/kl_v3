# vydej_frontend/urls.py
from django.urls import path
from . import views

app_name = 'vydej_frontend'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('issue-order/<int:order_id>/', views.issue_order, name='issue_order'),
    path('refresh-data/', views.refresh_data, name='refresh_data'),
    path('rfid-scan/', views.rfid_scan, name='rfid_scan'),
    path('settings/', views.get_vydej_settings, name='get_vydej_settings'),
    path('issue/<int:order_id>/', views.issue_order, name='issue_order'),
    path('get-order-detail/<int:order_id>/', views.get_order_detail, name='get_order_detail'),
    path('kiosk-login/', views.auto_login_kiosk, name='kiosk_login'),
    path('rfid/debug/', views.rfid_debug, name='rfid_debug'),
    path('issue-item/<int:item_id>/', views.issue_single_item, name='issue_single_item'),


]
