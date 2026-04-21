from django.template.response import TemplateResponse

from .services import get_blocked_admin_area_for_request, get_blocked_module_for_path


class ModuleAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
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
            context = {
                "module_name": blocked_module.name,
                "module_description": blocked_module.description,
                "blocked_by_groups": False,
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
