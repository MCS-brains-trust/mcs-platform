"""MCS Platform URL Configuration"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.views.generic.base import RedirectView

from config.media_serving import serve_protected_media

urlpatterns = [
    # Route the Django admin login through the custom, rate-limited and
    # TOTP-gated login flow (security finding B4). Django's built-in
    # /admin/login/ is neither rate-limited nor 2FA-gated, so a staff/superuser
    # password alone could yield an admin session with no TOTP. This redirect
    # (which MUST precede the admin include) sends admin logins to the custom
    # flow; query_string=True preserves ?next=/admin/. Require2FAMiddleware
    # additionally enforces the per-session "2fa_verified" flag on /admin/ URLs.
    path(
        "admin/login/",
        RedirectView.as_view(url="/accounts/login/", query_string=True),
        name="admin_login_redirect",
    ),
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),

    # Office Admin dashboard (must be before core catch-all)
    path("office-admin/", include("core.urls_office_admin")),

    # Review app handles the dashboard (homepage) and review pages
    path("", include("review.urls")),
    # Coworker desktop agent API (token-authenticated, read-only)
    path("api/coworker/", include("core.urls_coworker_api")),

    # Core app handles clients, entities, financial years, etc.
    path("", include("core.urls")),
    # Integrations app handles Xero/MYOB/QB connections and cloud imports
    path("integrations/", include("integrations.urls")),
    # Serve media files with authentication
    path("media/<path:path>", serve_protected_media, name="protected_media"),
]

# Customise admin site
admin.site.site_header = "MCS Financial Statements"
admin.site.site_title = "MCS Admin"
admin.site.index_title = "Administration"
