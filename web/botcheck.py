"""
The visitor check: a first visit from abroad does a second of work in the browser before a page shows. The owner,
9 Nov 2026: "I thought we could prevent these bots ourselves".

The crawler of 5 to 7 November 2026 rented ordinary home connections in about fifty countries and opened one page from
each address, calling itself Chrome: a limit per address never trips, its name gives nothing away and robots.txt is
ignored. What it cannot avoid is that every new address must show a page before it can read one. So, as the free tool
Anubis does against AI scrapers:

* A visitor from outside the countries the owner names (the UK and Ireland, catalogue.VisitorCheck) with no pass
  for its address gets a small page that works out a proof of work in the browser (a SHA-256 hash with a run of
  leading zeros, about a second on a phone) and sends it back. A right answer earns a pass: a signed cookie for that
  address, good for a week. A person pays once; a crawler that changes address every page pays every page, and one
  that does not run scripts never gets a page.
* Search engines and AI assistants are never checked: Google and Bing proven by their addresses' own names (reverse
  then forward DNS, remembered a day), the others by name, with the link previews of chat and social apps.
* The site's own data feeds (/api/) answer a visitor from abroad with no pass with 403, never a check page.
* Staff, the admin, the files the pages load, robots.txt, the sitemaps and the feeds are never checked, nor a
  visitor whose country cannot be told (no country database), so the check fails open.
* The flood's own browsers ran our pages (catalogue/automated.py), so they can run the sum too. A browser driven by a
  script says so in ways a person's never does (navigator.webdriver, HeadlessChrome and the like): the page does not
  work the sum for one, and an answer from one earns no pass.
* The owner picks what a first visit from abroad does (VisitorCheck.mode). Tap (the default): the sum, then a tap on
  Continue, which a crawler that opens pages by itself never makes; within minutes of going live on 9 Nov 2026 the
  flood passed the sum alone 80 times in 91. Work: the sum, then straight on. Away: a short page saying RipRaptor is
  for UK shoppers, with no way on and no pass honoured.

The switch and the countries are read from the cache (no query on a page); saving the row in Admin puts the new
settings there at once. How many were shown the check today and how many passed are kept in the cache too, for Who
is online (web/online.py).
"""

import hashlib
import ipaddress
import re
import secrets
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as LookupTimeout
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from catalogue import geo
from catalogue.insights import client_ip

COOKIE = "rr_ok"
PASS_SALT = "web.botcheck.pass"
TASK_SALT = "web.botcheck.task"
PASS_DAYS = 7
TASK_SECONDS = 600          # a task must be answered within ten minutes of being set
ZEROS = 4                   # leading zero hex digits the answer's hash needs: 65,536 tries on average
SETTINGS_KEY = "visitor-check:v2"
VERIFIED_KEY = "visitor-check:crawler:"
VERIFIED_SECONDS = 24 * 3600
LOOKUP_SECONDS = 2          # a name lookup that takes longer counts as not proven, so a page never waits on DNS
UNANSWERED_SECONDS = 600    # and is tried again ten minutes later
PATH = "/check/"            # where an answer is sent (ripraptor/urls.py)
TAP, WORK, AWAY = "tap", "work", "away"     # VisitorCheck.Mode
COUNT_KEY = "visitor-check:count:"
COUNT_SECONDS = 2 * 24 * 3600
# A browser that names itself as driven by a script. Never a person's: an answer from one earns no pass.
DRIVEN = re.compile(r"headlesschrome|phantomjs|puppeteer|playwright|selenium|webdriver", re.I)

# Never checked: the files a page loads, the files crawlers and feed readers read, the admin (its own sign-in) and the
# check's own address.
OPEN_PREFIXES = ("/static/", "/media/", "/admin/", "/dashboard/", "/feeds/", "/.well-known/", PATH)
OPEN_PATHS = ("/robots.txt", "/sitemap.xml", "/llms.txt", "/favicon.ico", "/manifest.webmanifest")

