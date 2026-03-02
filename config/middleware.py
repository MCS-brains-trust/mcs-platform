"""
Custom middleware for security enforcement.
"""
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse


def csrf_failure_view(request, reason=""):
    """
    Custom CSRF failure handler that returns JSON for AJAX/fetch requests
    instead of Django's default HTML 403 page.
    """
    is_ajax = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.content_type == "application/json"
        or "application/json" in request.headers.get("Accept", "")
    )
    if is_ajax:
        return JsonResponse(
            {"status": "error", "error": "CSRF token missing or expired. Please refresh the page and try again."},
            status=403,
        )
    # Fall back to Django's default HTML response for non-AJAX requests
    from django.middleware.csrf import REASON_NO_CSRF_COOKIE, REASON_BAD_TOKEN
    from django.template.response import TemplateResponse
    from django.utils.translation import gettext as _
    c = {
        "title": _("Forbidden"),
        "main": _("CSRF verification failed. Request aborted."),
        "reason": reason,
    }
    return TemplateResponse(request, "403_csrf.html", context=c, status=403)


class Require2FAMiddleware:
    """
    Middleware that enforces 2FA setup for all authenticated users.
    Users without 2FA configured are redirected to the 2FA setup page.
    """

    EXEMPT_URLS = [
        "/accounts/login/",
        "/accounts/logout/",
        "/accounts/totp-verify/",
        "/accounts/setup-2fa/",
        "/admin/",
        "/static/",
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.user.has_2fa:
            # Allow exempt URLs
            path = request.path
            if not any(path.startswith(url) for url in self.EXEMPT_URLS):
                # Allow signup URLs (they include token)
                if "/accounts/signup/" not in path:
                    # Return JSON for AJAX requests instead of redirect
                    is_ajax = (
                        request.headers.get("X-Requested-With") == "XMLHttpRequest"
                        or request.content_type == "application/json"
                    )
                    if is_ajax:
                        return JsonResponse(
                            {"status": "error", "error": "2FA setup required. Please refresh the page."},
                            status=403,
                        )
                    return redirect(reverse("accounts:setup_2fa"))

        return self.get_response(request)
