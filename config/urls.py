"""MCS Platform URL Configuration"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings

from config.media_serving import serve_protected_media

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),

    # Office Admin dashboard (must be before core catch-all)
    path("office-admin/", include("core.urls_office_admin")),

    # Review app handles the dashboard (homepage) and review pages
    path("", include("review.urls")),
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
