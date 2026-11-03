"""
Finding a shop's contact address on its own website, for Shop outreach (inbox/outreach.py).

The owner, 3 Nov 2026: "Can you add the email addresses to the shops". Until now only an address the owner typed
was kept. This reads the home page and a contact page or two of each shop that has no address yet, the way a
visitor would, and keeps a business address it finds there, with the page it was found on, so the owner can check
it before sending anything. The wording of what is kept is the ShopContact field's own: "the shop's business
address, as its website gives it (info@, hello@). Kept only to write to them."

What it keeps, and what it never does:

- Only an address on the shop's own domain whose first part is a business role (info, hello, contact, sales,
  support, orders and the like). A free-mail address (Gmail, Outlook), an address that looks like a person's
  (sam@shop.co.uk) and any other site's address (a web agency's, the platform's) are not kept; the row says
  which of the first two it saw, so the owner can decide and type it in.
- Never an address the owner typed: only shops with none are looked at, and an address typed over a found one
  clears where it was found.
- Never a shop marked Do not email, switched off, paused or waiting after errors, and nothing at all while
  Pause all is on. Pages are read through the judge's own reader (catalogue/shop_pages.py Reader): the shop's
  robots.txt is obeyed, only the shop's own site is followed (never another site, never a private network),
  one request at a time at the shop's own pace, one try with a short deadline, and never while the shop is
  being read or the stock watcher is asking it. A shop's home page and at most three more pages are read.
- A shop that was looked at is left alone for RETRY days, found or not, so a shop with no address on its pages is
  not asked every time. One run at a time (a flag in the shared cache), and none while another is going.
- Nothing is sent. The address only fills the box on the Shop outreach pages.
"""

import html as html_
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urljoin, urlsplit

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from catalogue import crawl
from catalogue.shop_pages import NotNow, OutOfTime, Reader, host, shop_hosts

from . import outreach
from .models import ShopContact

logger = logging.getLogger(__name__)

RETRY = timedelta(days=30)
# A shop's home page and up to three more: the contact pages its home page links to, or the usual addresses.
PAGES_PER_SHOP = 4
CONTACT_PATHS = ("/pages/contact", "/pages/contact-us", "/contact", "/contact-us")
# One run reads at most this many pages in this many seconds (about 95 shops at 4 pages, 2 seconds apart).
RUN_PAGES = 700
RUN_SECONDS = 2400
# The flag in the shared cache while a run is going, and for a minute after the button is pressed, until it starts.
RUNNING_KEY = "shop-emails-running"
STARTING_KEY = "shop-emails-starting"
RUNNING_FOR = 45 * 60
STARTING_FOR = 90

# The first part of an address that is a business's own, best first.
ROLES = ("info", "hello", "contact", "enquiries", "enquiry", "sales", "shop", "store", "support", "orders", "order",
         "customerservice", "customercare", "service", "help", "admin", "office", "team", "hi", "hey", "mail",
         "trade", "wholesale", "cards")
# Never wanted, however plain: nobody reads them, or they are for something else.
NOT_THESE = {"noreply", "donotreply", "privacy", "dpo", "gdpr", "abuse", "postmaster", "webmaster", "mailerdaemon",
             "security", "unsubscribe", "careers", "jobs", "legal"}
FREE_MAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "hotmail.co.uk", "live.com", "live.co.uk",
             "yahoo.com", "yahoo.co.uk", "icloud.com", "me.com", "aol.com", "btinternet.com", "sky.com", "msn.com",
             "talktalk.net", "protonmail.com", "proton.me"}
# Text in a page that looks like an address but is not one: a picture's name ("logo@2x.png"), a placeholder.
FILE_ENDINGS = {"png", "jpg", "jpeg", "gif", "webp", "svg", "css", "js", "woff", "woff2", "ico", "avif", "map"}
PLACEHOLDER_DOMAINS = {"example.com", "domain.com", "yourdomain.com", "email.com", "sentry.io", "wixpress.com",
                       "shopify.com", "myshopify.com", "godaddy.com", "squarespace.com", "wordpress.com"}
SECOND_LEVELS = {"co", "org", "ltd", "plc", "me", "gov", "ac", "net", "com", "sch"}

MAILTO = re.compile(r"""mailto:([^"'\s<>?#]+)""", re.I)
CLOUDFLARE = re.compile(r"""data-cfemail=["']([0-9a-fA-F]+)["']""")
JSON_EMAIL = re.compile(r'''"email"\s*:\s*"(?:mailto:)?([^"\s]+)"''', re.I)
PLAIN = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
SCRIPTS = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
TAGS = re.compile(r"<[^>]+>")
ANCHOR = re.compile(r"""<a\b[^>]*?href=["']([^"'#]+)[^>]*>(.*?)</a>""", re.I | re.S)


