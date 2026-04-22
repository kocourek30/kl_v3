from .services import get_license_footer_context


def admin_license_footer(request):
    return {
        "admin_license_footer": get_license_footer_context(),
    }

