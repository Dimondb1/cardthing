from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _sync(sender, using="default", **kwargs):
    from .service import sync_defaults

    sync_defaults(using=using)


class ContentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "content"
    verbose_name = "Site wording"

    def ready(self):
        post_migrate.connect(_sync, sender=self, dispatch_uid="content.sync_defaults")
