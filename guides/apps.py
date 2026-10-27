from django.apps import AppConfig


class GuidesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "guides"
    verbose_name = "Guides"

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from . import state
        from .models import Guide

        # The nav link and the doors in from product pages are read from the cache: kept right on every change.
        post_save.connect(state.on_change, sender=Guide, dispatch_uid="guides-state-save")
        post_delete.connect(state.on_change, sender=Guide, dispatch_uid="guides-state-delete")
