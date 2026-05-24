from django.shortcuts import redirect
from django.utils.cache import add_never_cache_headers
from django.urls import reverse


class ForcePasswordChangeMiddleware:
    """Vynutí změnu hesla, pokud má uživatel zapnutý příznak must_change_password."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated and getattr(user, "must_change_password", False):
            force_change_url = reverse("users:force-password-change")
            exempt_paths = {
                force_change_url,
                reverse("logout"),
                reverse("users:logout"),
                reverse("login"),
                reverse("rfid-login"),
                reverse("rfid-login-api"),
            }
            static_url = "/static/"
            media_url = "/media/"
            if request.path not in exempt_paths and not request.path.startswith(static_url) and not request.path.startswith(media_url):
                return redirect(f"{force_change_url}?next={request.path}")

        return self.get_response(request)


class NoCacheAuthenticatedMiddleware:
    """Zabrání prohlížeči vracet přihlášené stránky z cache po logoutu."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            add_never_cache_headers(response)
        return response
