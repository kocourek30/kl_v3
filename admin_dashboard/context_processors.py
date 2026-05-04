from django.urls import NoReverseMatch, reverse

from .services import get_blocked_module_for_path


def frontend_feature_flags(request):
    ankety_path = "/ankety/"
    try:
        ankety_path = reverse("ankety:moje_ankety")
    except NoReverseMatch:
        pass

    return {
        "frontend_ankety_enabled": get_blocked_module_for_path(ankety_path) is None,
    }
