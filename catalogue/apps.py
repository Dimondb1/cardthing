from importlib import import_module

from django.apps import AppConfig


class CatalogueConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "catalogue"
    verbose_name = "Products and prices"

    def ready(self):
        import_module(".signals", self.name)
