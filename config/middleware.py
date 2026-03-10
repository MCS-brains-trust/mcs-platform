"""
Custom middleware for security enforcement.
"""
import ipaddress
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


class SetRemoteAddrFromForwardedFor:
    """
    Populates REMOTE_ADDR from the first valid IP in the X-Forwarded-For header
    when the server is running behind a reverse proxy (e.g. Nginx) and
    REMOTE_ADDR is empty or not a valid IP address.

    This is required because django-ratelimit uses REMOTE_ADDR to key
    per-IP rate limits. When Gunicorn sits behind Nginx via a Unix socket,
    REMOTE_ADDR can be empty, causing a ValueError in ip_network() and a
    500 error on every request that hits a rate-limited view.

    Security note: Only the *first* IP in X-Forwarded-For is used (the
    client-supplied value). This is safe only when Nginx is the sole entry
    point and is configured to set/overwrite X-Forwarded-For with
    $proxy_add_x_forwarded_for (which prepends the real client IP).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        remote_addr = request.META.get("REMOTE_ADDR", "")
        # Only intervene when REMOTE_ADDR is missing or not a valid IP.
        try:
            if remote_addr:
                ipaddress.ip_address(remote_addr)
        except ValueError:
            remote_addr = ""

        if not remote_addr:
            forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
            if forwarded_for:
                # X-Forwarded-For may be a comma-separated list; take the first.
                candidate = forwarded_for.split(",")[0].strip()
                try:
                    ipaddress.ip_address(candidate)
                    request.META["REMOTE_ADDR"] = candidate
                except ValueError:
                    pass  # Leave REMOTE_ADDR as-is; ratelimit will raise its own error.

        return self.get_response(request)
