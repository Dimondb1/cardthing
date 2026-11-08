"""
The Content Security Policy: what a page may run and load, so a script slipped into a page (an XSS) does not run.

Every response carries the header. Each request gets a nonce, a random token the templates put on every script tag
(``csp_nonce``, web.context_processors.site): only scripts carrying it run, and with 'strict-dynamic' the scripts
they load in turn (Awin's tag, Google's). 'self', https: and 'unsafe-inline' are there for browsers too old to
know nonces, which ignore the rest; a browser that knows nonces ignores them. Styles may be inline (the templates
set a few custom properties in style attributes); images may come from any https address (the shops' own pictures);
everything else is the site's own. The admin's own scripts carry no nonce (Django's templates), so there the
policy is 'self' and the nonce without 'strict-dynamic'. A page kept whole in the cache (web.pagecache) is served
with a fresh nonce written into it, so no two responses share one.
"""

import secrets

from django.conf import settings

HEADER = "Content-Security-Policy"
ADMIN_PREFIX = "/admin/"
# Awin's MasterTag (web/templates/web/base.html) talks to these; Google's tag and adverts to those.
AWIN = ["https://*.awin1.com", "https://*.dwin1.com", "https://*.dwin2.com", "https://*.awin.com"]
GOOGLE_ANALYTICS = ["https://*.google-analytics.com", "https://*.analytics.google.com",
                    "https://*.googletagmanager.com", "https://*.g.doubleclick.net", "https://*.google.com"]
GOOGLE_ADS = ["https://*.googlesyndication.com", "https://*.doubleclick.net", "https://*.google.com",
              "https://*.googleadservices.com", "https://*.adtrafficquality.google"]


def nonce():
    """A nonce for one response: 24 characters nobody can guess."""
    return secrets.token_urlsafe(18)


def policy(nonce, admin=False, form_action="'self'"):
    """The header's value for a page with ``nonce`` on its scripts. ``form_action`` is where its forms may go, and
    where a form's answer may redirect: the Continue page's form lands on a shop (web.views.gate)."""
    connect, frames = ["'self'"], []
    if admin:
        script = f"'self' 'nonce-{nonce}'"
    else:
        script = f"'nonce-{nonce}' 'strict-dynamic' 'self' https: 'unsafe-inline'"
        if getattr(settings, "RIPRAPTOR_AWIN_PUBLISHER_ID", ""):
            connect += AWIN
            frames += AWIN
        if getattr(settings, "RIPRAPTOR_GA_ID", ""):
            connect += GOOGLE_ANALYTICS
        if getattr(settings, "RIPRAPTOR_ADSENSE_CLIENT", ""):
            connect += GOOGLE_ADS
            frames += GOOGLE_ADS
    directives = [
        "default-src 'self'",
        f"script-src {script}",
        "style-src 'self' 'unsafe-inline'",
        # Pictures come from the shops' own addresses, https on the live site; http too while developing (demo pictures).
        "img-src 'self' data: https:" + (" http:" if settings.DEBUG else ""),
        "font-src 'self' data:",
        "connect-src " + " ".join(dict.fromkeys(connect)),
        "frame-src " + (" ".join(dict.fromkeys(frames)) if frames else "'none'"),
        "object-src 'none'",
        "base-uri 'none'",
        f"form-action {form_action}",
        "frame-ancestors 'self'",
    ]
    return "; ".join(directives)


class ContentSecurityPolicyMiddleware:
    """Give each request a nonce, and each response the policy, unless the view set one of its own."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = nonce()
        response = self.get_response(request)
        if HEADER not in response:
            response[HEADER] = policy(request.csp_nonce, admin=request.path.startswith(ADMIN_PREFIX))
        return response