def registrable(name):
    """The part of a site's name that belongs to its owner: example.co.uk for shop.example.co.uk."""
    labels = [label for label in name.lower().strip(".").split(".") if label]
    if len(labels) >= 3 and labels[-2] in SECOND_LEVELS and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def plain_part(local):
    """"Customer.Service" and "customer-service" both as "customerservice"."""
    return re.sub(r"[._\-+]", "", local.lower())


def decode_cloudflare(code):
    """The address Cloudflare's email protection hid in ``code`` (hex, the first byte the key), or ""."""
    try:
        key = int(code[:2], 16)
        return "".join(chr(int(code[i:i + 2], 16) ^ key) for i in range(2, len(code), 2))
    except ValueError:
        return ""


def clean(address):
    address = html_.unescape(address).strip().strip(".,;:()<>[]'\"").lower()
    try:
        validate_email(address)
    except ValidationError:
        return ""
    domain = address.rpartition("@")[2]
    if domain.rpartition(".")[2] in FILE_ENDINGS or registrable(domain) in PLACEHOLDER_DOMAINS:
        return ""
    return address


def candidates(markup):
    """Every address a page shows, once each, in the order it shows them: mailto links, addresses Cloudflare hid,
    a JSON-LD email, then addresses written out in the page's own words (never those inside scripts)."""
    markup = html_.unescape(markup)
    found = [m.group(1) for m in MAILTO.finditer(markup)]
    found += [decode_cloudflare(m.group(1)) for m in CLOUDFLARE.finditer(markup)]
    found += [m.group(1) for m in JSON_EMAIL.finditer(markup)]
    words = TAGS.sub(" ", SCRIPTS.sub(" ", markup))
    found += PLAIN.findall(words)
    seen, ordered = set(), []
    for address in found:
        address = clean(address)
        if address and address not in seen:
            seen.add(address)
            ordered.append(address)
    return ordered


def judge(address, own):
    """What a shop whose own domain is ``own`` makes of ``address``: ("good", rank) for a business address on its
    own domain, else ("free", 0) for a free-mail address, ("person", 0) for one that looks like a person's, or
    ("other", 0) for another site's or one nobody reads."""
    local, _, domain = address.partition("@")
    part = plain_part(local)
    if part in NOT_THESE:
        return "other", 0
    if registrable(domain) != own:
        return ("free", 0) if domain in FREE_MAIL else ("other", 0)
    if part in ROLES:
        return "good", len(ROLES) - ROLES.index(part)
    return "person", 0


def best(addresses, own):
    """(the best business address or "", the kinds of the others seen) among ``addresses``."""
    good, kinds = [], set()
    for address in addresses:
        kind, rank = judge(address, own)
        if kind == "good":
            good.append((rank, address))
        else:
            kinds.add(kind)
    return (max(good)[1] if good else ""), kinds


def contact_links(markup, base, retailer):
    """The pages of the shop's own site its page links to as a contact page: by the link's address or its words."""
    hosts, seen, links = shop_hosts(retailer), set(), []
    for href, words in ANCHOR.findall(html_.unescape(markup)):
        url = urljoin(base, href.strip())
        parts = urlsplit(url)
        words = TAGS.sub(" ", words).strip().lower()
        if parts.scheme not in ("http", "https") or host(url) not in hosts:
            continue
        if "contact" not in parts.path.lower() and "contact" not in words:
            continue
        key = (parts.netloc, parts.path.rstrip("/"))
        if key not in seen:
            seen.add(key)
            links.append((len(parts.path), url))
    return [url for _length, url in sorted(links)]


@dataclass
class Found:
    address: str = ""
    page: str = ""
    note: str = ""


def find(retailer, reader):
    """Look for the shop's business address on its own pages. Returns Found: the address and the page it was on,
    or a note on why there is none. Raises NotNow when the shop must not be asked now (the shop is being read, the
    stock watcher is asking) and OutOfTime when the run has used its pages or time."""
    own = registrable(host(retailer.website))
    queue, asked, kinds, read, blocked = [retailer.website], set(), set(), 0, ""
    while queue and len(asked) < PAGES_PER_SHOP:
        url = queue.pop(0)
        key = (urlsplit(url).netloc, urlsplit(url).path.rstrip("/"))
        if key in asked:
            continue
        asked.add(key)
        body, facts = reader.fetch_or_fact(url, retailer)
        if body is None:
            if facts.get("status") == "not read":
                blocked = facts.get("why", "")
            continue
        read += 1
        markup = body.decode("utf-8", "replace")
        chosen, seen = best(candidates(markup), own)
        kinds |= seen
        if chosen:
            return Found(address=chosen, page=getattr(body, "url", "") or url)
        if len(asked) == 1:   # the home page: now the pages it links to as contact pages, else the usual addresses
            base = getattr(body, "url", "") or url
            guesses = [urljoin(base, path) for path in CONTACT_PATHS]
            queue = contact_links(markup, base, retailer)[:2] + guesses
    if not read:
        return Found(note=("its robots.txt asks robots not to read its pages" if "robots" in blocked
                           else "its pages could not be read"))
    if "free" in kinds:
        return Found(note="only a free-mail address is on its pages")
    if "person" in kinds:
        return Found(note="only an address that looks like a person's is on its pages")
    return Found(note="no address on its home or contact pages")