# Search engines whose addresses prove who they are: a reverse lookup ends in one of their own names, and that name
# looks up back to the same address. Google's crawlers come from googlebot.com and google.com; a plain Google Cloud
# address (googleusercontent.com) can be rented by anyone, so it proves only Google's site verification and page
# speed tests, which run there.
VERIFIED_CRAWLERS = {
    "googlebot": (".googlebot.com", ".google.com"),
    "google-inspectiontool": (".googlebot.com", ".google.com"),
    "googleother": (".googlebot.com", ".google.com"),
    "storebot-google": (".googlebot.com", ".google.com"),
    "adsbot-google": (".googlebot.com", ".google.com"),
    "google-site-verification": (".googlebot.com", ".google.com", ".googleusercontent.com"),
    "chrome-lighthouse": (".google.com", ".googleusercontent.com"),
    "bingbot": (".search.msn.com",),
    "msnbot": (".search.msn.com",),
    "bingpreview": (".search.msn.com",),
    "applebot": (".applebot.apple.com",),
}
# Let through by name: AI assistants and their search, smaller search engines and the link previews that chat and
# social apps make when someone shares a page. Naming one gets a crawler past the check only, never into anything else.
NAMED_CRAWLERS = (
    "gptbot", "oai-searchbot", "chatgpt-user", "claudebot", "claude-user", "claude-searchbot", "anthropic-ai",
    "perplexitybot", "perplexity-user", "google-extended", "applebot-extended", "ccbot", "amazonbot", "duckduckbot",
    "duckassistbot", "yandexbot", "petalbot", "mojeekbot", "facebookexternalhit", "facebot", "meta-externalagent",
    "twitterbot", "discordbot", "whatsapp", "slackbot", "linkedinbot", "pinterestbot", "telegrambot", "redditbot",
    "skypeuripreview", "embedly",
)


def current():
    """{"on", "countries", "mode"}: the owner's switch, the countries never checked and what a first visit from
    abroad does, from the cache (remember)."""
    found = cache.get(SETTINGS_KEY)
    return found if found is not None else remember()


def remember():
    """Read the owner's row (one query) and keep it in the cache. No row yet: on, tap, for the UK and Ireland."""
    from catalogue.models import VisitorCheck

    row = VisitorCheck.objects.filter(pk=1).first()
    found = ({"on": row.on, "countries": sorted(row.country_codes()), "mode": row.mode} if row is not None
             else {"on": True, "countries": ["GB", "IE"], "mode": VisitorCheck.Mode.TAP.value})
    cache.set(SETTINGS_KEY, found, None)
    return found


def address_key(ip):
    """The part of an address a pass is bound to: the whole of an IPv4 address, the network (/64) of an IPv6 one,
    which a phone keeps while the end of its address changes."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return ip or ""
    if address.version == 6:
        return str(ipaddress.ip_network(f"{address}/64", strict=False).network_address)
    return str(address)


def has_pass(request, ip):
    raw = request.COOKIES.get(COOKIE, "")
    if not raw:
        return False
    try:
        found = signing.loads(raw, salt=PASS_SALT, max_age=PASS_DAYS * 24 * 3600)
    except signing.BadSignature:
        return False
    return found == address_key(ip)


def crawler_name(agent):
    agent = agent.lower()
    return next((name for name in VERIFIED_CRAWLERS if name in agent), "")


_lookups = None


def lookups():
    """A few threads for name lookups, so one that hangs is given up on after LOOKUP_SECONDS."""
    global _lookups
    if _lookups is None:
        _lookups = ThreadPoolExecutor(max_workers=4, thread_name_prefix="visitor-check")
    return _lookups


def addresses_of(host):
    """Every address a name looks up to, IPv4 and IPv6 (Googlebot crawls over both)."""
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def same_address(one, other):
    try:
        return ipaddress.ip_address(one.split("%")[0]) == ipaddress.ip_address(other.split("%")[0])
    except ValueError:
        return False


def resolves(ip, suffixes, lookup, forward):
    try:
        host = lookup(ip)[0].lower().rstrip(".")
        return host.endswith(tuple(suffixes)) and any(same_address(ip, found) for found in forward(host))
    except (OSError, UnicodeError, IndexError):
        return False


def verified(ip, suffixes, lookup=None, forward=None):
    """Whether ``ip``'s own name ends in one of ``suffixes`` and that name leads back to ``ip``. Remembered a day;
    a lookup with no answer inside LOOKUP_SECONDS counts as no and is asked again ten minutes later."""
    key = VERIFIED_KEY + hashlib.sha256(f"{ip}|{'|'.join(suffixes)}".encode()).hexdigest()[:24]
    found = cache.get(key)
    if found is not None:
        return found
    job = lookups().submit(resolves, ip, suffixes, lookup or socket.gethostbyaddr, forward or addresses_of)
    try:
        result = job.result(timeout=LOOKUP_SECONDS)
    except LookupTimeout:
        cache.set(key, False, UNANSWERED_SECONDS)
        return False
    cache.set(key, result, VERIFIED_SECONDS)
    return result


def let_through(request, ip):
    """Whether this request skips the check whatever its country: a search engine proven by its address, an AI
    assistant or link preview by its name, or staff."""
    agent = request.headers.get("User-Agent", "")
    name = crawler_name(agent)
    if name:
        return verified(ip, VERIFIED_CRAWLERS[name])
    lowered = agent.lower()
    if any(crawler in lowered for crawler in NAMED_CRAWLERS):
        return True
    user = getattr(request, "user", None)
    return bool(user is not None and user.is_authenticated and user.is_staff)


def open_path(path):
    # A .txt file at the top (robots.txt, llms.txt, the IndexNow key the search engines read) is never checked.
    return (path.startswith(OPEN_PREFIXES) or path in OPEN_PATHS
            or (path.endswith(".txt") and path.count("/") == 1))


def needs_check(request):
    """Whether this request must show the check (or, for a data feed, be refused) before anything else."""
    if request.method not in ("GET", "HEAD") or open_path(request.path):
        return False
    # The country first: a visitor whose country cannot be told is never checked, so needs no settings read.
    ip = client_ip(request)
    country = geo.country_of(ip)
    if not country:
        return False
    rules = current()
    if not rules["on"] or country in rules["countries"]:
        return False
    # Turned away: a pass earned before counts for nothing.
    if rules["mode"] != AWAY and has_pass(request, ip):
        return False
    return not let_through(request, ip)


def task_for(ip):
    """A fresh task: a signed note of the address and a random part, which the answer must hash with."""
    return signing.dumps({"a": address_key(ip), "r": secrets.token_hex(8), "t": int(time.time())}, salt=TASK_SALT)


def solves(task, answer, zeros=ZEROS):
    return hashlib.sha256(f"{task}{answer}".encode()).hexdigest().startswith("0" * zeros)


def safe_next(target, host=None):
    """Only a path on this site: never another site's address, however it is dressed up."""
    target = target or "/"
    parts = urlsplit(target)
    if (parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//") or "\\" in target
            or any(ord(c) < 32 or ord(c) == 127 for c in target)
            or not url_has_allowed_host_and_scheme(target, allowed_hosts={host} if host else set())):
        return "/"
    return target


