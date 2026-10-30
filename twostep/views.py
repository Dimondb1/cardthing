"""
The two-step login set-up page (/admin/two-step/): the key to add to an authenticator app, the first code
to confirm it, and the backup codes, shown once. From then on Admin's sign-in asks for the app's code too.
"""

from django.conf import settings
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache

from . import totp
from .forms import ConfirmForm
from .models import TwoStep

# The session holds the id of the user whose sign-in passed two-step, so a session from before it was set up
# (or from a sign-in that skipped the code) never counts.
SESSION_KEY = "two_step_passed"


def mark_passed(sender, request, user, **kwargs):
    if request is not None and getattr(request, "two_step_passed", None) == user.pk:
        request.session[SESSION_KEY] = user.pk


def passed(request):
    return request.session.get(SESSION_KEY) == request.user.pk


def next_url(request):
    """Where to go once set up: the page that sent the owner here, if it is on this site, else Admin's home."""
    wanted = request.POST.get("next") or request.GET.get("next") or ""
    if wanted and url_has_allowed_host_and_scheme(wanted, allowed_hosts={request.get_host()},
                                                  require_https=request.is_secure()):
        return wanted
    return "/admin/"


@never_cache
@staff_member_required
def setup(request):
    two_step, _ = TwoStep.objects.get_or_create(user=request.user)
    # Once two-step is on, only a sign-in that passed it may change it.
    if two_step.is_on and not passed(request):
        return redirect("admin:login")
    context = {**admin.site.each_context(request), "title": "Two-step login", "two_step": two_step,
               "next": next_url(request), "site_name": settings.RIPRAPTOR_SITE_NAME}
    action = request.POST.get("action") if request.method == "POST" else ""
    if action == "codes" and two_step.is_on:
        return render(request, "twostep/setup.html", {**context, "codes": two_step.new_backup_codes(),
                                                      "state": "codes"})
    if action == "cancel" and two_step.is_on:
        two_step.pending_secret = ""
        two_step.save(update_fields=["pending_secret"])
        return redirect("twostep_setup")
    if two_step.is_on and action != "new" and action != "confirm":
        return render(request, "twostep/setup.html", {**context, "state": "on"})
    form = ConfirmForm(request.POST if action == "confirm" else None)
    if form.is_valid():
        codes = two_step.confirm(form.cleaned_data["code"])
        if codes is not None:
            request.session[SESSION_KEY] = request.user.pk
            return render(request, "twostep/setup.html", {**context, "codes": codes, "state": "done"})
        form.add_error("code", "That code is not the one the app shows now. Check the key was typed in full, "
                               "then try the newest code.")
    secret = two_step.start()
    return render(request, "twostep/setup.html", {
        **context, "form": form, "state": "new", "key": totp.grouped(secret),
        "link": totp.app_link(secret, request.user.get_username(), settings.RIPRAPTOR_SITE_NAME),
    })
