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
server to see the page they are served, or open Search visibility in Admin, which shows the same on a page.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from web import proof


class Command(BaseCommand):
    help = "Check the pages built for search engines and AI assistants, fetched as each crawler."

    def add_arguments(self, parser):
        parser.add_argument("--site", default="", help="The site to fetch. Default: RIPRAPTOR_SITE_URL.")
        parser.add_argument("--inside", action="store_true", help="Ask this machine's own copy, with no network.")
        parser.add_argument("--set", default="", dest="set_path", help="A set page to check. Default: one of Pokémon's.")

    def handle(self, *args, site="", inside=False, set_path="", **options):
        site = (site or settings.RIPRAPTOR_SITE_URL).rstrip("/")
        fetch = proof.inside(site) if inside else proof.over_network(site)
        self.stdout.write(f"Site: {site}{' (this machine, no network)' if inside else ''}")
        rows = proof.run(fetch, site, set_path or proof.a_set(), known_by_address=not inside)
        for row in rows:
            self.stdout.write(f"{'ok  ' if row.passed else 'FAIL'} {row.path} {f'[{row.crawler}] ' if row.crawler else ''}"
                              f"{row.name}{f' ({row.detail})' if row.detail else ''}")
        failed = sum(1 for row in rows if not row.passed)
        self.stdout.write(f"\n{'All checks passed.' if not failed else f'{failed} checks FAILED.'}")
        if failed:
            raise SystemExit(1)
