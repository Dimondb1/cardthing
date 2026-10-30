from django.apps import AppConfig


class TwoStepConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "twostep"
    verbose_name = "Two-step login"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in

        from . import views

        # A sign-in that gave the right code is marked on its session, so the owner pages let it in.
        user_logged_in.connect(views.mark_passed, dispatch_uid="twostep-mark-passed")
