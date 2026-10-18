"""
Why traffic changed, in plain text: the same findings as the top of the Insights page.

    python manage.py explain_traffic
    python manage.py explain_traffic --at 2026-10-18T13:13

Prints the ranked findings, today so far against the same hours last week and yesterday, yesterday against
its usual, where the change came from by source, the burst days left out of the averages, and the health
checks. ``--at`` explains as if it were that moment (local time), for looking back at a day.
"""

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from catalogue.traffic import explain, number


class Command(BaseCommand):
    help = "Explain why traffic changed: like-for-like comparisons, bursts, drivers and health checks."

    def add_arguments(self, parser):
        parser.add_argument("--at", help="Explain as at this local time, for example 2026-10-18T13:13.")

    def handle(self, *args, **options):
        now = None
        if options["at"]:
            try:
                now = timezone.make_aware(datetime.fromisoformat(options["at"]))
            except ValueError as error:
                raise CommandError(f"--at needs a time like 2026-10-18T13:13 ({error})")
        result = explain(now)
        out = self.stdout.write
        out(f"Why traffic changed, at {result['now']:%a %-d %b %H:%M}")
        out("")
        if not result["enough"]:
            out(result["headline"])
            return
        for n, finding in enumerate(result["findings"], 1):
            out(f"{n}. {finding['text']}")
            if finding["check"]:
                out(f"   {finding['check']}")
        for cmp in result["comparisons"]:
            out("")
            out(f"{cmp['label']}: {number(cmp['now'])} people and {number(cmp['now_clicks'])} clicks, against "
                f"{number(cmp['then'])} people and {number(cmp['then_clicks'])} clicks {cmp['basis']}")
            for ref in cmp.get("references", [])[1:]:
                out(f"  and {number(ref['then'])} people {ref['basis']}")
            drivers = cmp.get("drivers")
            if drivers:
                out(f"  {'Source':<24}{'Then':>8}{'Now':>8}{'Change':>8}{'Share':>7}")
                for row in drivers["sources"]:
                    out(f"  {row['label']:<24}{number(row['then']):>8}{number(row['now']):>8}"
                        f"{row['change']:>+8.0f}{row['share']:>6}%")
        if result["bursts"]:
            out("")
            out("Burst days, left out of the averages:")
            for burst in result["bursts"]:
                out(f"  {burst['date']:%a %-d %b}: {'; '.join(burst['reasons'])}")
        out("")
        out("Health:")
        for item in result["health"]:
            out(f"  {item['text']}")
        if not result["health"]:
            out(f"  {result['healthy']}")
        history = result["history"]
        out("")
        out(f"Counting since {history['counting_since']:%-d %b %Y}; "
            + (f"arrival times since {history['timed_since']:%-d %b %Y}." if history["timed_since"]
               else "arrival times not recorded yet."))