def tally(what, day=None):
    """Count one check ``shown`` or ``passed`` today. Best effort: two workers counting at once may lose one."""
    key = f"{COUNT_KEY}{(day or timezone.localdate()).isoformat()}:{what}"
    try:
        if not cache.add(key, 1, COUNT_SECONDS):
            cache.incr(key)
    except ValueError:
        cache.set(key, 1, COUNT_SECONDS)


def today_counts(day=None):
    """{"shown", "passed"}: how many first visits from abroad were shown the check today and how many passed."""
    day = (day or timezone.localdate()).isoformat()
    return {what: cache.get(f"{COUNT_KEY}{day}:{what}") or 0 for what in ("shown", "passed")}


def challenge(request):
    """The check page: the task, the work to do and where to send the answer; or, turned away, the page saying the
    site is for UK shoppers (403). Never kept or indexed."""
    mode = current()["mode"]
    away = mode == AWAY
    response = render(request, "web/botcheck.html", {
        "away": away, "tap": mode == TAP, "task": "" if away else task_for(client_ip(request)), "zeros": ZEROS,
        "next": request.get_full_path(), "check_url": PATH, "meta_title": "RipRaptor",
    }, status=403 if away else 200)
    response["Cache-Control"] = "no-store, private"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


class VisitorCheckMiddleware:
    """Show the check (web/botcheck.py) to a request that needs it; refuse a data feed with 403 instead."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if needs_check(request):
            if request.path.startswith("/api/"):
                response = JsonResponse({"error": "check"}, status=403)
                response["Cache-Control"] = "no-store"
                return response
            if request.method == "GET":
                tally("shown")
            return challenge(request)
        return self.get_response(request)


@never_cache
@require_GET
def answer(request):
    """An answer to the check: a right one for this address, inside ten minutes, earns the pass and goes on to the
    page that was asked for; a wrong or late one is set a new task."""
    ip = client_ip(request)
    task, given = request.GET.get("task", ""), request.GET.get("answer", "")[:32]
    target = safe_next(request.GET.get("next", "/"), request.get_host())
    try:
        found = signing.loads(task, salt=TASK_SALT, max_age=TASK_SECONDS)
    except signing.BadSignature:
        found = None
    driven = DRIVEN.search(request.headers.get("User-Agent", ""))
    if (driven or current()["mode"] == AWAY or not found or found.get("a") != address_key(ip) or not given.isdigit()
            or not solves(task, given)):
        return HttpResponseRedirect(target)
    tally("passed")
    response = HttpResponseRedirect(target)
    response.set_cookie(
        COOKIE, signing.dumps(address_key(ip), salt=PASS_SALT), max_age=PASS_DAYS * 24 * 3600,
        httponly=True, samesite="Lax", secure=request.is_secure() or not settings.DEBUG,
    )
    return response


def check_url(task, answer_value, target):
    """The address an answer is sent to (for the tests)."""
    return f"{PATH}?{urlencode({'task': task, 'answer': answer_value, 'next': target})}"
