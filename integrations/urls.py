"""Integrations URL Configuration"""
from django.urls import path
from . import views

app_name = "integrations"

urlpatterns = [
    # Connections Hub (unified view of all platforms)
    path("connections/", views.connections_hub, name="connections_hub"),

    # Connection management (per-entity, legacy)
    path(
        "entities/<uuid:entity_pk>/connections/",
        views.connection_manage,
        name="connection_manage",
    ),
    path(
        "entities/<uuid:entity_pk>/connect/<str:provider_name>/",
        views.oauth_connect,
        name="oauth_connect",
    ),
    path("oauth/callback/", views.oauth_callback, name="oauth_callback"),
    path(
        "entities/<uuid:entity_pk>/select-tenant/",
        views.select_tenant,
        name="select_tenant",
    ),
    path(
        "connections/<uuid:connection_pk>/disconnect/",
        views.disconnect,
        name="disconnect",
    ),

    # Cloud import with review/approval
    path(
        "years/<uuid:fy_pk>/import-cloud/",
        views.import_from_cloud,
        name="import_from_cloud",
    ),
    path(
        "years/<uuid:fy_pk>/select-provider/",
        views.select_provider_import,
        name="select_provider_import",
    ),
    path(
        "years/<uuid:fy_pk>/select-xero-tenant/",
        views.xero_select_tenant_import,
        name="xero_select_tenant_import",
    ),
    path(
        "xero/<uuid:fy_pk>/upload-gl-summary/",
        views.xero_gl_summary_upload,
        name="xero_gl_summary_upload",
    ),
    path(
        "years/<uuid:fy_pk>/select-qb-company/",
        views.qb_select_tenant_import,
        name="qb_select_tenant_import",
    ),
    path(
        "years/<uuid:fy_pk>/review-import/",
        views.review_import,
        name="review_import",
    ),
    path(
        "years/<uuid:fy_pk>/commit-import/",
        views.commit_import,
        name="commit_import",
    ),
    path(
        "api/quick-add-entity-account/",
        views.quick_add_entity_account,
        name="quick_add_entity_account",
    ),

    # Global Xero Connection (Advisor-level)
    path("xero/", views.xero_global_dashboard, name="xero_global_dashboard"),
    path("xero/connect/", views.xero_global_connect, name="xero_global_connect"),
    path("xero/callback/", views.xero_global_callback, name="xero_global_callback"),
    path("xero/refresh-tenants/", views.xero_global_refresh_tenants, name="xero_global_refresh_tenants"),
    path("xero/stop-rapid/", views.xero_stop_rapid, name="xero_stop_rapid"),
    path("xero/disconnect/", views.xero_global_disconnect, name="xero_global_disconnect"),

    # Global QuickBooks Connection (Advisor-level)
    path("qb/", views.qb_global_dashboard, name="qb_global_dashboard"),
    path("qb/connect/", views.qb_global_connect, name="qb_global_connect"),
    path("qb/callback/", views.qb_global_callback, name="qb_global_callback"),
    path("qb-global/callback/", views.qb_global_callback, name="qb_global_callback_legacy"),
    path("qb/stop-rapid/", views.qb_stop_rapid, name="qb_stop_rapid"),
    path("qb/disconnect/", views.qb_global_disconnect, name="qb_global_disconnect"),

    # Xero Practice Manager (XPM) Integration
    path("xpm/", views.xpm_dashboard, name="xpm_dashboard"),
    path("xpm/connect/", views.xpm_connect, name="xpm_connect"),
    path("xpm/callback/", views.xpm_callback, name="xpm_callback"),
    path("xpm/select-tenant/", views.xpm_select_tenant, name="xpm_select_tenant"),
    path("xpm/disconnect/", views.xpm_disconnect, name="xpm_disconnect"),
    path("xpm/sync/", views.xpm_sync_now, name="xpm_sync_now"),
]
