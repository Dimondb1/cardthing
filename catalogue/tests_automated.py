"""Scripts that ran our pages, found visitor by visitor and left out of people and clicks (catalogue/automated.py), and
the click check that a browser ran one of our pages (catalogue/clicks.py). The owner, 7 Nov 2026: "fix the bot clicks
and burst detection, but factor it was a massive Delta Reign Pokémon drop yesterday"."""

from datetime import datetime, timedelta
from io import StringIO
from itertools import count
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase

from catalogue import automated, traffic
from catalogue.models import DailyPageView, DailyVisitor, OutboundClick
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

LONDON = ZoneInfo("Europe/London")
# Saturday 7 Nov 2026 at noon: the day after the Delta Reign drop.
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=LONDON)
TODAY = NOW.date()
DROP_DAY = TODAY - timedelta(days=1)
_tokens = count()


def visitors(day, n, *, country="GB", device="mobile", source="", returning=False, views=1, browser=True,
             landing="home"):
    """``n`` visitors on ``day`` whose browser ran the page unless ``browser`` is False; their tokens."""
    rows = [DailyVisitor(date=day, token=f"v{next(_tokens)}", country=country, device=device, source=source,
                         returning=returning, views=views, browser=browser, landing=landing,
                         first_seen=datetime(day.year, day.month, day.day, 10, tzinfo=LONDON))
            for _ in range(n)]
    DailyVisitor.objects.bulk_create(rows)
    return [row.token for row in rows]


class ClassifyCase(TestCase):
    def setUp(self):
        cache.clear()
        self.product = make_product(make_set(make_game()), name="Delta Reign Elite Trainer Box", slug="delta-etb")
        self.shop = make_retailer("Harbour Games")
        self.listing = make_listing(self.product, self.shop)

    def click(self, day, token, hour=11, **fields):
        return OutboundClick.objects.create(product=self.product, retailer=self.shop, listing=self.listing,
                                            visitor=token, created_at=datetime(day.year, day.month, day.day, hour,
                                                                               tzinfo=LONDON), **fields)

    def calm_days(self, n=10, foreign=5):
        """``n`` ordinary days before the drop: forty people from the UK and a few one-page visits from abroad."""
        for back in range(2, n + 2):
            day = TODAY - timedelta(days=back)
            visitors(day, 40, views=3, source="facebook.com")
            visitors(day, foreign, country="US", device="desktop")


class FloodTests(ClassifyCase):
    def test_a_flood_from_abroad_is_left_out_and_the_drop_days_real_crowd_counts(self):
        self.calm_days()
        # The drop: 600 people from the UK, many on one page from a Discord or app link that sends no referrer.
        drop = visitors(DROP_DAY, 400, views=1) + visitors(DROP_DAY, 200, source="discord.com", views=4, returning=True)
        # And the scripts: 2,000 one-page visits from abroad, typed, new, their browser running the page.
        flood = []
        for country in ("US", "BR", "PK", "BD"):
            flood += visitors(DROP_DAY, 500, country=country, device="desktop", landing="product:delta-etb")
        DailyPageView.objects.create(date=DROP_DAY, kind="product", key="delta-etb", hits=2300)
        done = automated.classify(now=NOW)
        self.assertEqual(done["flood"], 2000)
        self.assertEqual(set(DailyVisitor.objects.filter(automated=True).values_list("token", flat=True)), set(flood))
        self.assertFalse(DailyVisitor.objects.filter(token__in=drop, automated=True).exists())
        # Their page views are taken off the product's count, so the flood does not make it the most opened.
        self.assertEqual(DailyPageView.objects.get(date=DROP_DAY, key="delta-etb").hits, 300)
        # The day still counts, with its real people only, and the flood is named on Insights.
        why = traffic.explain(NOW)
        self.assertFalse(why["days"][DROP_DAY]["burst"])
        self.assertEqual(why["days"][DROP_DAY]["people"], 600)
        self.assertEqual([(f["date"], f["count"]) for f in why["floods"]], [(DROP_DAY, 2000)])
        self.assertIn("2,000 one-page visits from outside the UK", why["floods"][0]["reason"])
        # Run again: nothing more to mark.
        self.assertEqual(automated.classify(now=NOW), {"direct": 0, "flood": 0, "clicks": 0, "views": 0})

    def test_a_drop_day_alone_marks_nobody(self):
        self.calm_days()
        visitors(DROP_DAY, 900, views=1)                              # typed, one page, new: but from the UK
        visitors(DROP_DAY, 80, country="IE", views=1)
        visitors(DROP_DAY, 30, country="US", device="desktop")        # a few from abroad, as on any day
        visitors(DROP_DAY, 700, country="", views=1)                  # an unknown country is never a flood
        self.assertEqual(automated.classify(now=NOW)["flood"], 0)
        self.assertEqual(traffic.explain(NOW)["days"][DROP_DAY]["people"], 1710)

    def test_more_than_one_page_a_link_to_us_or_a_return_is_never_a_flood(self):
        self.calm_days()
        visitors(DROP_DAY, 2000, country="US", device="desktop")
        kept = (visitors(DROP_DAY, 50, country="US", views=2) + visitors(DROP_DAY, 50, country="US", source="reddit.com")
                + visitors(DROP_DAY, 50, country="US", returning=True) + visitors(DROP_DAY, 50, country="US", browser=False))
        automated.classify(now=NOW)
        self.assertFalse(DailyVisitor.objects.filter(token__in=kept, automated=True).exists())

    def test_with_too_few_days_to_know_the_usual_only_a_plain_flood_counts(self):
        # Two days on record: 400 of 500 browsers in the profile is a flood, 400 of 1,000 is not.
        visitors(DROP_DAY, 100, views=3)
        visitors(DROP_DAY, 400, country="US", device="desktop")
        day = DROP_DAY - timedelta(days=1)
        visitors(day, 600, views=3)
        visitors(day, 400, country="US", device="desktop")
        automated.classify(now=NOW)
        self.assertEqual(DailyVisitor.objects.filter(date=DROP_DAY, automated_why="flood").count(), 400)
        self.assertFalse(DailyVisitor.objects.filter(date=day, automated=True).exists())

    def test_once_a_day_is_a_flood_its_later_scripts_are_left_out_too(self):
        self.calm_days()
        visitors(TODAY, 1000, country="US", device="desktop")
        automated.classify(now=NOW)
        later = visitors(TODAY, 40, country="BR", device="desktop")
        automated.classify(now=NOW, days=1)
        self.assertEqual(DailyVisitor.objects.filter(token__in=later, automated_why="flood").count(), 40)


