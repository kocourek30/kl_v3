from django.contrib import admin

from .services import (
    filter_admin_app_items_for_user,
    get_disabled_admin_app_labels,
    get_restricted_admin_app_labels_for_user,
    is_menu_link_visible_for_user,
)


def patch_admin_site():
    site = admin.site
    if getattr(site, "_admin_dashboard_patched", False):
        return

    original_get_app_list = site.get_app_list

    def get_app_list(request, app_label=None):
        app_list = original_get_app_list(request, app_label)
        disabled_labels = get_disabled_admin_app_labels()
        restricted_labels = get_restricted_admin_app_labels_for_user(request.user)
        hidden_labels = disabled_labels | restricted_labels
        if hidden_labels:
            app_list = [app for app in app_list if app.get("app_label") not in hidden_labels]
        return filter_admin_app_items_for_user(request.user, app_list)

    site.get_app_list = get_app_list

    try:
        from jazzmin.templatetags import jazzmin as jazzmin_tags

        original_get_top_menu = getattr(jazzmin_tags.get_top_menu, "__wrapped__", jazzmin_tags.get_top_menu)
        original_get_side_menu = getattr(jazzmin_tags.get_side_menu, "__wrapped__", jazzmin_tags.get_side_menu)

        def get_top_menu(user, admin_site="admin"):
            menu = original_get_top_menu(user, admin_site=admin_site)
            filtered_menu = []

            for link in menu:
                children = [
                    child
                    for child in (link.get("children") or [])
                    if is_menu_link_visible_for_user(user, child.get("url", ""))
                ]
                if link.get("children"):
                    if not children:
                        continue
                    link = {**link, "children": children}
                    filtered_menu.append(link)
                    continue

                if is_menu_link_visible_for_user(user, link.get("url", "")):
                    filtered_menu.append(link)

            return filtered_menu

        jazzmin_tags.get_top_menu = get_top_menu
        jazzmin_tags.register.simple_tag(get_top_menu, name="get_top_menu")

        def get_side_menu(context, using="available_apps"):
            menu = original_get_side_menu(context, using=using)
            try:
                user = context["request"].user
            except Exception:
                return menu
            return filter_admin_app_items_for_user(user, menu)

        jazzmin_tags.get_side_menu = get_side_menu
        jazzmin_tags.register.simple_tag(get_side_menu, takes_context=True, name="get_side_menu")
    except Exception:
        pass

    site._admin_dashboard_patched = True
