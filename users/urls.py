from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    path('force-password-change/', views.force_password_change_view, name='force-password-change'),
    path('profile/', views.user_profile_view, name='user-profile'),
    path('history/', views.consumption_history_view, name='consumption-history'),
    path('account-history/', views.account_history_view, name='account-history'),  # ✅ NOVÉ
    path('receipt/<int:order_id>/', views.receipt_pdf_view, name='receipt-pdf'),
    path('receipt/<int:order_id>/download/', views.receipt_pdf_download, name='receipt-pdf-download'),
    path('logout/', views.logout_view, name='logout'),
    
]
