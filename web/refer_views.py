"""Refer a friend pages (catalogue/referrals.py has the rules). Every page and form works without JavaScript.

/refer/ and /r/<code>/ are public but not indexed; the confirm, claim, address and delete pages are private
links (no third-party script, kept out of search engines, see web/context_processors.py and robots.txt).
"""

from urllib.parse import quote

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from catalogue import mail, referrals
from catalogue.models import KeyringClaim, Referral, ReferralSettings
from content.service import for_request, get

ERRORS = {"invalid": "refer.form.invalid", "agree": "refer.form.agree_needed", "busy": "refer.form.busy",
          "failed": "refer.form.failed"}


def text(request, key, **values):
    return get(key, store=for_request(request), **values)


def private(response):
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def message(request, title, body, status=200, **extra):
    """A short page: a heading and one line."""
    heading = text(request, title)
    return private(render(request, "web/refer_message.html", {
        "heading": heading, "message": text(request, body), "meta_title": heading, "noindex": True, **extra,
    }, status=status))


def ended(request):
    return message(request, "refer.title", "refer.ended")


def gone(request, network):
    """A link that does not exist or no longer works: counted, so guessing codes is slow."""
    referrals.missed(network)
    return message(request, "refer.gone.title", "refer.gone.body", status=404)


def busy(request):
    return message(request, "refer.gone.title", "refer.busy", status=429)


def take_email(request):
    """The form's address. Returns (email, error key)."""
    email = request.POST.get("email", "").strip()[:254]
    try:
        validate_email(email)
    except ValidationError:
        return "", "invalid"
    if request.POST.get("agree") != "1":
        return email, "agree"
    return email, ""


@require_http_methods(["GET", "POST"])
def refer(request):
    """Ask for a link to share. POST answers the same for every address, then the page says check your inbox."""
    email, error = "", ""
    if request.method == "POST":
        # The cheap checks first, from the cache, so a flood is turned away before the database is read.
        if request.POST.get("website"):
            return HttpResponseRedirect(reverse("web:refer"))   # the hidden field is only ever filled by robots
        connection, network = referrals.fingerprints(request)
        if not referrals.network_allowed(network):
            error = "busy"
    row = ReferralSettings.current()
    referrals.refresh_flag(row)
    if not referrals.taking_part(row):
        return ended(request)
    if request.method == "POST" and not error:
        email, error = take_email(request)
        if not error:
            try:
                referrals.ask(email, connection, network)
            except mail.MailError:
                error = "failed"
            else:
                return HttpResponseRedirect(reverse("web:refer") + "?sent=1")
    elif request.method == "GET" and request.GET.get("sent") == "1":
        return message(request, "refer.sent.title", "refer.sent.body")
    title = text(request, "refer.title")
    return private(render(request, "web/refer.html", {
        "email": email, "error": ERRORS.get(error, ""), "meta_title": title, "noindex": True,
        "action": reverse("web:refer"),
    }, status=429 if error == "busy" else 200))


@require_http_methods(["GET"])
def refer_confirm(request, token):
    """The referrer's confirm link, and their page from then on: their link with ways to share it."""
    _connection, network = referrals.fingerprints(request)
    if referrals.too_many_misses(network):
        return busy(request)
    if not referrals.TOKEN_SHAPE.match(token):
        return gone(request, network)
    referrer, state = referrals.confirm(token)
    if state == "gone":
        return gone(request, network)
    if state == "ended":
        return ended(request)
    if state == "used":
        return message(request, "refer.link.title", "refer.link.used")
    url = referrals.share_url(referrer)
    words = text(request, "refer.share.message", link=url)
    heading = text(request, "refer.link.title")
    return private(render(request, "web/refer_link.html", {
        "share_url": url, "meta_title": heading,
        "whatsapp_url": "https://wa.me/?text=" + quote(words),
        "sms_url": "sms:?&body=" + quote(words),
        "email_url": f"mailto:?subject={quote(text(request, 'refer.share.subject'))}&body={quote(words)}",
        "share_title": text(request, "refer.share.subject"), "share_words": words, "noindex": True,
    }))


