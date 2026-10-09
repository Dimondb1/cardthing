"""The Crawl health page: what each shop's reading is doing, with one-tap buttons to change it."""

import re
import tempfile
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue import crawl
from catalogue.models import ImportRun, Retailer, WorkerState
from catalogue.testing import make_retailer


class CrawlPageTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        moved = override_settings(RIPRAPTOR_CACHE_DIR=self.folder)
        moved.enable()
        self.addCleanup(moved.disable)
        self.now = timezone.now()
        self.shop = self.make_shop("Harbour Games")
        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))
        self.url = reverse("crawl")

    def make_shop(self, name, **kwargs):
        kwargs.setdefault("source_type", Retailer.Source.SHOPIFY)
        kwargs.setdefault("source_url", f"https://{name.lower().replace(' ', '-')}.example/")
        return make_retailer(name, **kwargs)

    def run_due(self, *args, **options):
        """import_prices with a fake read. Returns the names read and what it printed."""
        read = []

        def fake_run(retailer, feed_path=None):
            read.append(retailer.name)
            return ImportRun.objects.create(retailer=retailer, finished_at=timezone.now())

        out = StringIO()
        with mock.patch("catalogue.management.commands.import_prices.run_import", fake_run):
            call_command("import_prices", *args, stdout=out, stderr=StringIO(), **options)
        return read, out.getvalue()

    def test_page_is_staff_only_and_linked_from_the_admin_index_and_insights(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Crawl health")
        self.assertContains(page, "Harbour Games")
        self.assertContains(page, 'id="shop-harbour-games"')
        self.assertContains(self.client.get(reverse("admin:index")), self.url)
        self.assertContains(self.client.get(reverse("insights")), f"{self.url}#shop-harbour-games")
        self.client.logout()
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 302)
        self.assertIn("/admin/login/", page["Location"])
        self.client.post(self.url, {"action": "pause", "shop": self.shop.pk})
        self.shop.refresh_from_db()
        self.assertFalse(self.shop.reading_paused)

    def test_read_now_clears_the_wait_and_pause_and_resume_set_the_shop(self):
        self.shop.next_read_at = self.now + timedelta(hours=3)
        self.shop.backoff_until = self.now + timedelta(hours=2)
        self.shop.error_streak = 5
        self.shop.save()
        page = self.client.post(self.url, {"action": "read_now", "shop": self.shop.pk}, follow=True)
        self.assertRedirects(page, self.url)
        self.assertContains(page, "Harbour Games will be read at the next hourly read.")
        self.shop.refresh_from_db()
        self.assertLessEqual(self.shop.next_read_at, timezone.now())
        self.assertGreaterEqual(self.shop.next_read_at, self.now)
        self.assertEqual((self.shop.backoff_until, self.shop.error_streak), (None, 0))
        self.assertEqual(self.run_due(due=True)[0], ["Harbour Games"])

        page = self.client.post(self.url, {"action": "pause", "shop": self.shop.pk}, follow=True)
        self.assertRedirects(page, self.url)
        self.assertContains(page, "Harbour Games is paused.")
        self.shop.refresh_from_db()
        self.assertTrue(self.shop.reading_paused)
        self.assertContains(page, 'value="Resume"')
        # A second tap from a page loaded before the first keeps it paused.
        self.client.post(self.url, {"action": "pause", "shop": self.shop.pk})
        self.shop.refresh_from_db()
        self.assertTrue(self.shop.reading_paused)
        page = self.client.post(self.url, {"action": "read_now", "shop": self.shop.pk}, follow=True)
        self.assertContains(page, "It is paused: tap Resume as well.")

        page = self.client.post(self.url, {"action": "resume", "shop": self.shop.pk}, follow=True)
        self.assertRedirects(page, self.url)
        self.assertContains(page, "Harbour Games will be read on its schedule again.")
        self.shop.refresh_from_db()
        self.assertFalse(self.shop.reading_paused)

    def test_buttons_only_act_on_shops_read_on_a_schedule(self):
        by_hand = make_retailer("Hand Shop", source_type=Retailer.Source.MANUAL)
        self.assertEqual(self.client.post(self.url, {"action": "pause", "shop": by_hand.pk}).status_code, 404)
        self.assertEqual(self.client.post(self.url, {"action": "pause", "shop": "nonsense"}).status_code, 404)
        self.assertRedirects(self.client.post(self.url, {"action": "something else"}), self.url)
        self.assertNotContains(self.client.get(self.url), "Hand Shop")

    def test_pause_all_stops_scheduled_reads_until_resume_all(self):
        page = self.client.post(self.url, {"action": "pause_all"}, follow=True)
        self.assertRedirects(page, self.url)
        self.assertContains(page, "Reading is paused for every shop.")
        self.assertContains(page, "Paused for every shop.")
        self.assertContains(page, 'value="Resume all"')
        self.assertTrue(crawl.all_paused())
        read, said = self.run_due(due=True)
        self.assertEqual(read, [])
        self.assertIn("Reading is paused for every shop.", said)
        self.shop.refresh_from_db()
        self.assertIsNone(self.shop.next_read_at)
        # A shop named by hand is still read.
        self.assertEqual(self.run_due("harbour-games", due=True)[0], ["Harbour Games"])
        # Each shop's own pause is left alone.
        self.assertFalse(Retailer.objects.filter(reading_paused=True).exists())

        page = self.client.post(self.url, {"action": "resume_all"}, follow=True)
        self.assertRedirects(page, self.url)
        self.assertContains(page, "Reading has resumed.")
        self.assertContains(page, 'value="Pause all"')
        self.assertFalse(crawl.all_paused())
        # The read by hand set its next read an hour on; Read now brings it back.
        self.client.post(self.url, {"action": "read_now", "shop": self.shop.pk})
        self.assertEqual(self.run_due(due=True)[0], ["Harbour Games"])

    def test_clearing_the_shared_cache_keeps_pause_all(self):
        from django.core.cache.backends.filebased import FileBasedCache

        shared = FileBasedCache(self.folder, {})
        shared.set("home", "cached lists")
        crawl.pause_all()
        shared.clear()
        self.assertIsNone(shared.get("home"))
        self.assertTrue(crawl.all_paused())
        # Pause all is the background reader's row, which the reader and the cron both read.
        self.assertTrue(WorkerState.objects.get().paused)

    def test_a_pause_file_left_by_the_earlier_version_still_pauses_until_resume_all(self):
        from pathlib import Path

        (Path(self.folder) / crawl.FLAG_NAME).touch()
        self.assertTrue(crawl.all_paused())
        self.assertEqual(self.run_due(due=True)[0], [])
        self.client.post(self.url, {"action": "resume_all"})
        self.assertFalse((Path(self.folder) / crawl.FLAG_NAME).exists())
        self.assertFalse(crawl.all_paused())

    def test_a_busy_database_says_so(self):
        from django.db import OperationalError

        with mock.patch("catalogue.crawl.pause_all", side_effect=OperationalError("database is locked")):
            page = self.client.post(self.url, {"action": "pause_all"}, follow=True)
        self.assertContains(page, "The database was busy, so nothing changed.")
        self.assertFalse(crawl.all_paused())

    def test_each_shop_shows_its_state_and_what_went_wrong(self):
        ImportRun.objects.create(retailer=self.shop, started_at=self.now - timedelta(minutes=2))
        paused = self.make_shop("Paused Shop", reading_paused=True)
        ImportRun.objects.create(retailer=paused, started_at=self.now - timedelta(hours=2), finished_at=self.now)
        self.make_shop(
            "Waiting Shop", backoff_until=self.now + timedelta(minutes=20), error_streak=3,
            last_error="HTTP Error 503: Service Unavailable " + "x" * 300,
        )
        self.make_shop("Quiet Shop", last_ok_at=self.now, last_read_seconds=85, next_read_at=self.now + timedelta(minutes=40))
        # A run left open long ago and followed by one that finished does not count as reading.
        old = self.make_shop("Old Shop")
        ImportRun.objects.create(retailer=old, started_at=self.now - timedelta(days=2))
        ImportRun.objects.create(retailer=old, started_at=self.now - timedelta(hours=1), finished_at=self.now)
        rows = {row["retailer"].name: row for row in crawl.shops(self.now)}
        self.assertEqual(rows["Harbour Games"]["state"], "Reading")
        self.assertEqual(rows["Paused Shop"]["state"], "Paused")
        self.assertTrue(rows["Waiting Shop"]["state"].startswith("Backing off until "))
        self.assertEqual(len(rows["Waiting Shop"]["last_error"]), 160)
        self.assertEqual(rows["Quiet Shop"]["state"], "Idle")
        self.assertEqual(rows["Old Shop"]["state"], "Idle")
        self.assertEqual(rows["Harbour Games"]["next_read"], "due now")
        page = self.client.get(self.url)
        self.assertContains(page, "took 85 s")
        self.assertContains(page, "Errors in a row: 3.")
        self.assertContains(page, "HTTP Error 503: Service Unavailable")
        self.assertContains(page, "The last read finished at")

    def test_the_page_runs_in_a_fixed_number_of_queries(self):
        def count():
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get(self.url).status_code, 200)
            return len(queries)

        few = count()
        for n in range(48):
            shop = self.make_shop(f"Shop {n:02d}", error_streak=n % 3, last_error="HTTP Error 500" if n % 3 else "",
                                  backoff_until=self.now + timedelta(minutes=n) if n % 3 else None,
                                  reading_paused=n % 7 == 0)
            ImportRun.objects.create(retailer=shop, finished_at=None if n % 5 else self.now)
        self.assertEqual(Retailer.objects.count(), 49)
        with self.assertNumQueries(few):
            page = self.client.get(self.url)
        self.assertContains(page, "Shops (49)")
        # Session, user, the last finished read, the background reader's row, four for how hot each product
        # is, and the shops.
        self.assertEqual(few, 9)

    def test_copy_has_no_em_dashes_or_exclamation_marks(self):
        self.make_shop("Waiting Shop", backoff_until=self.now + timedelta(minutes=20), error_streak=1, last_error="Timed out")
        self.make_shop("Paused Shop", reading_paused=True)
        pages = [self.client.get(self.url)]
        for action in ("read_now", "pause", "read_now", "resume"):
            pages.append(self.client.post(self.url, {"action": action, "shop": self.shop.pk}, follow=True))
        for action in ("pause_all", "resume_all"):
            pages.append(self.client.post(self.url, {"action": action}, follow=True))
        pages.append(self.client.get(reverse("admin:index")))
        for page in pages:
            text = page.content.decode()
            self.assertNotIn("\u2014", text)
            self.assertNotIn("&mdash;", text)
            # Admin's own scripts and comments are not copy; every visible word is.
            visible = re.sub(r"<script.*?</script>|<!--.*?-->", "", text.split("<body", 1)[1], flags=re.S)
            self.assertIn("Read now" if page.request["PATH_INFO"] == self.url else "Crawl health", visible)
            self.assertNotIn("!", visible)


    def alerts_line(self):
        page = self.client.get(self.url).content.decode()
        found = re.search(r'id="alerts-line">([^<]+)<', page)
        self.assertIsNotNone(found, "the page does not say how problems reach the owner")
        return found.group(1)

    @override_settings(RIPRAPTOR_NTFY_TOPIC="", RIPRAPTOR_INBOX_NOTIFY_EMAIL="", RIPRAPTOR_CRAWL_PUSHES=True)
    def test_the_page_says_when_nothing_reaches_the_owners_phone(self):
        self.assertIn("Nothing tells you about crawl problems yet", self.alerts_line())

    @override_settings(RIPRAPTOR_NTFY_TOPIC="a-long-topic", RIPRAPTOR_INBOX_NOTIFY_EMAIL="", RIPRAPTOR_CRAWL_PUSHES=True)
    def test_the_page_says_problems_are_pushed(self):
        self.assertIn("You are told by push when the reader stops", self.alerts_line())

    @override_settings(RIPRAPTOR_NTFY_TOPIC="a-long-topic", RIPRAPTOR_CRAWL_PUSHES=False)
    def test_the_page_says_when_problem_alerts_are_off(self):
        self.assertIn("turned off on the server", self.alerts_line())


