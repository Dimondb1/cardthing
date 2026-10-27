from django.apps import AppConfig


class GuidesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "guides"
    verbose_name = "Guides"

    def ready(self):
        from django.db.models.signals import post_delete, post_migrate, post_save

        from . import state
        from .models import Guide

        # The nav link and the doors in from product pages are read from the cache: kept right on every change.
        post_save.connect(state.on_change, sender=Guide, dispatch_uid="guides-state-save")
        post_delete.connect(state.on_change, sender=Guide, dispatch_uid="guides-state-delete")
        post_migrate.connect(editors_group, sender=self, dispatch_uid="guides-editors-group")


EDITORS = "Guide editors"
# What a marketing login needs: write and import guides, see what people check, and find products for price cards.
EDITOR_PERMISSIONS = [
    ("guides", "guide", ("add", "change", "view")), ("guides", "section", ("add", "change", "delete", "view")),
    ("guides", "pricecheck", ("view",)), ("catalogue", "product", ("view",)),
]


def editors_group(sender, using="default", **kwargs):
    """The Guide editors group, kept with just EDITOR_PERMISSIONS: give a marketing login this group and
    Staff status, and Admin shows it Guides and nothing else to change."""
    from django.contrib.auth.models import Group, Permission

    group, _ = Group.objects.using(using).get_or_create(name=EDITORS)
    wanted = []
    for app, model, actions in EDITOR_PERMISSIONS:
        for action in actions:
            found = Permission.objects.using(using).filter(content_type__app_label=app,
                                                            codename=f"{action}_{model}").first()
            if found is not None:
                wanted.append(found)
    group.permissions.set(wanted)
