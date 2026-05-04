from django.contrib import admin
from django.urls import include, path
from kliknijidlo import settings
from users import views
from django.conf.urls.static import static
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('frontend.urls')),  # zůstává, pokud chceš homepage/login atd.
    path('accounts/', include('django.contrib.auth.urls')),
    path('profile/', views.user_profile_view, name='user-profile'),
    path('jidelnicek/', include('jidelnicek.urls')), 
    path('users/', include('users.urls', namespace='users')),
    path('vydej/', include('vydej_frontend.urls')),  
    path('accounts/profile/', RedirectView.as_view(url='/jidelnicek/dashboard/', permanent=False)),
    path("pokladna/", include("pokladna.urls")),
    path("ankety/", include("ankety.urls")),

    
] 


# Servírování static v DEBUG módu
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
