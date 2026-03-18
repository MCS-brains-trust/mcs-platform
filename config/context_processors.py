from django.conf import settings


def build_info(request):
    """Inject build timestamp and firm branding into all templates.

    The ``firm_settings`` object is injected globally so that every template
    (including auth pages, login, invitation emails, and the base layout) can
    access the firm name, logo URL, and contact details without needing
    per-view wiring.
    """
    ctx = {
        "BUILD_TIMESTAMP": getattr(settings, "BUILD_TIMESTAMP", "unknown"),
    }

    # Inject FirmSettings for white-label branding across all templates
    try:
        from core.models import FirmSettings
        fs = FirmSettings.get()
        ctx["firm_settings"] = fs
        ctx["firm_name"] = fs.firm_name or "StatementHub"
        ctx["firm_logo_url"] = fs.logo_url or ""
    except Exception:
        ctx["firm_settings"] = None
        ctx["firm_name"] = "StatementHub"
        ctx["firm_logo_url"] = ""

    return ctx