@require_http_methods(["GET", "POST"])
def refer_friend(request, code):
    """A referrer's link: what the offer is, and a form for the friend's own email address."""
    connection, network = referrals.fingerprints(request)
    if referrals.too_many_misses(network):
        return busy(request)
    if request.method == "POST":
        # The cheap checks first, from the cache, so a flood is turned away before the database is read.
        if request.POST.get("website"):
            return HttpResponseRedirect(request.path)
        if not referrals.network_allowed(network):
            return private(render(request, "web/refer_friend.html", {
                "email": "", "error": ERRORS["busy"], "meta_title": text(request, "refer.friend.title"),
                "noindex": True, "action": request.path,
            }, status=429))
    row = ReferralSettings.current()
    referrer = referrals.referrer_for_code(code)
    if referrer is None:
        return gone(request, network)
    if not referrals.taking_part(row):
        return ended(request)
    if referrals.at_limit(referrer, row):
        return message(request, "refer.friend.title", "refer.friend.used", own_link=True)
    here = reverse("web:refer_friend", args=[referrer.code])
    email, error = "", ""
    if request.method == "POST":
        email, error = take_email(request)
        if not error:
            try:
                referrals.ask_friend(referrer, email, connection, network)
            except mail.MailError:
                error = "failed"
            else:
                return HttpResponseRedirect(here + "?sent=1")
    elif request.GET.get("sent") == "1":
        return message(request, "refer.sent.title", "refer.sent.body")
    title = text(request, "refer.friend.title")
    return private(render(request, "web/refer_friend.html", {
        "email": email, "error": ERRORS.get(error, ""), "meta_title": title, "noindex": True, "action": here,
    }, status=429 if error == "busy" else 200))


@require_http_methods(["GET", "POST"])
def refer_claim(request, token):
    """The friend's confirm link: a button, so a mail scanner opening the link cannot complete the referral."""
    _connection, network = referrals.fingerprints(request)
    if referrals.too_many_misses(network):
        return busy(request)
    referral = None
    if referrals.TOKEN_SHAPE.match(token):
        referral = Referral.objects.select_related("referrer").filter(token=token).first()
    if referral is None or referral.status == Referral.Status.CANCELLED:
        return gone(request, network)
    if referral.status != Referral.Status.WAITING:
        return message(request, "refer.claimed.title", "refer.claimed.body")
    if not referrals.taking_part():
        return ended(request)
    if request.method == "POST":
        state = referrals.complete(referral)
        if state == "done":
            return message(request, "refer.claimed.title", "refer.claimed.body")   # true held for review or not
        if state == "not_new":
            return message(request, "refer.claim.title", "refer.claim.not_new")
        if state == "used":
            return message(request, "refer.claim.title", "refer.friend.used")
        if state == "ended":
            return ended(request)
        return gone(request, network)
    heading = text(request, "refer.claim.title")
    return private(render(request, "web/refer_claim.html", {"meta_title": heading, "noindex": True}))


@require_http_methods(["GET", "POST"])
def refer_address(request, token):
    """The private address form. Once sent it cannot be changed from the link, and never shows the address."""
    _connection, network = referrals.fingerprints(request)
    if referrals.too_many_misses(network):
        return busy(request)
    claim = None
    if referrals.TOKEN_SHAPE.match(token):
        claim = KeyringClaim.objects.select_related("referral").filter(token=token).first()
    state = referrals.claim_state(claim)
    if state == "gone":
        return gone(request, network)
    if state == "ended":
        return ended(request)
    if state == "done":
        return message(request, "refer.address.done.title", "refer.address.done.body")
    if state == "expired":
        return message(request, "refer.gone.title", "refer.address.expired")
    values, error = {name: "" for name in referrals.ADDRESS_FIELDS}, ""
    if request.method == "POST":
        if request.POST.get("website"):
            return HttpResponseRedirect(request.path)
        values = {name: request.POST.get(name, "")[:limit] for name, limit in referrals.ADDRESS_FIELDS.items()}
        error = referrals.save_address(claim, values)
        if not error:
            return message(request, "refer.address.done.title", "refer.address.done.body")
    heading = text(request, "refer.address.title")
    return private(render(request, "web/refer_address.html", {
        "values": values, "error": error, "meta_title": heading, "noindex": True,
    }, status=400 if error else 200))


@require_http_methods(["GET", "POST"])
def refer_stop(request, token):
    """Delete your details. A button, not the link itself: mail scanners open links in emails."""
    _connection, network = referrals.fingerprints(request)
    if referrals.too_many_misses(network):
        return busy(request)
    if request.method == "POST" and not referrals.network_allowed(network):
        return busy(request)
    found = referrals.find_token(token)
    if request.method == "POST":
        if found is not None:
            referrals.forget(found)
        else:
            referrals.missed(network)   # counted like a wrong link, so guessing by POST is slow too
        return message(request, "refer.stopped.title", "refer.stopped.body")
    if found is None:
        # Nothing is held for this link, which is what a deleted one looks like too.
        referrals.missed(network)
        return message(request, "refer.stopped.title", "refer.stopped.body")
    heading = text(request, "refer.stop.title")
    return private(render(request, "web/refer_stop.html", {"meta_title": heading, "noindex": True}))
