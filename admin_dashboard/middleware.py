from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse

from .services import get_blocked_admin_area_for_request, get_blocked_module_for_path


class ModuleAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.path.rstrip("/") == "/admin"
            and request.user.is_authenticated
            and request.GET.get("classic") != "1"
        ):
            if request.user.is_superuser:
                return HttpResponseRedirect(reverse("admin:admin_dashboard_dashboardtask_changelist"))
            if request.user.is_staff:
                return HttpResponseRedirect(reverse("admin:provoz_jidelny_provoznidashboard_changelist"))

        try:
            from licencovani.services import is_admin_superadmin_only_mode

            if (
                request.path.startswith("/admin/")
                and request.user.is_authenticated
                and request.user.is_staff
                and not request.user.is_superuser
                and is_admin_superadmin_only_mode()
            ):
                context = {
                    "module_name": "Administrace systému",
                    "module_description": "Po skončení platnosti licence a ochranné lhůty je administrace přístupná jen superadminům.",
                    "blocked_by_groups": False,
                    "blocked_by_license": True,
                    "blocked_by_license_admin": True,
                }
                response = TemplateResponse(
                    request,
                    "admin/admin_dashboard/module_disabled.html",
                    context,
                    status=403,
                )
                response.render()
                return response
        except Exception:
            pass

        blocked_area = get_blocked_admin_area_for_request(request)
        if blocked_area:
            context = {
                "module_name": blocked_area.name,
                "module_description": blocked_area.description,
                "blocked_by_groups": True,
            }
            response = TemplateResponse(
                request,
                "admin/admin_dashboard/module_disabled.html",
                context,
                status=403,
            )
            response.render()
            return response

        blocked_module = get_blocked_module_for_path(request.path)
        if blocked_module:
            blocked_by_license = False
            try:
                from licencovani.services import LICENSABLE_MODULE_SLUGS, is_module_licensed

                blocked_by_license = blocked_module.slug in LICENSABLE_MODULE_SLUGS and not is_module_licensed(blocked_module.slug)
            except Exception:
                blocked_by_license = False
            context = {
                "module_name": blocked_module.name,
                "module_description": blocked_module.description,
                "blocked_by_groups": False,
                "blocked_by_license": blocked_by_license,
                "blocked_by_license_admin": False,
            }
            response = TemplateResponse(
                request,
                "admin/admin_dashboard/module_disabled.html",
                context,
                status=403,
            )
            response.render()
            return response
        return self.get_response(request)
