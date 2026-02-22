from django.conf import settings


def build_info(request):
    """Inject build timestamp into all templates."""
    return {
        "BUILD_TIMESTAMP": getattr(settings, "BUILD_TIMESTAMP", "unknown"),
    }
