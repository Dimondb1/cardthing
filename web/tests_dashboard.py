"""The owner's dashboard (/dashboard/, web/dashboard.py): staff only, private, every figure from the functions the
admin pages use, a bounded number of queries, and the house style."""

import re
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse

from catalogue import checks, clicks, judge, traffic
from catalogue.models import (
    CheckAnswer, ClaudeAsk, ClaudeJudge, DailyPageView, DailySearch, DropAlert, KeyringClaim, Listing, OutboundClick,
    Referral, ReferralSettings, Retailer, SearchAlert, StockAlert, WorkerState,
)
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from catalogue.tests_traffic import add_visitors
from content.registry import REGISTRY
from inbox.models import Conversation, ShopEnquiry
from web import dashboard

LONDON = ZoneInfo("Europe/London")
# Sunday 18 Oct 2026 at 13:13, as in the traffic explainer's tests.
NOW = datetime(2026, 10, 18, 13, 13, tzinfo=LONDON)
TODAY = NOW.date()
URL = "/dashboard/"


def at(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=LONDON)


def visible(html):
    """The words a reader sees: no tags, scripts, styles or the SVG's own markup."""
    html = re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", html)
    return re.sub(r"<[^>]*>", " ", html)


class DashboardCase(TestCase):
    """A signed-in staff user, the clock stopped at NOW and an empty shared cache."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        clock = mock.patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.staff = get_user_model().objects.create_user("owner", password="pw", is_staff=True)
        self.client.force_login(self.staff)

    def page(self, url=URL):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response


def build_fixtures(self):
    """A week of people, clicks, searches, alerts, shops and Claude, as the live site records them."""
    game = make_game()
    product_set = make_set(game)
    self.box = make_product(product_set, name="Prismatic Evolutions Booster Box", product_type="booster_box")
    self.etb = make_product(product_set)
    self.tin = make_product(product_set, name="Prismatic Evolutions Mini Tin", product_type="tin")
    # How each shop earns: an Awin link, a CJ link, eBay's own programme, and none.
    self.awin = make_retailer("Harbour Games", affiliate_url_template="https://www.awin1.com/cread.php?awinmid=1&ued={url}",
                              source_type=Retailer.Source.SHOPIFY, last_ok_at=NOW - timedelta(minutes=10))
    self.cj = make_retailer("Northgate Cards", affiliate_url_template="https://www.anrdoezrs.net/click-1-2?url={url}",
                            source_type=Retailer.Source.SHOPIFY, last_ok_at=NOW - timedelta(hours=3), read_every_minutes=180)
    self.ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY, last_ok_at=NOW - timedelta(hours=8))
    self.plain = make_retailer("Kestrel Collectables", source_type=Retailer.Source.WEBSITE, error_streak=2,
                               last_error="HTTP Error 503: Service Unavailable", last_ok_at=NOW - timedelta(hours=2),
                               backoff_until=NOW + timedelta(minutes=30))
    self.paused = make_retailer("Tollgate TCG", source_type=Retailer.Source.SHOPIFY, reading_paused=True)
    make_listing(self.etb, self.awin, price="52.00", delivery="2.99")
    make_listing(self.etb, self.cj, price="58.00")
    # A doubtful price that shows as the cheapest, and a shop whose delivery charge is not known.
    make_listing(self.box, self.awin, price="20.00", sanity=Listing.Sanity.DOUBTFUL)
    make_listing(self.box, self.cj, price="140.00", delivery_known=False)
    WorkerState.objects.create(pk=1, heartbeat_at=NOW - timedelta(seconds=40), started_at=NOW - timedelta(hours=3))

    # People: four normal weeks, a burst on Wednesday, and today so far.
    for back in range(1, 32):
        day = TODAY - timedelta(days=back)
        add_visitors(day, 100, views=2, source="google.com")
        add_visitors(day, 20, views=2, source="reddit.com", returning=True)
    self.burst_day = TODAY - timedelta(days=4)
    add_visitors(self.burst_day, 20, views=150, source="")      # at the cap: scripts, never people
    add_visitors(self.burst_day, 15, views=100, source="")      # under the cap: people who read like scripts
    add_visitors(TODAY, 30, views=2, source="google.com", hours=[8, 9, 10, 11, 12])
    add_visitors(TODAY, 10, views=2, source="", returning=True, hours=[9, 10])

    # Clicks from people at each kind of shop, and direct hits that must count nowhere else.
    made = []
    for back in range(7):
        day = TODAY - timedelta(days=back)
        hour = 11 if back == 0 else 15
        made += [OutboundClick(product=self.etb, retailer=self.awin, created_at=at(day, hour)) for _ in range(4)]
        made += [OutboundClick(product=self.box, retailer=self.cj, created_at=at(day, hour))]
        made += [OutboundClick(product=self.tin, retailer=self.ebay, created_at=at(day, hour)) for _ in range(2)]
        made += [OutboundClick(product=self.box, retailer=self.plain, created_at=at(day, hour))]
        made += [OutboundClick(product=self.tin, retailer=self.plain, created_at=at(day, hour), suspect=True)
                 for _ in range(9)]
    OutboundClick.objects.bulk_create(made)

    # Searches this week, one found plenty, one before the week.
    for back, query, hits, results in ((0, "riftbound origins", 5, 0), (2, "riftbound origins", 3, 0),
                                       (1, "ascended heroes", 4, 2), (1, "prismatic", 20, 14), (9, "old search", 50, 0)):
        DailySearch.objects.create(date=TODAY - timedelta(days=back), query=query, hits=hits, results=results)

    # Customers.
    DropAlert.objects.create(product_set=product_set, email="a@example.com", confirmed_at=NOW)
    DropAlert.objects.create(product_set=product_set, email="b@example.com", confirmed_at=NOW)
    DropAlert.objects.create(product_set=product_set, email="c@example.com")
    SearchAlert.objects.create(email="d@example.com", game=game, confirmed_at=NOW)
    StockAlert.objects.create(product=self.box, email="e@example.com", confirmed_at=NOW)
    for back, key, hits in ((0, "sent", 2), (3, "drop_sent_day", 3), (5, "search_sent", 1), (10, "sent", 40),
                            (1, "asked", 9)):
        DailyPageView.objects.create(date=TODAY - timedelta(days=back), kind=DailyPageView.Kind.ALERTS, key=key, hits=hits)
    Conversation.objects.create(name="Sam", unread=True)
    ShopEnquiry.objects.create(name="Shop", shop_url="https://shop.example", email="shop@example.com")
    ReferralSettings.objects.create(pk=1, is_on=True, keyrings_left=12)
    held = Referral.objects.create(friend_key="held", status=Referral.Status.REVIEW)
    done = Referral.objects.create(friend_key="done", status=Referral.Status.COMPLETE)
    KeyringClaim.objects.create(referral=done, role=KeyringClaim.Role.FRIEND, submitted_at=NOW, same_postcode=True)
    KeyringClaim.objects.create(referral=held, role=KeyringClaim.Role.REFERRER)

    # Claude: $2.50 this month, one act today and one earlier this month.
    for hours, cost in ((2, 1_500_000), (100, 1_000_000)):
        ask = ClaudeAsk.objects.create(kind="offer", row_key=f"offer:{hours}", fingerprint="f", model_asked="m",
                                       effort="medium", outcome="answered", action="acted", cost_micros=cost,
                                       asked_at=NOW - timedelta(hours=hours))
        CheckAnswer.objects.create(kind=CheckAnswer.Kind.CHECKED, what="Checked", ask=ask,
                                   created_at=NOW - timedelta(hours=hours))
    ClaudeJudge.objects.create(pk=1, enabled=True, may_act=True, last_run_at=NOW - timedelta(minutes=20))


class AccessTests(DashboardCase):
    def test_anonymous_visitors_go_to_the_admin_sign_in(self):
        self.client.logout()
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{reverse('admin:login')}?next={URL}")

    def test_a_signed_in_visitor_who_is_not_staff_is_refused(self):
        self.client.force_login(get_user_model().objects.create_user("visitor", password="pw"))
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(reverse("admin:login")))

    def test_staff_see_it(self):
        self.assertContains(self.page(), "Dashboard")
        self.assertEqual(reverse("web:dashboard"), URL)

    def test_only_get(self):
        self.assertEqual(self.client.post(URL).status_code, 405)


class PrivacyTests(DashboardCase):
    def test_never_indexed_or_kept_by_a_browser_or_proxy(self):
        response = self.page()
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertContains(response, '<meta name="robots" content="noindex, nofollow">')
        self.assertNotContains(response, '<meta name="robots" content="noindex, follow">')
        # Leaving the page passes on only the site's address.
        self.assertContains(response, '<meta name="referrer" content="strict-origin">')

    def test_robots_txt_keeps_it_out_for_every_crawler(self):
        body = self.client.get("/robots.txt").content.decode()
        sections = body.split("User-agent: ")[1:]
        self.assertTrue(sections)
        for section in sections:
            if section.startswith("*") or "Allow: /" in section:
                self.assertIn("Disallow: /dashboard/", section)

    @override_settings(RIPRAPTOR_GA_ID="G-TEST123", RIPRAPTOR_ADSENSE_CLIENT="ca-pub-1", RIPRAPTOR_AWIN_PUBLISHER_ID="999")
    def test_no_analytics_or_other_third_party_script(self):
        html = self.page().content.decode()
        for marker in ("analytics.js", "G-TEST123", "data-consent", "googlesyndication", "dwin2.com", "googletagmanager"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, html)
        # A public page still carries them, so the switch is the address and not the settings.
        self.assertIn("analytics.js", self.client.get(reverse("web:about")).content.decode())

    def test_the_analytics_script_knows_it_is_private(self):
        from django.contrib.staticfiles import finders

        from web.context_processors import NO_REFER_CARD, PRIVATE_LINK

        self.assertTrue(PRIVATE_LINK.match(URL))
        self.assertTrue(NO_REFER_CARD.match(URL))
        script = open(finders.find("js/analytics.js"), encoding="utf-8").read()
        self.assertIn(PRIVATE_LINK.pattern.replace("/", r"\/"), script)

    def test_visits_are_not_counted_as_page_views(self):
        self.page()
        self.assertFalse(DailyPageView.objects.exclude(kind=DailyPageView.Kind.ALERTS).exists())

    def test_the_admin_home_and_insights_link_to_it(self):
        self.staff.is_superuser = True
        self.staff.save()
        self.assertContains(self.client.get(reverse("admin:index")), f'href="{URL}"')
        self.assertContains(self.client.get(reverse("insights")), f'href="{URL}"')


class EmptyTests(DashboardCase):
    def test_every_part_shows_with_nothing_recorded(self):
        response = self.page()
        for heading in ("Today at a glance", "Visitors, last 7 days", "Clicks to shops, last 7 days", "Things to correct",
                        "Shops health", "Customers", "Searches that found little", "Claude"):
            with self.subTest(heading=heading):
                self.assertContains(response, heading)
        html = response.content.decode()
        # Visitors, clicks, shops and searches have no rows: each says so rather than drawing an empty chart.
        self.assertEqual(html.count("No data yet."), 4)
        self.assertNotIn("<svg class=\"dash-chart__plot\"", html)
        self.assertContains(response, "No earlier day to set it against yet.")
        self.assertContains(response, "Nothing waiting for you.")
        self.assertContains(response, "$0.00")
        self.assertContains(response, "The background reader has not run here.")
        self.assertContains(response, "Claude has not run yet.")
        self.assertContains(response, "The offer is switched off.")

    def test_the_figures_are_noughts(self):
        context = self.page().context
        self.assertEqual(context["traffic"]["today"]["people"], 0)
        self.assertEqual(context["traffic"]["people"], 0)
        self.assertEqual(context["checks"]["total"], 0)
        self.assertEqual(context["customers"]["sent"], 0)
        self.assertEqual(context["gaps"]["searches"], [])
        self.assertEqual(context["shops"]["total"], 0)


class FigureTests(DashboardCase):
    """Each figure is the one the underlying function gives for the same rows."""

    def setUp(self):
        super().setUp()
        build_fixtures(self)

    def test_people_match_the_traffic_explainer_with_bursts_left_out(self):
        context = self.page().context
        why = traffic.explain(NOW)
        self.assertIn(self.burst_day, why["burst_dates"])
        week = [TODAY - timedelta(days=back) for back in range(6, -1, -1)]
        self.assertEqual([day["date"] for day in context["traffic"]["days"]], week)
        for day in context["traffic"]["days"]:
            row = why["days"][day["date"]]
            with self.subTest(day=day["date"]):
                self.assertEqual(day["people"], row["people"])
                self.assertEqual(day["views"], row["views"])
                self.assertEqual(day["clicks"], row["clicks"])
                self.assertEqual(day["burst"], day["date"] == self.burst_day)
        counted = [why["days"][day] for day in week if day != self.burst_day]
        self.assertEqual(context["traffic"]["people"], sum(row["people"] for row in counted))
        self.assertEqual(context["traffic"]["returning"], 5 * 20 + 10)
        self.assertEqual(context["traffic"]["bursts"], [self.burst_day])
        # The scripts at the cap are never people, even on the burst day.
        self.assertEqual(why["days"][self.burst_day]["people"], 120 + 15)

    def test_today_is_set_against_the_same_time_last_week(self):
        context = self.page().context
        today = context["traffic"]["today"]
        so_far = traffic.explain(NOW)["today_so_far"]
        self.assertEqual(today["people"], so_far["now"])
        self.assertEqual(today["people"], 40)
        reference = so_far["references"][0]
        self.assertEqual((today["then"], today["basis"]), (reference["then"], reference["basis"]))
        self.assertEqual(today["clicks"], 8)
        self.assertEqual(today["then_clicks"], reference["then_clicks"])
        self.assertContains(self.page(), f"Against {reference['then']} {reference['basis']}.")

    def test_the_burst_is_labelled_and_left_out(self):
        html = self.page().content.decode()
        self.assertIn("dash-bar--burst", html)
        self.assertIn(f"left out of the totals: {self.burst_day:%a %-d %b}", html)

    def test_clicks_are_people_only_and_direct_hits_apart(self):
        context = self.page().context
        data = context["traffic"]
        people = OutboundClick.objects.people().filter(created_at__gte=at(TODAY - timedelta(days=6), 0))
        self.assertEqual(data["clicks"], people.count())
        self.assertEqual(data["clicks"], 7 * 8)
        self.assertEqual(data["direct"], 7 * 9)
        self.assertEqual([day["direct"] for day in data["click_days"]], [9] * 7)
        # Clicks per person sets the clicks of the days counted against their people: the burst day is left out.
        self.assertEqual(data["per_person"], round((data["clicks"] - 8) / data["people"], 2))
        # Grouped by how the shop pays, from its price source and its affiliate link format.
        earning = {row["key"]: (row["clicks"], [shop["name"] for shop in row["shops"]]) for row in data["earning"]}
        self.assertEqual(earning, {
            "awin": (28, ["Harbour Games"]), "cj": (7, ["Northgate Cards"]), "ebay": (14, ["eBay"]),
            "direct": (7, ["Kestrel Collectables"]),
        })
        self.assertEqual([row["key"] for row in data["earning"]], ["ebay", "awin", "cj", "direct"])
        # Direct hits on Kestrel's tin were nine a day: none of them count for the shop or the product.
        products = [(row["name"], row["clicks"]) for row in data["products"]]
        self.assertEqual(products, [(self.etb.name, 28), (self.box.name, 14), (self.tin.name, 14)])

    def test_things_to_correct_match_things_to_check(self):
        checks.note_duplicate_counts([], NOW)
        context = self.page().context
        waiting = checks.waiting_counts(NOW)
        sections = {row["key"]: row["count"] for row in context["checks"]["sections"]}
        self.assertEqual(sections["doubtful"], waiting["doubtful"])
        self.assertEqual(sections["doubtful"], checks.doubtful_count())
        self.assertEqual(sections["doubtful"], 1)
        self.assertEqual(sections["unknown_delivery"], len(checks.unknown_delivery_shops()))
        self.assertEqual(sections["unknown_delivery"], 1)
        self.assertEqual(sections["wrong"], len(checks.wrong_matches()))
        self.assertEqual(sections["found"], checks.found_waiting().count())
        self.assertEqual(context["checks"]["total"], sum(sections.values()))
        # Claude settled one row in the last day.
        self.assertEqual(context["claude"]["settled_day"], 1)
        self.assertContains(self.page(), "Claude settled 1 row in the last 24 hours.")

    def test_possible_duplicates_come_from_the_kept_count_with_its_time(self):
        from catalogue.management.commands.merge_duplicates import CERTAIN, LIKELY

        keep, other, third = self.etb, self.box, self.tin
        other.certainty, third.certainty = CERTAIN, LIKELY
        checks.note_duplicate_counts([(keep, [other, third])], NOW - timedelta(minutes=30))
        with mock.patch("catalogue.checks.duplicates", side_effect=AssertionError("read the catalogue")):
            response = self.page()
        self.assertEqual({row["key"]: row["count"] for row in response.context["checks"]["sections"]}["duplicates"], 2)
        self.assertContains(response, "Certain 1, Likely 1, Pairs counted at 12:43.")

    def test_possible_duplicates_are_counted_when_no_count_is_kept(self):
        self.assertIsNone(cache.get(checks.DUPLICATE_COUNTS_KEY))
        self.page()
        self.assertEqual(cache.get(checks.DUPLICATE_COUNTS_KEY)["pairs"], sum(len(o) for _k, o in checks.duplicates()))

    def test_things_to_check_keeps_the_duplicate_count_for_the_dashboard(self):
        self.staff.is_superuser = True
        self.staff.save()
        self.assertEqual(self.client.get(reverse("checks")).status_code, 200)
        self.assertEqual(cache.get(checks.DUPLICATE_COUNTS_KEY)["counted_at"], NOW)

    def test_shops_health(self):
        shops = self.page().context["shops"]
        counts = {segment["key"]: segment["count"] for segment in shops["segments"]}
        # Harbour read 10 minutes ago; Northgate 3 hours ago on a 3 hour schedule; eBay 8 hours ago, overdue; Kestrel
        # is backing off after errors; Tollgate is paused.
        self.assertEqual(counts, {"fresh": 1, "ok": 1, "paused": 1, "late": 2})
        self.assertEqual(shops["total"], 5)
        self.assertEqual([(row["name"], row["failing"]) for row in shops["problems"]],
                         [("Kestrel Collectables", True), ("eBay", False)])
        self.assertTrue(shops["worker"]["alive"])
        html = self.page().content.decode()
        self.assertIn(reverse("admin:catalogue_retailer_change", args=[self.plain.pk]), html)
        self.assertIn("HTTP Error 503: Service Unavailable", html)
        self.assertIn("1 of 5 shops read in the last hour.", html)

    def test_a_stopped_reader_is_said(self):
        WorkerState.objects.filter(pk=1).update(heartbeat_at=NOW - timedelta(minutes=45))
        self.assertContains(self.page(), "The background reader stopped at 12:28.")

    def test_customers(self):
        customers = self.page().context["customers"]
        self.assertEqual((customers["drops"], customers["searches"], customers["stock"]), (2, 1, 1))
        # Emails sent in the last seven days only: back in stock, drop reminders and search alerts.
        self.assertEqual(customers["sent"], 2 + 3 + 1)
        self.assertEqual((customers["unread"], customers["shops"]), (1, 1))
        self.assertEqual(customers["refer"]["keyrings"], 12)
        self.assertEqual((customers["refer"]["to_post"], customers["refer"]["check"], customers["refer"]["review"]),
                         (1, 1, 1))

    def test_searches_that_found_little(self):
        searches = self.page().context["gaps"]["searches"]
        self.assertEqual([(row["query"], row["hits"], row["results"]) for row in searches],
                         [("riftbound origins", 8, 0), ("ascended heroes", 4, 2)])
        html = self.page().content.decode()
        self.assertIn("Searched 8 times, found nothing", html)
        self.assertIn("Searched 4 times, found 2 products", html)
        self.assertIn(f"{reverse('web:search')}?q=riftbound%20origins", html)

    def test_claude_spend_against_its_limit(self):
        state = ClaudeJudge.current()
        claude = self.page().context["claude"]
        self.assertEqual(claude["spent"], judge.dollars(judge.spent_this_month(NOW)))
        self.assertEqual(claude["spent"], "$2.50")
        self.assertEqual(claude["limit"], judge.dollars(judge.monthly_limit_micros(state)))
        self.assertEqual((claude["settled_month"], claude["settled_day"]), (2, 1))
        self.assertEqual(claude["last_run"], "12:53")
        self.assertContains(self.page(), f"$2.50 of {claude['limit']} this month.")

    def test_cardmarket_matching_for_each_game_and_its_last_run(self):
        from catalogue.models import CardmarketMatch, CardmarketPrice, CardmarketProduct

        pokemon = self.box.game
        # A game Cardmarket does not list, and one it lists whose products the morning read has not fetched.
        for name, slug in (("Weiss Schwarz", "weiss-schwarz"), ("Magic", "magic-the-gathering")):
            product = make_product(make_set(make_game(name, slug), slug=f"{slug}-set"), name=f"{name} Box", slug=f"{slug}-box")
            make_listing(product, self.awin, price="90.00")
        listed = CardmarketProduct.objects.create(pk=784949, game=pokemon, name="Prismatic Evolutions Booster Box",
                                                  category_id=53, category="Pokémon Display")
        CardmarketPrice.objects.create(product=listed, date=TODAY - timedelta(days=1), trend=Decimal("250.24"))
        CardmarketMatch.objects.create(product=self.box, cardmarket=listed, status=CardmarketMatch.Status.MATCHED)
        CardmarketMatch.objects.create(product=self.etb, status=CardmarketMatch.Status.CHECK)
        ClaudeJudge.objects.update_or_create(pk=1, defaults={
            "market_run_at": NOW - timedelta(minutes=20),
            "market_run_note": "Asked 12, matched 3, to check 4, none 5, spent $0.06. Reached the limit for one run.",
        })
        response = self.page()
        rows = {row["name"]: row for row in response.context["prices"]["games"]}
        self.assertEqual((rows["Pokémon"]["looked"], rows["Pokémon"]["matched"], rows["Pokémon"]["listed"],
                          rows["Pokémon"]["priced_on"]), (2, 1, 1, TODAY - timedelta(days=1)))
        self.assertContains(response, "Claude looked at 2 of 3, matched 1. Cardmarket lists 1, prices read 17 Oct.")
        self.assertContains(response, "Cardmarket does not list this game.")
        self.assertContains(response, "list for this game has not been read: nothing can be matched until it is.")
        self.assertContains(response, "Last run 12:53: Asked 12, matched 3, to check 4, none 5, spent $0.06. "
                                      "Reached the limit for one run.")
        self.assertEqual(response.context["prices"]["matching"]["last_run"], "12:53")
        # Before the matcher has run, and with the list read but no prices yet, each says so.
        CardmarketPrice.objects.all().delete()
        ClaudeJudge.objects.filter(pk=1).update(market_run_at=None, market_run_note="")
        cache.clear()
        response = self.page()
        self.assertContains(response, "Claude looked at 2 of 3, matched 1. Cardmarket lists 1, no prices read yet.")
        self.assertNotContains(response, "Last run 12:53:")
        self.assertEqual(response.context["prices"]["matching"]["last_run"], "")

    def test_every_link_into_admin_resolves_and_opens(self):
        self.staff.is_superuser = True
        self.staff.save()
        html = self.page().content.decode()
        for name, link in dashboard.admin_links().items():
            with self.subTest(link=name):
                resolve(urlsplit(link).path)
                self.assertIn(f'href="{link}"', html)
                # A filter the admin does not know sends it to ?e=1; each of these lists opens as asked.
                response = self.client.get(link)
                self.assertEqual(response.status_code, 302 if name == "refer" else 200)
        for href in set(re.findall(r'href="(/admin/catalogue/retailer/\d+/change/)"', html)):
            with self.subTest(href=href):
                self.assertEqual(self.client.get(href).status_code, 200)


class StatusTests(DashboardCase):
    def test_every_claude_state_has_its_words(self):
        state = ClaudeJudge.load()
        for kind in ("server_off", "no_key", "stopped", "off", "paused", "limit", "sort_all", "acting", "trial"):
            with self.subTest(kind=kind):
                self.assertIn(f"dashboard.claude.{kind}", REGISTRY)
        self.assertEqual(judge.status_kind(state, NOW, has_key=False, spent=0, limit=100), "no_key"
                         if judge.settings.RIPRAPTOR_CLAUDE else "server_off")

    def test_things_to_check_keeps_its_own_words(self):
        with override_settings(RIPRAPTOR_CLAUDE=True), mock.patch.object(judge, "key", return_value=""):
            self.assertEqual(judge.page_status(NOW)["status"], "Claude needs a key. Paste it below and tap Save key.")
            self.assertContains(self.page(), "Claude needs a key. Add it under Claude settings and key on Things to check.")

    def test_every_shop_state_has_its_words(self):
        for key in ("fresh", "ok", "paused", "late"):
            self.assertIn(f"dashboard.shops.{key}", REGISTRY)


class NetworkTests(TestCase):
    def test_how_a_shop_earns(self):
        cases = [
            (Retailer.Source.EBAY, "", "ebay"),
            (Retailer.Source.AMAZON, "", "amazon"),
            (Retailer.Source.SHOPIFY, "https://www.awin1.com/cread.php?awinmid=1&ued={url}", "awin"),
            (Retailer.Source.FEED, "https://www.anrdoezrs.net/click-1-2?url={url}", "cj"),
            (Retailer.Source.FEED, "https://click.linksynergy.com/deeplink?id=x&murl={url}", "rakuten"),
            (Retailer.Source.SHOPIFY, "https://network.example/c?u={url}", "other"),
            (Retailer.Source.SHOPIFY, "", "direct"),
            (Retailer.Source.WEBSITE, "   ", "direct"),
        ]
        for source, template, network in cases:
            with self.subTest(template=template):
                self.assertEqual(dashboard.network_of(source, template), network)

    def test_every_network_the_click_code_knows_is_named(self):
        for domain in clicks.REDIRECTORS:
            with self.subTest(domain=domain):
                self.assertIn(dashboard.network_of(Retailer.Source.FEED, f"https://www.{domain}/c?u={{url}}"),
                              ("awin", "cj", "rakuten"))


class CacheTests(DashboardCase):
    def setUp(self):
        super().setUp()
        build_fixtures(self)
        checks.note_duplicate_counts([], NOW)

    def test_each_part_is_kept_for_a_minute_and_read_again_on_refresh(self):
        before = self.page().context["traffic"]["clicks"]
        OutboundClick.objects.create(product=self.etb, retailer=self.awin, created_at=NOW - timedelta(minutes=5))
        self.assertEqual(self.page().context["traffic"]["clicks"], before)
        self.assertEqual(self.page(URL + "?fresh=1").context["traffic"]["clicks"], before + 1)
        for name, _build in dashboard.SECTIONS:
            self.assertIsNotNone(cache.get(dashboard.SECTION_KEY.format(name=name)))
        self.assertEqual(dashboard.SECTION_SECONDS, 60)
        self.assertContains(self.page(), "Updated 13:13.")

    def test_queries_are_bounded_whatever_the_rows(self):
        with CaptureQueriesContext(connection) as few:
            self.page()
        cache.clear()
        checks.note_duplicate_counts([], NOW)
        # Many more people, clicks, shops, products and searches.
        more = [make_retailer(f"Shop {n}", source_type=Retailer.Source.SHOPIFY, last_ok_at=NOW) for n in range(6)]
        products = [make_product(make_set(make_game(f"Game {n}", f"game-{n}"), slug=f"set-{n}"), name=f"Box {n}")
                    for n in range(6)]
        OutboundClick.objects.bulk_create([
            OutboundClick(product=product, retailer=shop, created_at=NOW - timedelta(hours=n))
            for n, (product, shop) in enumerate(zip(products * 6, more * 6))
        ])
        for back in range(7):
            add_visitors(TODAY - timedelta(days=back), 50, views=3, source=f"site{back}.example", hours=[8, 9])
            DailySearch.objects.create(date=TODAY - timedelta(days=back), query=f"missing {back}", hits=2, results=0)
        with CaptureQueriesContext(connection) as many:
            self.page()
        self.assertEqual(len(few), len(many))
        # What clicks earn reads seven of them (dashboard.money_section), however many clicks, sales and offers,
        # and the Price check coverage seven, with Claude's Cardmarket matching (prices_section).
        # One more for a staff login that is not the owner: its groups are read, so a Guide editor is kept out
        # (guides.access). The owner's own login is a superuser and skips it.
        self.assertLessEqual(len(many), 61)
        # Within the minute the page reads its parts from the cache: the session, the user and the wording, and the
        # sign-in renews itself (SESSION_SAVE_EVERY_REQUEST: a transaction round one update), and this staff login's
        # groups (guides.access, above).
        with CaptureQueriesContext(connection) as warm:
            self.page()
        self.assertLessEqual(len(warm), 7)
        self.assertFalse([q for q in warm.captured_queries if "catalogue_" in q["sql"]])


class CopyTests(DashboardCase):
    def assert_house_style(self, html):
        from web.tests import CopyStyleTests

        text = visible(html)
        self.assertNotIn("!", text)
        self.assertNotIn("—", html)
        for phrase in CopyStyleTests.BANNED:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, html.lower())
        for mark in ("{#", "#}", "{%", "%}", "{{", "}}"):
            self.assertNotIn(mark, text)

    def test_empty_page(self):
        self.assert_house_style(self.page().content.decode())

    def test_full_page(self):
        build_fixtures(self)
        self.assert_house_style(self.page().content.decode())

    def test_the_wording_comes_from_the_registry(self):
        from content.models import SiteContent

        SiteContent.objects.filter(key="dashboard.visitors.title").update(content="People this week")
        self.assertContains(self.page(), "People this week")

    def test_the_chart_has_words_and_a_table(self):
        build_fixtures(self)
        html = self.page().content.decode()
        self.assertIn('role="img" aria-labelledby="visitors-chart"', html)
        self.assertIn("<title id=\"visitors-chart\">People each day, last 7 days</title>", html)
        self.assertIn("Most people on", html)
        self.assertEqual(html.count("Show the numbers"), 2)
        # Colour is never the only signal: up and down carry an arrow and words, burst days a label.
        self.assertIn('<span class="dash-chart__tag">burst</span>', html)
        self.assertIn('<span class="dash-chart__tag">so far</span>', html)


class ChartTests(TestCase):
    def test_bars_share_one_scale_and_leave_room_for_their_numbers(self):
        days = dashboard.bars([{"a": 0}, {"a": 5}, {"a": 10}], ["a"])
        heights = [day["bars"][0]["height"] for day in days]
        self.assertEqual(heights, [0, dashboard.BAR_REACH / 2, dashboard.BAR_REACH])
        self.assertEqual(days[2]["bars"][0]["y"], 100 - dashboard.BAR_REACH)
        # A small figure still shows.
        tiny = dashboard.bars([{"a": 1}, {"a": 1000}], ["a"])
        self.assertEqual(tiny[0]["bars"][0]["height"], dashboard.BAR_MIN)

    def test_a_pair_of_bars_sits_either_side_of_the_day(self):
        day = dashboard.bars([{"a": 4, "b": 2}], ["a", "b"])[0]
        first, second = day["bars"]
        self.assertAlmostEqual(first["x"] + first["width"], second["x"], places=2)
        self.assertEqual((first["nudge"], second["nudge"]), (-1, 1))
        self.assertEqual((day["band_x"], day["band"]), (0, 100))


class ExplainerTests(TestCase):
    def test_the_explainer_gives_each_days_people_and_returning(self):
        add_visitors(TODAY, 3, views=2, returning=True, hours=[9])
        add_visitors(TODAY, 2, views=200, returning=True, hours=[9])   # scripts at the cap
        row = traffic.explain(NOW)["days"][TODAY]
        self.assertEqual((row["people"], row["returning"], row["burst"]), (3, 3, False))


@override_settings(RIPRAPTOR_CLAUDE=True)
class SpendDecimalTests(DashboardCase):
    def test_at_the_limit_the_bar_turns_red_and_says_so(self):
        state = ClaudeJudge.load()
        state.enabled, state.monthly_budget_usd = True, Decimal("1.00")
        state.save()
        ClaudeAsk.objects.create(kind="offer", row_key="offer:1", fingerprint="f", model_asked="m", effort="medium",
                                 outcome="answered", cost_micros=1_200_000, asked_at=NOW - timedelta(hours=1))
        with mock.patch.object(judge, "key", return_value="sk-ant-test"):
            response = self.page()
        self.assertTrue(response.context["claude"]["at_limit"])
        self.assertEqual(response.context["claude"]["share"], 100)
        self.assertContains(response, "meter meter--full")
        self.assertContains(response, "Claude reached this month&#x27;s limit.")


class MoneyTests(DashboardCase):
    """What clicks earn: the five rates, what people press, and each game and shop (dashboard.money_section)."""

    def setUp(self):
        super().setUp()
        from catalogue.models import AffiliateSale, DailyVisitor

        self.pokemon = make_product(make_set(make_game()), slug="pre-etb")
        self.lorcana = make_product(make_set(make_game("Disney Lorcana", "lorcana"), name="The First Chapter",
                                             slug="tfc", code="TFC"), name="The First Chapter Booster Box", slug="tfc-box")
        self.harbour = make_retailer("Harbour Games", affiliate_url_template="https://aff.example/?u={url}")
        self.plain = make_retailer("Plain Cards")
        for product, shop in ((self.pokemon, self.harbour), (self.lorcana, self.harbour), (self.pokemon, self.plain)):
            make_listing(product, shop)
        # 20 visits today, 5 of them clicking through to a shop (one twice).
        DailyVisitor.objects.bulk_create([DailyVisitor(date=TODAY, token=f"v{n}", views=2) for n in range(20)])
        rows = [(self.pokemon, f"v{n % 5}", "buy_now") for n in range(6)] + [(self.lorcana, f"v{n}", "price_list")
                                                                          for n in range(4)]
        self.clicks = OutboundClick.objects.bulk_create([
            OutboundClick(product=product, retailer=self.harbour, visitor=visitor, button=button, source="product",
                          created_at=NOW - timedelta(hours=1)) for product, visitor, button in rows
        ])
        OutboundClick.objects.create(product=self.pokemon, retailer=self.harbour, suspect=True, created_at=NOW)
        DailyPageView.objects.create(date=TODAY, kind=DailyPageView.Kind.TAPS, key="buy_box:product", hits=5)
        sale = {"retailer": self.harbour, "ordered_on": TODAY - timedelta(days=1)}
        AffiliateSale.objects.create(click=self.clicks[6], commission=Decimal("3.00"), status="approved", **sale)
        AffiliateSale.objects.create(click=self.clicks[7], commission=Decimal("2.00"), status="approved", **sale)
        AffiliateSale.objects.create(click=self.clicks[0], commission=Decimal("1.50"), status="pending", **sale)
        AffiliateSale.objects.create(click=self.clicks[1], commission=Decimal("9.00"), status="declined", **sale)
        AffiliateSale.objects.create(commission=Decimal("4.00"), status="approved", retailer=self.harbour,
                                     ordered_on=TODAY - timedelta(days=40))   # before the 30 days

    def test_the_five_rates(self):
        money = self.page().context["money"]
        self.assertEqual((money["visits"], money["clicks"], money["sales"]), (20, 10, 3))
        self.assertEqual((money["clicked_visits"], money["tracked_visits"]), (5, 20))
        self.assertEqual(money["ctr"], Decimal("25"))                       # visits with a click / visits
        self.assertEqual(money["per_100"], Decimal("50"))                   # £5 approved / 10 clicks x 100
        self.assertEqual(money["conversion"], Decimal("30"))                # 3 sales / 10 clicks
        self.assertEqual(money["per_1000"], Decimal("250"))                 # £5 / 20 visits x 1,000
        self.assertEqual((money["earning_offers"], money["offers"]), (2, 3))
        self.assertAlmostEqual(float(money["coverage"]), 66.67, places=2)   # 2 of 3 in-stock offers earn
        self.assertEqual(money["pending"], Decimal("1.50"))

    def test_by_game_and_shop_and_what_people_press(self):
        money = self.page().context["money"]
        games = {row["name"]: row for row in money["games"]}
        self.assertEqual([row["name"] for row in money["games"]], ["Disney Lorcana", "Pokémon"])
        self.assertEqual((games["Disney Lorcana"]["commission"], games["Disney Lorcana"]["clicks"]), (Decimal("5.00"), 4))
        self.assertEqual(games["Disney Lorcana"]["per_100"], Decimal("125"))
        self.assertEqual(games["Pokémon"]["coverage"], Decimal("50"))      # Plain Cards' offer earns nothing
        self.assertEqual(money["best_game"]["name"], "Disney Lorcana")
        shops = {row["name"]: row for row in money["shops"]}
        self.assertEqual(shops["Plain Cards"]["coverage"], Decimal("0"))   # listed: stock whose links earn nothing
        self.assertEqual(shops["Harbour Games"]["sales"], 3)
        self.assertEqual([(item["name"], item["count"]) for item in money["pressed"]],
                         [("Buy now on a product page", 6), ("Compare prices", 5), ("A shop in a product's price list", 4)])
        html = visible(self.page().content.decode())
        for words in ("What clicks earn, last 30 days", "Retailer click-through rate", "25.0%", "5 of 20 visits since",
                      "Commission per 100 clicks", "£50.00", "Retailer conversion rate", "30.0%",
                      "Revenue per 1,000 visits", "£250.00", "Affiliate coverage", "66.7%", "2 of 3 in-stock offers earn",
                      "Disney Lorcana earns the most per 100 clicks: £125.00.", "£1.50 more is pending with the networks."):
            self.assertIn(words, html)

    def test_with_no_sales_the_page_says_how_to_fill_them_in(self):
        from catalogue.models import AffiliateSale

        AffiliateSale.objects.all().delete()
        html = visible(self.page(URL + "?fresh=1").content.decode())
        self.assertIn("No sales imported for these days", html)
        self.assertIn("Import a network report", html)
