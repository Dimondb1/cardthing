"""
Staff sign-ins count only once they have passed two-step login.

A staff session that has not (one from before two-step was switched on, or one whose user has not set an app up
yet) is sent to the set-up page from Admin and the dashboard, and is a visitor on public pages, so no staff
power works without the code. A user who has set the app up since that session started signs in again, with it.
"""

from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.urls import reverse

from .models import TwoStep
from .views import passed

OWNER_PATHS = ("/admin/", "/dashboard/")


class TwoStepMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # No session cookie means nobody is signed in: public pages never look at the session.
        if (not settings.RIPRAPTOR_TWO_STEP_REQUIRED or settings.SESSION_COOKIE_NAME not in request.COOKIES
                or not request.user.is_authenticated or not request.user.is_staff or passed(request)):
            return self.get_response(request)
        path = request.path
        if path in (reverse("admin:logout"), reverse("admin:jsi18n"), reverse("admin_manifest")):
            return self.get_response(request)
        login = reverse("admin:login")
        if TwoStep.objects.filter(user=request.user).exclude(secret="").exists():
            logout(request)
            if path.startswith(OWNER_PATHS) and path != login:
                return redirect_to_login(request.get_full_path(), login)
            return self.get_response(request)
        setup = reverse("twostep_setup")
        if path == setup:
            return self.get_response(request)
        if path.startswith(OWNER_PATHS):
            return redirect(f"{setup}?next={quote(request.get_full_path())}")
        request.user = AnonymousUser()
        return self.get_response(request)
