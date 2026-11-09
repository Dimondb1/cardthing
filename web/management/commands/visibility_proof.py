"""
Fetch each page built for search engines and AI assistants as each crawler would, and check the raw HTML says what it
should (web/proof.py). Prints one line a check and ends with how many failed.

    python manage.py visibility_proof                    the live site (RIPRAPTOR_SITE_URL), over the internet
    python manage.py visibility_proof --site http://localhost:8124
    python manage.py visibility_proof --inside           this machine's own copy, with no network: what the site
                                                         serves a crawler it knows, whatever address asks
    python manage.py visibility_proof --set /games/pokemon/prismatic-evolutions/

Google and Bing are known to the site by their addresses, so a fetch from anywhere else that only uses their name is
shown the visitor check, as it should be: those rows say so and do not count as failures. Run with --inside on the
server to see the page they are served.
"""

import urllib.error
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand

from web import proof

GAMES = ("/games/pokemon/", "/games/magic-the-gathering/")
TIMEOUT = 40


class Command(BaseCommand):
    help = "Check the pages built for search engines and AI assistants, fetched as each crawler."

    def add_arguments(self, parser):
        parser.add_argument("--site", default="", help="The site to fetch. Default: RIPRAPTOR_SITE_URL.")
        parser.add_argument("--inside", action="store_true", help="Ask this machine's own copy, with no network.")
        parser.add_argument("--set", default="", dest="set_path", help="A set page to check. Default: one of Pokémon's.")

    def handle(self, *args, site="", inside=False, set_path="", **options):
        self.site = (site or settings.RIPRAPTOR_SITE_URL).rstrip("/")
        self.inside = inside
        self.failed = 0
        set_path = set_path or self.a_set()
        llms = self.fetch("/llms.txt", "ClaudeBot")[1]
        sitemap = self.fetch("/sitemap.xml", "ClaudeBot")[1]
        self.stdout.write(f"Site: {self.site}{' (this machine, no network)' if inside else ''}")
        self.report("/llms.txt", "", proof.llms(llms, self.site))
        self.report("/sitemap.xml", "", proof.sitemap(sitemap, self.site))
        pages = [("/", proof.home)]
        pages += [(path, lambda html, path=path: proof.game(html, proof.llms_line(llms, path))) for path in GAMES]
        if set_path:
            pages.append((set_path, proof.product_set))
        pages += [(proof.GUIDE, proof.guide), ("/guides/", proof.guides_index)]
        for path, check in pages:
            for crawler in proof.CRAWLERS:
                status, html = self.fetch(path, crawler)
                if status == 200 and proof.is_check_page(html):
                    known = crawler in proof.VERIFIED and not inside
                    self.line(known, path, crawler, "visitor check shown",
                              "expected: this address is not the crawler's own" if known else "the page was not served")
                    continue
                self.report(path, crawler, [("answers 200", status == 200, f"status {status}, {len(html):,} bytes")]
                            + (check(html) if status == 200 else []))
        self.stdout.write(f"\n{'All checks passed.' if not self.failed else f'{self.failed} checks FAILED.'}")
        if self.failed:
            raise SystemExit(1)

    def a_set(self):
        """A Pokémon set with something in stock, from this machine's catalogue, or "" without one."""
        from catalogue.models import ProductSet
        from web import hubs

        stocked = hubs.in_stock_listings().values("product__product_set")
        found = (ProductSet.objects.filter(game__slug="pokemon", game__is_active=True, pk__in=stocked)
                 .select_related("game").order_by("-release_date", "pk").first())
        return found.get_absolute_url() if found else ""

    def fetch(self, path, crawler):
        """(status, text) of ``path`` asked for as ``crawler``."""
        agent = proof.CRAWLERS[crawler]
        if self.inside:
            from urllib.parse import urlsplit

            from django.test import Client

            site = urlsplit(self.site)
            response = Client(HTTP_USER_AGENT=agent, HTTP_HOST=site.netloc).get(path, secure=site.scheme == "https")
            body = b"".join(response.streaming_content) if getattr(response, "streaming", False) else response.content
            return response.status_code, body.decode("utf-8", "replace")
        request = urllib.request.Request(self.site + path, headers={"User-Agent": agent, "Accept": "text/html,*/*"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, ""
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return 0, exc.__class__.__name__

    def report(self, path, crawler, checks):
        for name, passed, detail in checks:
            self.line(passed, path, crawler, name, detail)

    def line(self, passed, path, crawler, name, detail):
        if not passed:
            self.failed += 1
        self.stdout.write(f"{'ok  ' if passed else 'FAIL'} {path} {f'[{crawler}] ' if crawler else ''}{name}"
                          f"{f' ({detail})' if detail else ''}")
