from django.apps import AppConfig


class LoyaltyConfig(AppConfig):
    name = 'apps.loyalty'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        from . import signals  # noqa: F401
