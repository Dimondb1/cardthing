"""Being found: proof of ownership for Google and Bing, IndexNow for the pages that changed, and the product's facts
as plain sentences. The owner, 31 Oct 2026: RipRaptor lists poorly in Google and in AI answers."""

import json
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalogue import indexnow
from catalogue.models import Listing, Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set


class VerificationTests(TestCase):
    def test_the_tags_show_only_once_set(self):
        html = self.client.get(reverse("web:home")).content.decode()
        self.assertNotIn("google-site-verification", html)
        self.assertNotIn("msvalidate.01", html)
        with self.settings(RIPRAPTOR_GOOGLE_VERIFICATION="abc123", RIPRAPTOR_BING_VERIFICATION="DEF456"):
            html = self.client.get(reverse("web:home")).content.decode()
        self.assertIn('<meta name="google-site-verification" content="abc123">', html)
        self.assertIn('<meta name="msvalidate.01" content="DEF456">', html)


class IndexNowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.set = make_set(make_game())
        self.box = make_product(self.set, name="Surging Sparks Booster Box", product_type="booster_box")
        self.shop = make_retailer("Harbour Games")
        self.listing = make_listing(self.box, self.shop, price="87.00")

    def test_the_key_file_proves_the_site_is_ours_and_nothing_else_answers(self):
        key = indexnow.key()
        self.assertRegex(key, r"^[0-9a-f]{32}$")
        response = self.client.get(f"/{key}.txt")
        self.assertEqual((response.status_code, response.content.decode()), (200, key))
        self.assertEqual(self.client.get("/0123456789abcdef0123456789abcdef.txt").status_code, 404)
        self.assertEqual(self.client.get("/robots.txt").status_code, 200)
        self.assertTrue(self.client.get("/llms.txt").content.startswith(b"# "))

    def test_only_pages_changed_since_the_last_run_are_sent(self):
        now = timezone.now()
        Listing.objects.filter(pk=self.listing.pk).update(changed_at=now - timedelta(hours=3))
        Product.objects.filter(pk=self.box.pk).update(updated_at=now - timedelta(days=3))
        self.set.__class__.objects.filter(pk=self.set.pk).update(updated_at=now - timedelta(days=3))
        url = settings.RIPRAPTOR_SITE_URL + self.box.get_absolute_url()
        self.assertEqual(indexnow.changed_urls(now - timedelta(hours=4), now), [url])
        self.assertEqual(indexnow.changed_urls(now - timedelta(hours=2), now), [])

    def test_a_run_sends_the_pages_and_remembers_when(self):
        sent = []

        def fake_urlopen(request, timeout):
            sent.append(json.loads(request.data))
            return mock.MagicMock(__enter__=lambda s: s, __exit__=lambda *a: None, read=lambda: b"")

        Listing.objects.filter(pk=self.listing.pk).update(changed_at=timezone.now())
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self.assertEqual(indexnow.run(), 1)
            self.assertEqual(indexnow.run(), 0)   # nothing changed since
        body = sent[0]
        self.assertEqual(body["host"], "ripraptor.com")
        self.assertEqual(body["key"], indexnow.key())
        self.assertEqual(body["keyLocation"], f"https://ripraptor.com/{indexnow.key()}.txt")
        self.assertEqual(body["urlList"], [settings.RIPRAPTOR_SITE_URL + self.box.get_absolute_url()])
        self.assertEqual(len(sent), 1)   # no empty submission

    def test_a_refused_submission_is_tried_again_next_hour(self):
        import urllib.error

        Listing.objects.filter(pk=self.listing.pk).update(changed_at=timezone.now())
        refused = urllib.error.HTTPError(indexnow.ENDPOINT, 422, "Unprocessable", {}, None)
        with override_settings(RIPRAPTOR_INDEXNOW=True), mock.patch("urllib.request.urlopen", side_effect=refused):
            with self.assertRaisesMessage(CommandError, "IndexNow answered 422. The same pages go again next hour."):
                call_command("indexnow", stdout=StringIO())
        self.assertIsNone(cache.get(indexnow.LAST_KEY))

    def test_off_in_tests_and_on_a_development_machine_and_counted_with_dry_run(self):
        out = StringIO()
        call_command("indexnow", stdout=out)
        self.assertIn("IndexNow is off", out.getvalue())
        out = StringIO()
        call_command("indexnow", dry_run=True, stdout=out)
        self.assertIn("changed product pages would be sent to IndexNow.", out.getvalue())

    def test_the_hourly_cron_runs_it_on_its_own_lock(self):
        line = ("25 * * * * cd {dir} && set -a && . ./.env && set +a && timeout -k 30 300 flock -n "
                "/tmp/ripraptor-indexnow.lock .venv/bin/python manage.py indexnow >> /var/log/ripraptor-import.log 2>&1")
        deploy = Path(settings.BASE_DIR) / "deploy"
        self.assertIn(line.format(dir="$DIR"), (deploy / "install.sh").read_text())
        self.assertIn(line.format(dir="/srv/ripraptor"), (deploy / "crontab").read_text())


