"""
Telling the owner something needs a look, on their phone and by email.

A push goes through ntfy (RIPRAPTOR_NTFY_TOPIC) and an email to the owner's own address through ZeptoMail
(RIPRAPTOR_INBOX_NOTIFY_EMAIL). Either can be left blank; with both blank nothing is sent.

The push carries only a title and a link to admin, never a shop, product, price or visitor's words,
because anyone who guesses an ntfy.sh topic can read it. The email goes to the owner alone, so it carries
the detail.

Crawl problems (the background reader stopped, a shop failing for a day, doubtful prices piling up) are
sent at most once a day each, remembered in WorkerState.notices, and RIPRAPTOR_CRAWL_PUSHES=0 turns them
off. Sending is best effort: nothing here raises because a server is down.
"""

import http.client
import logging
import urllib.request
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.html import escape

from . import mail

logger = logging.getLogger(__name__)

ONCE_EVERY = timedelta(hours=24)
PUSH_SECONDS = 10
PUSH_BODY = b"Open admin to read it."

# The crawl problems, each a push title and email subject. None names a shop or a product.
WORKER_STOPPED = "RipRaptor crawl stopped"
SHOP_FAILING = "A shop keeps failing"
PRICES_TO_CHECK = "Prices to check"
CRAWL_PATH = "/admin/crawl/"
CHECKS_PATH = "/admin/checks/"


def link_for(path):
    """A full address for a link in a push or an email."""
    return path if "://" in path else settings.RIPRAPTOR_SITE_URL + path


def can_push():
    return bool(settings.RIPRAPTOR_NTFY_TOPIC)


def can_email():
    return bool(settings.RIPRAPTOR_INBOX_NOTIFY_EMAIL) and mail.enabled()


def push(title, link, tags="", opener=None):
    """Send the push. Returns whether the server took it; a server that is down is not an error."""
    if not can_push():
        return False
    headers = {"Title": title, "Click": link}
    if tags:
        headers["Tags"] = tags
    request = urllib.request.Request(
        settings.RIPRAPTOR_NTFY_URL.rstrip("/") + "/" + settings.RIPRAPTOR_NTFY_TOPIC,
        data=PUSH_BODY, headers=headers, method="POST",
    )
    try:
        with (opener or urllib.request.urlopen)(request, timeout=PUSH_SECONDS):
            pass
    except (OSError, ValueError, http.client.HTTPException) as exc:
        logger.warning("The push %r was not sent: %s", title, exc)
        return False
    return True


def email(subject, detail, link, link_label="Open admin"):
    """Email the owner. Returns whether it was sent."""
    if not can_email():
        return False
    text = f"{detail}\n\n{link_label}: {link}\n" if detail else f"{link_label}: {link}\n"
    paragraph = f"<p style=\"white-space:pre-wrap\">{escape(detail)}</p>" if detail else ""
    html = f"{paragraph}<p><a href=\"{escape(link)}\">{escape(link_label)}</a></p>"
    try:
        mail.send(settings.RIPRAPTOR_INBOX_NOTIFY_EMAIL, subject, text, html)
    except mail.MailError as exc:
        logger.warning("The email %r was not sent: %s", subject, exc)
        return False
    return True


def claim(subject, now):
    """True, and the time noted, when ``subject`` has not been sent in the last day. One writer at a time."""
    from .models import WorkerState

    with transaction.atomic():
        state = WorkerState.load()
        notices = dict(state.notices or {})
        try:
            last = datetime.fromisoformat(notices.get(subject) or "")
        except (TypeError, ValueError):
            last = None
        if last is not None and now - last < ONCE_EVERY:
            return False
        notices[subject] = now.isoformat()
        WorkerState.objects.filter(pk=state.pk).update(notices=notices)
    return True


def owner(subject, title, click_path, detail="", now=None, once_a_day=True, link_label="Open admin", tags="",
          opener=None):
    """Tell the owner: a push titled ``title`` and an email headed ``subject`` carrying ``detail``.

    With ``once_a_day`` a subject sent in the last 24 hours is not sent again. Returns whether it was sent.
    """
    if not (can_push() or can_email()):
        return False
    if once_a_day and not claim(subject, now or timezone.now()):
        return False
    link = link_for(click_path)
    push(title, link, tags=tags, opener=opener)
    email(subject, detail, link, link_label)
    return True


def crawl_problem(subject, click_path, detail="", now=None, opener=None):
    """A crawl problem, once a day at most, unless RIPRAPTOR_CRAWL_PUSHES is off."""
    if not settings.RIPRAPTOR_CRAWL_PUSHES:
        return False
    return owner(subject, subject, click_path, detail, now=now, tags="warning", opener=opener)


def worker_stopped(heartbeat_at, now=None):
    now = now or timezone.now()
    when = timezone.localtime(heartbeat_at)
    detail = (
        f"The background reader last sent a heartbeat at {when:%H:%M} on {when:%d %b}. The hourly schedule "
        "reads the shops meanwhile, so prices update less often. The server restarts the reader on its own; "
        "if this stays, the server needs a look."
    )
    return crawl_problem(WORKER_STOPPED, CRAWL_PATH, detail, now=now)


def shops_failing(shops, now=None):
    """``shops`` is [(name, last error)]. The names go in the email only."""
    lines = "\n".join(f"{name}: {error or 'no error recorded'}" for name, error in shops)
    detail = f"These shops have failed every read for more than a day:\n{lines}"
    return crawl_problem(SHOP_FAILING, CRAWL_PATH, detail, now=now)


def prices_to_check(count, now=None):
    detail = f"{count} doubtful prices are waiting on Things to check. Each has a button to say the price is right or to hide it."
    return crawl_problem(PRICES_TO_CHECK, CHECKS_PATH, detail, now=now)