@dataclass
class Result:
    found: int = 0
    none: int = 0
    skipped: int = 0
    stopped: bool = False
    already: bool = False
    paused: bool = False


def queue(now=None, only="", force=False):
    """The shops to look at: switched on, with a website, not marked Do not email, no address yet, and (unless
    ``force``) not looked at in the last RETRY days. Shop by shop in alphabetical order."""
    now = now or timezone.now()
    shops = (ShopContact.objects.select_related("retailer").filter(email="", retailer__is_active=True)
             .exclude(retailer__website="").exclude(status=ShopContact.Status.DO_NOT_EMAIL))
    if only:
        shops = shops.filter(retailer__slug=only)
    if not force:
        shops = shops.exclude(email_looked_at__gte=now - RETRY)
    return shops.order_by("retailer__name")


def resting(retailer, now):
    """Why this shop is not asked in this run, or ""."""
    if retailer.reading_paused:
        return "reading this shop is paused"
    if retailer.backoff_until and retailer.backoff_until > now:
        return "the shop is waiting after errors"
    return ""


def run(limit=None, only="", force=False, dry_run=False, reader=None, now=None, report=None, **reader_options):
    """Look for an address for each shop that has none, keeping what is found (not on a dry run). ``report(line)``
    is told what happened to each shop. Returns Result."""
    now = now or timezone.now()
    report = report or (lambda line: None)
    result = Result()
    outreach.ensure_contacts()
    if crawl.all_paused():
        report("Pause all is on, so no shop's pages are read.")
        result.paused = True
        return result
    if not dry_run and not cache.add(RUNNING_KEY, now.isoformat(), RUNNING_FOR):
        report("Another run is looking for addresses now.")
        result.already = True
        return result
    reader = reader or Reader(pages=RUN_PAGES, seconds=RUN_SECONDS, **reader_options)
    tried = 0
    try:
        for contact in queue(now, only, force):
            retailer = contact.retailer
            if limit is not None and tried >= limit:
                break
            why = resting(retailer, now)
            if why:
                result.skipped += 1
                report(f"{retailer.name}: left for now, {why}.")
                continue
            try:
                found = find(retailer, reader)
            except OutOfTime:
                result.stopped = True
                report("This run has used its time, so the rest wait for the next.")
                break
            except NotNow as stopped:
                result.skipped += 1
                report(f"{retailer.name}: left for now, {stopped}.")
                continue
            except Exception:   # one shop must never stop the rest
                logger.warning("Shop addresses: %s failed", retailer.name, exc_info=True)
                result.skipped += 1
                report(f"{retailer.name}: could not be looked at.")
                continue
            tried += 1
            if found.address:
                result.found += 1
                report(f"{retailer.name}: {found.address} (on {found.page})")
            else:
                result.none += 1
                report(f"{retailer.name}: none, {found.note}.")
            if not dry_run:
                save(contact, found, now)
    finally:
        if not dry_run:
            cache.delete(RUNNING_KEY)
    return result


def save(contact, found, now):
    """Keep what the look found. An address typed in the meantime is never replaced."""
    contact.refresh_from_db(fields=["email"])
    contact.email_looked_at = now
    fields = ["email_looked_at", "email_note"]
    if found.address and not contact.email:
        contact.email, contact.email_found_on, contact.email_note = found.address, found.page, ""
        fields += ["email", "email_found_on"]
    elif not found.address:
        contact.email_note = found.note
    contact.save(update_fields=fields)


def running():
    """Whether a run is going, or has just been asked for and is starting."""
    return cache.get(RUNNING_KEY) is not None or cache.get(STARTING_KEY) is not None


def start_now():
    """Start a run at once in its own process, so the page returns straight away; the page then shows how far it
    has got. Never in tests. False when one is already going or it could not start."""
    import subprocess
    import sys
    from pathlib import Path

    from django.conf import settings

    if getattr(settings, "TESTING", False) or running():
        return False
    try:
        subprocess.Popen(
            [sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), "find_shop_emails"], cwd=settings.BASE_DIR,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=None, start_new_session=True,
        )
    except OSError:
        logger.warning("Shop addresses: could not start the run.", exc_info=True)
        return False
    cache.set(STARTING_KEY, "1", STARTING_FOR)
    return True