class SummaryTests(TestCase):
    def setUp(self):
        self.box = make_product(make_set(make_game()), name="Surging Sparks Booster Box", product_type="booster_box",
                                packs_found=36)
        make_listing(self.box, make_retailer("Harbour Games"), price="87.00")
        make_listing(self.box, make_retailer("Tollgate TCG"), price="90.00")

    def test_the_facts_as_plain_sentences_under_product_details(self):
        html = self.client.get(self.box.get_absolute_url()).content.decode()
        self.assertIn('<p class="details__summary">The cheapest Surging Sparks Booster Box we found right now is £87.00 '
                      'delivered at Harbour Games. 2 UK shops have it in stock. It holds 36 packs, £2.42 a pack.</p>', html)
        self.assertLess(html.index('id="details-title"'), html.index("details__summary"))


class HubAboutTests(TestCase):
    """Game and set pages end with their facts as sentences (the owner, 31 Oct 2026: searches for a whole game or set
    found RipRaptor on page 2), while the top keeps its one short line."""

    def setUp(self):
        self.game = make_game()
        self.set = make_set(self.game, name="Surging Sparks", slug="surging-sparks")
        self.box = make_product(self.set, name="Surging Sparks Booster Box", product_type="booster_box")
        self.etb = make_product(self.set, name="Surging Sparks Elite Trainer Box")
        make_listing(self.box, make_retailer("Harbour Games"), price="87.00")
        make_listing(self.etb, make_retailer("Tollgate TCG"), price="45.00")

    def about(self, url):
        html = self.client.get(url).content.decode()
        start = html.index('<section class="block hub-about"')
        return html, html[start:html.index("</section>", start)]

    def test_a_game_page_says_what_it_compares_and_its_numbers(self):
        html, about = self.about(self.game.get_absolute_url())
        self.assertIn(f'<h2 id="about-title" class="section-title">About {self.game.name}</h2>', about)
        self.assertIn(f"RipRaptor compares UK prices for sealed {self.game.name} products: booster boxes and ETBs.", about)
        self.assertIn("2 sealed products compared across 2 UK shops, 2 in stock now.", about)
        self.assertIn(f'Cheapest in stock: <a href="{self.etb.get_absolute_url()}">Surging Sparks Elite Trainer Box</a>, '
                      "£45.00 delivered at Tollgate TCG.", about)
        # At the foot: after the list, not under the heading.
        self.assertGreater(html.index("hub-about"), html.index("stock-pane"))

    def test_a_set_page_names_its_game_and_keeps_its_date_up_top(self):
        _html, about = self.about(self.set.get_absolute_url())
        self.assertIn("Surging Sparks is a ", about)
        self.assertIn(" set. 2 sealed products compared across 2 UK shops, 2 in stock now.", about)
        self.assertNotIn("released", about.lower())

    def test_a_tab_fetched_by_script_has_no_about(self):
        html = self.client.get(self.game.get_absolute_url() + "?pane=1&tab=preorder").content.decode()
        self.assertNotIn("hub-about", html)


class TitleLengthTests(TestCase):
    """Bing Webmaster Tools warns on a title over 70 characters (the owner, 31 Oct 2026: 23 pages, most only over by the
    " | RipRaptor" after a title fitted to 60). The site's name goes after a title only while the whole fits."""

    def test_the_site_name_goes_on_only_while_the_whole_title_fits(self):
        from web.templatetags.ripraptor import TITLE_MAX, with_site_name

        self.assertEqual(with_site_name("Your watchlist", "RipRaptor"), "Your watchlist | RipRaptor")
        fits = "x" * (TITLE_MAX - len(" | RipRaptor"))
        self.assertEqual(with_site_name(fits, "RipRaptor"), fits + " | RipRaptor")
        over = "Cheapest Pokémon booster boxes in stock: UK price comparison"
        self.assertEqual(with_site_name(over, "RipRaptor"), over)

    def test_pages_use_it(self):
        game = make_game()
        html = self.client.get(reverse("web:watchlist")).content.decode()
        self.assertIn("<title>Your watchlist | RipRaptor</title>", html)
        html = self.client.get(game.get_absolute_url()).content.decode()
        title = html[html.index("<title>") + 7:html.index("</title>")]
        self.assertLessEqual(len(title.replace("&#x27;", "'").replace("&amp;", "&")), 70)
