from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        import core.signals  # noqa: F401 — register signal receivers
        # Connect Eva style learning edit capture signals
        try:
            from core.eva_style import connect_edit_capture_signals
            connect_edit_capture_signals()
        except Exception:
            pass  # Graceful degradation if models not yet migrated
