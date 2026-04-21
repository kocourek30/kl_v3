from django.contrib.auth.models import Group


def get_effective_user_groups(user):
    """
    Vrátí všechny Django skupiny relevantní pro oprávnění uživatele.

    Vedle přímého členství v auth.Group zohledňuje i vazbu přes
    `stravovaci_skupina -> django_group`.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return []

    groups = list(user.groups.all())
    stravovaci = getattr(user, "stravovaci_skupina", None)
    linked_group = getattr(stravovaci, "django_group", None) if stravovaci else None

    if linked_group and all(group.pk != linked_group.pk for group in groups):
        groups.append(linked_group)

    return groups


def get_primary_effective_group(user):
    groups = get_effective_user_groups(user)
    return groups[0] if groups else None


def get_first_group_setting(user, related_name="nastaveni"):
    for group in get_effective_user_groups(user):
        try:
            return getattr(group, related_name)
        except Exception:
            continue
    return None


def user_in_group(user, group: Group | None):
    if not group:
        return False
    return any(candidate.pk == group.pk for candidate in get_effective_user_groups(user))