class DirectHitTests(ClassifyCase):
    def test_a_visitor_that_sent_a_shop_link_without_our_page_is_a_script_and_its_clicks_go_too(self):
        self.calm_days()
        [bot] = visitors(DROP_DAY, 1, country="GB", device="desktop", views=2)
        [busy] = visitors(DROP_DAY, 1, views=2)
        [person] = visitors(DROP_DAY, 1, views=2)
        self.click(DROP_DAY, bot, suspect=True, suspect_why="referer")
        tracked = self.click(DROP_DAY, bot, hour=12, ran_page=True)
        # Over the hourly cap is a busy shared connection, not evidence.
        self.click(DROP_DAY, busy, suspect=True, suspect_why="cap")
        mine = self.click(DROP_DAY, person, ran_page=True)
        done = automated.classify(now=NOW)
        self.assertEqual((done["direct"], done["clicks"]), (1, 1))
        self.assertEqual(DailyVisitor.objects.get(token=bot).automated_why, "direct")
        self.assertFalse(DailyVisitor.objects.filter(token__in=[busy, person], automated=True).exists())
        self.assertEqual(list(OutboundClick.objects.people()), [mine])
        tracked.refresh_from_db()
        self.assertTrue(tracked.automated)
        self.assertFalse(tracked.suspect)   # it was sent with tracking: not a direct hit, just not a person's


class OldClickTests(ClassifyCase):
    def test_a_click_from_before_the_check_whose_browser_never_ran_a_page_is_left_out(self):
        visitors(TODAY - timedelta(days=5), 30, views=2)   # pages reported in from then on
        [fetcher] = visitors(DROP_DAY, 1, browser=False, views=1)
        [person] = visitors(DROP_DAY, 1, views=2)
        script = self.click(DROP_DAY, fetcher)
        kept = self.click(DROP_DAY, person)
        cookie = self.click(DROP_DAY, "a-token-with-no-row", ran_page=True)   # passed by its cookie when made
        recent = self.click(TODAY, "another")
        OutboundClick.objects.filter(pk=recent.pk).update(created_at=NOW - timedelta(minutes=1))   # ahead of its page's report
        automated.classify(now=NOW)
        self.assertEqual(set(OutboundClick.objects.people()), {kept, cookie, recent})
        script.refresh_from_db()
        self.assertTrue(script.automated)

    def test_clicks_before_pages_reported_in_are_never_judged(self):
        [old] = visitors(TODAY - timedelta(days=20), 1, browser=False)
        click = self.click(TODAY - timedelta(days=20), old)
        visitors(TODAY - timedelta(days=5), 30, views=2)
        automated.classify(now=NOW)
        click.refresh_from_db()
        self.assertFalse(click.automated)


class CommandTests(ClassifyCase):
    def test_classify_visits_says_what_it_marked(self):
        self.calm_days()
        visitors(DROP_DAY, 1500, country="US", device="desktop")
        out = StringIO()
        with self.settings(TIME_ZONE="Europe/London"):
            call_command("classify_visits", stdout=out)
        self.assertIn("in floods", out.getvalue())

    def test_the_installer_and_the_hourly_tidy_run_it(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        self.assertIn("manage.py classify_visits", (root / "deploy" / "install.sh").read_text())
        self.assertIn("automated.classify()", (root / "catalogue" / "management" / "commands" / "tidy_all.py").read_text())
