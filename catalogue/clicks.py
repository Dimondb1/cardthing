"""
Which clicks to a shop came from a person on our pages, and where to send the rest.

A click through /go/ carries our affiliate tracking (eBay's campaign id, Awin, CJ) only when it looks like a
person who pressed a button on one of our pages:

* the request is a GET (link checkers often send HEAD),
* it has a Referer from our own site (the site's Referrer-Policy sends our origin with every click from a page),
* the browser does not say it is a robot, and is not prefetching or building a link preview,
* the connection has not already made more than CLICKS_PER_HOUR such clicks this hour.

Everything else is a "direct hit": the visitor still reaches the shop, at the plain product address with no
tracking, and the click is saved as suspect so Insights can show how many there were. Robots that pass as
browsers, link scanners and scripts then never reach eBay with our campaign id, so the partner programme sees
only clicks that could become sales.

The hourly cap counts a one-way hash of the internet address and the day's secret, held in the cache for an
hour. The address itself is never stored.
"""

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.core.cache import cache
from django.urls import Resolver404, resolve
from django.utils import timezone

from .insights import client_ip, is_bot

CLICKS_PER_HOUR = 30
OWN_HOSTS = ("ripraptor.com", "www.ripraptor.com")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
PREFETCH_HEADERS = ("Purpose", "Sec-Purpose", "X-Moz", "X-Purpose")

# eBay Partner Network parameters on an itemAffiliateWebUrl, and the rover parameters older links carry.
EBAY_TRACKING = {"mkevt", "mkcid", "mkrid", "campid", "customid", "toolid", "siteid", "mkgroupid", "mksrc", "mktype",
                 "ul_noapp", "amdata", "_trkparms", "_trksid", "ssspo", "sssrc", "ssuid", "widget_ver", "media"}
# Amazon Associates parameters on a product link.
AMAZON_TRACKING = {"tag", "linkcode", "linkid", "ascsubtag", "creative", "creativeasin", "camp"}
# Affiliate networks' click addresses, and the parameter that carries the shop's own address when there is one.
# A feed can give us one of these as the product's link (Awin's aw_deep_link, a CJ or Rakuten deep link).
REDIRECTORS = {
    "awin1.com": "ued",
    "anrdoezrs.net": "url", "dpbolvw.net": "url", "jdoqocy.com": "url", "kqzyfj.com": "url", "tkqlhce.com": "url",
    "emjcd.com": "url", "linksynergy.com": "murl",
}


# The pages a click can come from: the name of the page's address (web/urls.py), and the word kept for it.
PAGES = {"home": "home", "product": "product", "game": "game", "set": "set", "search": "search", "deals": "deals",
         "new": "new", "drops": "drops", "watchlist": "watchlist", "deck": "swipe", "games": "games"}


# The links to a product's prices a Compare tap can name (data-compare, counted by /api/note/?what=compare).
COMPARE_PLACES = ("buy_box", "card", "featured", "picks", "recent", "trending", "out_now", "instead", "hub_cheapest")


def page_of(request):
    """The page the click came from, read from its Referer (a click is only counted when it has one from our
    pages), or the ``from`` the link names (watchlist links before 22 Oct 2026), or ""."""
    if from_our_pages(request):
        try:
            name = resolve(urlsplit(request.headers.get("Referer", "")).path).url_name
        except (Resolver404, ValueError):
            name = None
        if name in PAGES:
            return PAGES[name]
    named = request.GET.get("from", "")
    return named if named in PAGES.values() else ""


def button_of(request):
    """The button pressed, as the link names it (?b=), or "" for a link from an older page."""
    from .models import OutboundClick

    named = request.GET.get("b", "")
    return named if named in OutboundClick.Button.values else ""


def own_hosts(request):
    hosts = set(OWN_HOSTS)
    site = urlsplit(settings.RIPRAPTOR_SITE_URL).hostname
    if site:
        hosts.update({site.lower(), "www." + site.lower().removeprefix("www.")})
    request_host = urlsplit("//" + request.get_host()).hostname
    if request_host:
        hosts.add(request_host.lower())
    if settings.DEBUG:
        hosts.update(LOCAL_HOSTS)
    return hosts


def from_our_pages(request):
    """True when the Referer's origin is this site."""
    referer = request.headers.get("Referer", "")
    try:
        parts = urlsplit(referer)
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(host) and host in own_hosts(request)


def prefetching(request):
    """True for a browser prefetch or prerender rather than a press of the button."""
    return any("prefetch" in request.headers.get(name, "").lower() or "preview" in request.headers.get(name, "").lower()
               for name in PREFETCH_HEADERS)


def over_cap(request):
    """Count this click against its connection's hour and say whether it is past CLICKS_PER_HOUR."""
    now = timezone.now()
    secret = f"{settings.SECRET_KEY}:go:{now:%Y-%m-%d-%H}"
    key = "go:" + hashlib.sha256(f"{secret}|{client_ip(request)}".encode()).hexdigest()[:24]
    cache.add(key, 0, 3600)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, 3600)
        count = 1
    return count > CLICKS_PER_HOUR


def suspect_reason(request):
    """"" for a click from a person on our pages, otherwise a short word for why it is not counted."""
    if request.method != "GET":
        return "method"
    if is_bot(request):
        return "robot"
    if prefetching(request):
        return "prefetch"
    if not from_our_pages(request):
        return "referer"
    if over_cap(request):
        return "cap"
    return ""


def with_click_ref(url, number):
    """An eBay Partner Network link with the click's number as its custom id, which eBay's report names, so a
    sale can be matched to its click. Other links are left alone: theirs is the {click} in the shop's
    affiliate link format. Nothing is added without a number or to a link without our campaign."""
    if not number:
        return url
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not (host == "ebay.com" or ".ebay." in f".{host}" or host.startswith("ebay.")):
        return url
    query = parse_qsl(parts.query, keep_blank_values=True)
    if not any(name.lower() == "campid" and value for name, value in query):
        return url
    # In its place when the link has one, so the rest of eBay's link is left as it was.
    if any(name.lower() == "customid" for name, _ in query):
        query = [(name, str(number) if name.lower() == "customid" else value) for name, value in query]
    else:
        query.append(("customid", str(number)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def strip_query(url, names):
    parts = urlsplit(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in names]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def redirector(host):
    """The parameter holding the shop's address when host is an affiliate network's click address, else None."""
    for domain, name in REDIRECTORS.items():
        if host == domain or host.endswith("." + domain):
            return name
    return None


def plain_url(url):
    """The shop's own address for a product, without any affiliate tracking we added or the shop's API gave us.

    "" when the address is an affiliate network's click with no shop address inside (Awin's pclick.php), so
    the caller sends the visitor somewhere untracked instead."""
    host = (urlsplit(url).hostname or "").lower()
    if host == "ebay.com" or ".ebay." in f".{host}" or host.startswith("ebay."):
        return strip_query(url, EBAY_TRACKING)
    if ".amazon." in f".{host}" or host.startswith("amazon."):
        return strip_query(url, AMAZON_TRACKING)
    name = redirector(host)
    if name is not None:
        inside = dict((k.lower(), v) for k, v in parse_qsl(urlsplit(url).query, keep_blank_values=True)).get(name, "")
        if urlsplit(inside).scheme in ("http", "https") and redirector((urlsplit(inside).hostname or "").lower()) is None:
            return plain_url(inside)
        return ""
    return url
