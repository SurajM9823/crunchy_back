from django.apps import AppConfig


class CustomerWebConfig(AppConfig):
    name = 'apps.customer_web'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        from . import contact_signals  # noqa: F401
