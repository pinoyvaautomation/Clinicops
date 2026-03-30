from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self) -> None:
        from . import checks  # noqa: F401
        from . import security  # noqa: F401
        from . import signals  # noqa: F401
