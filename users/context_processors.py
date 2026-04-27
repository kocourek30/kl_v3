from decimal import Decimal
from .models import CustomUser
from .group_utils import get_primary_effective_group


def _format_role_label(user):
    if not getattr(user, "is_authenticated", False):
        return ""
    if getattr(user, "is_superuser", False):
        return "Superadmin"

    group = get_primary_effective_group(user)
    if not group:
        return "Bez přiřazené role"

    name = group.name
    for prefix in ("Role • ", "Admin • "):
        if name.startswith(prefix):
            return name.replace(prefix, "", 1)
    return name


def admin_user_identity(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {
            "admin_user_display_name": "",
            "admin_user_role_label": "",
        }

    full_name = (user.get_full_name() or "").strip()
    display_name = full_name or user.username
    return {
        "admin_user_display_name": display_name,
        "admin_user_role_label": _format_role_label(user),
    }

def user_balance(request):
    if request.user.is_authenticated:
        return {'user_balance': request.user.aktualni_zustatek}
    return {'user_balance': Decimal('0')}  # ✅ IMPORT PŘIDÁN!