class PauseAllReachesEveryReadTests(TestCase):
    """Pause all and a shop's own pause stop every request to the shops, not only the next hourly run."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        moved = override_settings(RIPRAPTOR_CACHE_DIR=folder.name)
        moved.enable()
        self.addCleanup(moved.disable)
        self.now = timezone.now()

    def make_shop(self, name, **kwargs):
        kwargs.setdefault("source_type", Retailer.Source.SHOPIFY)
        kwargs.setdefault("source_url", f"https://{name.lower().replace(' ', '-')}.example/")
        return make_retailer(name, **kwargs)

    def test_pause_all_tapped_during_a_run_stops_it_before_the_next_shop(self):
        for name in ("Aaa Shop", "Bbb Shop", "Ccc Shop"):
            self.make_shop(name)
        read = []

        def fake_run(retailer, feed_path=None):
            read.append(retailer.name)
            # The owner taps Pause all while the first shop is being read.
            crawl.pause_all()
            return ImportRun.objects.create(retailer=retailer, finished_at=timezone.now())

        out = StringIO()
        with mock.patch("catalogue.management.commands.import_prices.run_import", fake_run):
            call_command("import_prices", due=True, stdout=out, stderr=StringIO())
        self.assertEqual(read, ["Aaa Shop"])
        self.assertIn("Reading is paused for every shop.", out.getvalue())
        self.assertNotIn("No shops are due.", out.getvalue())
        # The shops left are still due once reading resumes.
        crawl.resume_all()
        self.assertEqual(list(Retailer.due(timezone.now()).values_list("name", flat=True)), ["Bbb Shop", "Ccc Shop"])

    def test_a_run_left_open_is_closed_while_pause_all_is_on(self):
        shop = self.make_shop("Hung Shop")
        run = ImportRun.objects.create(retailer=shop, started_at=self.now - timedelta(days=2))
        self.assertEqual(crawl.shops(self.now)[0]["state"], "Reading")
        crawl.pause_all()
        out = StringIO()
        with mock.patch("catalogue.management.commands.import_prices.run_import") as fake_run:
            call_command("import_prices", due=True, stdout=out, stderr=StringIO())
        fake_run.assert_not_called()
        self.assertIn("Closed 1 earlier runs", out.getvalue())
        self.assertIn("Reading is paused for every shop.", out.getvalue())
        run.refresh_from_db()
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(crawl.shops(timezone.now())[0]["state"], "Idle")

    def test_next_read_never_claims_a_read_that_will_not_happen(self):
        self.make_shop("Backoff Shop", next_read_at=self.now - timedelta(minutes=5),
                       backoff_until=self.now + timedelta(hours=3), error_streak=4)
        self.make_shop("Late Shop", next_read_at=self.now + timedelta(hours=2),
                       backoff_until=self.now + timedelta(minutes=10), error_streak=1)
        self.make_shop("Paused Shop", reading_paused=True)
        self.make_shop("Due Shop", next_read_at=self.now - timedelta(minutes=1),
                       backoff_until=self.now - timedelta(minutes=1))
        self.make_shop("Waiting Shop", next_read_at=self.now + timedelta(minutes=40))
        rows = {row["retailer"].name: row["next_read"] for row in crawl.shops(self.now)}
        due = set(Retailer.due(self.now).values_list("name", flat=True))
        self.assertEqual(rows["Backoff Shop"], crawl.clock(self.now + timedelta(hours=3), self.now))
        self.assertEqual(rows["Late Shop"], crawl.clock(self.now + timedelta(hours=2), self.now))
        self.assertEqual(rows["Paused Shop"], "paused")
        self.assertEqual(rows["Due Shop"], "due now")
        self.assertEqual(rows["Waiting Shop"], crawl.clock(self.now + timedelta(minutes=40), self.now))
        # "due now" is said of exactly the shops a run would read now.
        self.assertEqual({name for name, said in rows.items() if said == "due now"}, due)
        crawl.pause_all()
        self.assertEqual({row["next_read"] for row in crawl.shops(self.now)}, {"paused"})

    def test_the_stock_watch_leaves_paused_shops_alone(self):
        from catalogue.testing import make_game, make_listing, make_product, make_set

        product = make_product(make_set(make_game()))
        reading = self.make_shop("Reading Shop")
        paused = self.make_shop("Paused Shop", reading_paused=True)
        make_listing(product, reading, availability="out_of_stock", url="https://reading-shop.example/products/etb", hours_ago=5)
        make_listing(product, paused, availability="out_of_stock", url="https://paused-shop.example/products/etb", hours_ago=5)
        asked = []

        def fetch(url):
            asked.append(url)
            return b'{"variants": [{"price": 4999, "available": false}]}'

        with mock.patch("catalogue.importers.fetch", fetch):
            call_command("watch_stock", "--pause", "0", stdout=StringIO())
            self.assertEqual(asked, ["https://reading-shop.example/products/etb.js"])
            asked.clear()
            crawl.pause_all()
            out = StringIO()
            call_command("watch_stock", "--pause", "0", stdout=out)
        self.assertEqual(asked, [])
        self.assertIn("Reading is paused for every shop.", out.getvalue())


class CrawlOrderTests(TestCase):
    def test_shops_with_errors_come_first_then_paused_then_the_rest_by_name(self):
        from catalogue.testing import make_retailer

        now = timezone.now()
        make_retailer("Aardvark Cards", source_type=Retailer.Source.SHOPIFY)
        make_retailer("Zebra Games", source_type=Retailer.Source.SHOPIFY, error_streak=2,
                      backoff_until=now + timedelta(minutes=20), last_error="HTTP Error 500")
        make_retailer("Middle Cards", source_type=Retailer.Source.SHOPIFY, reading_paused=True)
        make_retailer("Beta Cards", source_type=Retailer.Source.SHOPIFY, error_streak=1)
        names = [row["retailer"].name for row in crawl.shops(now)]
        self.assertEqual(names, ["Beta Cards", "Zebra Games", "Middle Cards", "Aardvark Cards"])
