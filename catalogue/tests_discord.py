"""Posts to the owner's Discord: restocks, pre-orders opening, price drops and drop days, each once."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from . import discord
from .models import DailyLowestPrice, DiscordPost, DiscordSettings, Listing, PreorderOpen, Restock, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set

HOOK = "https://discord.example/api/webhooks/1/secret-token"
DROPS_HOOK = "https://discord.example/api/webhooks/2/other-token"


class Outbox:
    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    def __call__(self, url, data):
        if self.fail:
            raise self.fail
        self.sent.append((url, data))

    def embeds(self):
        return [embed for _url, data in self.sent for embed in data.get("embeds", [])]


@override_settings(RIPRAPTOR_DISCORD_WEBHOOK=HOOK, RIPRAPTOR_SITE_URL="https://ripraptor.com")
class DiscordTests(TestCase):
    def setUp(self):
        self.now = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
        self.game = make_game()
        self.set = make_set(self.game)
        self.shop = make_retailer("Harbour Games")

    def restock(self, name, minutes_ago=10, availability=Listing.Availability.IN_STOCK, model=Restock, retailer=None):
        product = make_product(self.set, name=name)
        listing = make_listing(product, retailer or self.shop, price="47.01", delivery="2.99", availability=availability)
        return model.objects.create(product=product, retailer=retailer or self.shop, listing=listing,
                                    at=self.now - timedelta(minutes=minutes_ago), price=Decimal("50.00"))

    def run_once(self, minutes_later=0, **kwargs):
        outbox = kwargs.pop("outbox", None) or Outbox()
        return discord.run(now=self.now + timedelta(minutes=minutes_later), post=outbox, **kwargs), outbox

    def test_nothing_is_posted_until_a_webhook_is_set(self):
        self.restock("Surging Sparks Booster Box")
        with self.settings(RIPRAPTOR_DISCORD_WEBHOOK=""):
            sent, outbox = self.run_once()
            self.assertEqual((sent, outbox.sent), ({}, []))
            out = StringIO()
            call_command("post_discord", stdout=out)
            self.assertIn("Discord is not set up", out.getvalue())

    def test_the_first_run_only_notes_what_is_there_then_each_restock_posts_once(self):
        self.restock("Surging Sparks Booster Box")
        sent, outbox = self.run_once()
        self.assertEqual(outbox.sent, [])
        self.assertEqual(sent["restock"], 0)
        self.assertTrue(DiscordPost.objects.filter(key="seeded-restock").exists())
        fresh = self.restock("Prismatic Evolutions Elite Trainer Box", minutes_ago=2)
        sent, outbox = self.run_once()
        self.assertEqual(sent["restock"], 1)
        [(url, data)] = outbox.sent
        self.assertEqual(url, HOOK)
        [embed] = data["embeds"]
        self.assertEqual(embed["title"], "Back in stock: Prismatic Evolutions Elite Trainer Box")
        self.assertEqual(embed["description"],
                         f"£50.00 delivered, checked at {timezone.localtime(fresh.at):%H:%M}. Compare every UK shop on RipRaptor.")
        self.assertEqual(embed["url"], f"https://ripraptor.com{fresh.product.get_absolute_url()}"
                                       "?utm_source=discord&utm_medium=social&utm_campaign=alerts")
        self.assertIn("commission", embed["footer"]["text"])
        # Never the shop: people compare every shop on the page.
        self.assertNotIn("Harbour", str(data))
        # A product's name can never ping the server.
        self.assertEqual(data["allowed_mentions"], {"parse": []})
        # The name the owner gave the webhook in Discord, with RipRaptor's logo.
        self.assertNotIn("username", data)
        self.assertTrue(data["avatar_url"].endswith("/static/img/apple-touch-icon.png"))
        # Posted once.
        self.assertEqual(self.run_once(minutes_later=10)[1].sent, [])

    def test_only_what_is_still_true_and_never_a_marketplace(self):
        self.run_once()   # switch on
        self.restock("Gone Again Booster Box", availability=Listing.Availability.OUT_OF_STOCK)
        self.restock("Old News Booster Box", minutes_ago=60 * 7)
        ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        self.restock("Marketplace Booster Box", model=PreorderOpen, retailer=ebay, availability=Listing.Availability.PREORDER)
        opening = self.restock("Destined Rivals Booster Box", model=PreorderOpen, availability=Listing.Availability.PREORDER)
        sent, outbox = self.run_once()
        self.assertEqual([e["title"] for e in outbox.embeds()], [f"Pre-orders open: {opening.product.name}"])
        self.assertEqual(sent, {"restock": 0, "preorder": 1, "price_drop": 0, "drop_day": 0})

    def test_a_busy_hour_posts_a_few_a_run_and_the_rest_next_time(self):
        self.run_once()
        per_post = DiscordSettings.current().per_post
        for n in range(per_post + 2):
            self.restock(f"Booster Box {n}", minutes_ago=n + 1)
        first = self.run_once()[1]
        self.assertEqual(len(first.embeds()), per_post)
        self.assertEqual(len(first.sent), 1)   # one message
        # The rest at the next run, ten minutes on (the default how often).
        self.assertEqual(self.run_once(minutes_later=5)[1].sent, [])
        second = self.run_once(minutes_later=10)[1]
        self.assertEqual(len(second.embeds()), 2)

    def test_too_many_posts_stops_the_run_and_nothing_is_lost(self):
        self.run_once()
        self.restock("Surging Sparks Booster Box")
        busy = Outbox(fail=discord.DiscordError("Discord answered 429", 429))
        sent, _ = self.run_once(outbox=busy)
        self.assertNotIn("restock", sent)
        self.assertFalse(DiscordPost.objects.filter(key__startswith="restock-").exists())
        self.assertEqual(len(self.run_once()[1].embeds()), 1)

    def test_a_price_drop_of_five_percent_or_more_is_posted_once_a_day(self):
        self.run_once()
        today = timezone.localdate(self.now)
        dropped = make_product(self.set, name="Journey Together Booster Box")
        make_listing(dropped, self.shop, price="47.01", delivery="2.99")
        DailyLowestPrice.objects.create(product=dropped, date=today - timedelta(days=1), price="60.00")
        small = make_product(self.set, name="Black Bolt Booster Box")
        make_listing(small, self.shop, price="58.01", delivery="2.99")
        DailyLowestPrice.objects.create(product=small, date=today - timedelta(days=1), price="62.00")
        outbox = self.run_once()[1]
        [embed] = outbox.embeds()
        self.assertEqual(embed["title"], "Price drop: Journey Together Booster Box")
        self.assertTrue(embed["description"].startswith("Now £50.00 delivered, was £60.00 yesterday. Checked at "))
        self.assertEqual(self.run_once(minutes_later=10)[1].sent, [])

    @override_settings(RIPRAPTOR_DISCORD_WEBHOOK_DROPS=DROPS_HOOK)
    def test_a_set_out_today_goes_to_its_own_channel_from_eight(self):
        today = timezone.localdate(self.now)
        out = make_set(self.game, name="Mega Evolution", slug="mega-evolution", code="MEG", release_date=today)
        box = make_product(out, name="Mega Evolution Booster Box")
        make_listing(box, self.shop, price="147.01", delivery="2.99")
        early = self.now.replace(hour=7)
        discord.run(now=early - timedelta(minutes=30), post=Outbox())   # switch on, before anything is due
        before = Outbox()
        discord.run(now=early, post=before)
        self.assertEqual(before.sent, [])
        after = Outbox()
        discord.run(now=self.now, post=after)
        [(url, data)] = after.sent
        self.assertEqual(url, DROPS_HOOK)
        [embed] = data["embeds"]
        self.assertEqual(embed["title"], "Out today: Mega Evolution (Pokémon)")
        self.assertTrue(embed["description"].startswith("Cheapest from £150.00 delivered, checked at "))
        self.assertEqual(embed["url"], f"https://ripraptor.com{out.get_absolute_url()}"
                                       "?utm_source=discord&utm_medium=social&utm_campaign=alerts")

    def test_the_test_command_says_hello_to_each_channel_once_and_never_prints_the_address(self):
        with override_settings(RIPRAPTOR_DISCORD_WEBHOOK_DROPS=DROPS_HOOK), mock.patch.object(discord, "send") as send:
            out = StringIO()
            call_command("post_discord", "--test", stdout=out)
        self.assertEqual([call.args[0] for call in send.call_args_list], [HOOK, DROPS_HOOK])
        self.assertIn("RipRaptor is connected", send.call_args_list[0].args[1]["content"])
        self.assertEqual(out.getvalue().strip(), "Hello posted to 2 Discord channels.")
        with mock.patch.object(discord, "send", side_effect=discord.DiscordError("Discord answered 404", 404)):
            out = StringIO()
            call_command("post_discord", "--test", stdout=out)
        self.assertIn("Not posted: Discord answered 404", out.getvalue())
        self.assertNotIn("secret-token", out.getvalue())

    def test_the_owner_chooses_kinds_how_often_how_many_games_and_the_name(self):
        controls = DiscordSettings.load()
        controls.restocks, controls.every_minutes, controls.per_post = False, 60, 2
        controls.name, controls.use_logo = "Raptor News", False
        controls.save()
        other = make_game(name="One Piece Card Game", slug="one-piece", short_name="One Piece")
        controls.games.set([self.game])
        self.run_once()   # switch on
        self.restock("Off Kind Booster Box")
        for n in range(3):
            self.restock(f"Opening {n}", minutes_ago=n + 1, model=PreorderOpen, availability=Listing.Availability.PREORDER)
        elsewhere = make_product(make_set(other, name="OP-15", slug="op-15"), name="OP-15 Booster Box")
        listing = make_listing(elsewhere, self.shop, availability=Listing.Availability.PREORDER)
        PreorderOpen.objects.create(product=elsewhere, retailer=self.shop, listing=listing, at=self.now, price="99.00")
        sent, outbox = self.run_once()
        self.assertNotIn("restock", sent)
        [(_url, data)] = outbox.sent
        self.assertEqual([e["title"] for e in data["embeds"]], ["Pre-orders open: Opening 0", "Pre-orders open: Opening 1"])
        self.assertEqual(data["username"], "Raptor News")
        self.assertNotIn("avatar_url", data)
        # Not again until an hour has passed: then the one left over goes.
        self.assertEqual(self.run_once()[1].sent, [])
        later = Outbox()
        discord.run(now=self.now + timedelta(minutes=58), post=later)
        self.assertEqual([e["title"] for e in later.embeds()], ["Pre-orders open: Opening 2"])

    def test_the_smallest_price_drop_is_the_owners(self):
        controls = DiscordSettings.load()
        controls.min_drop_percent = 20
        controls.save()
        self.run_once()
        today = timezone.localdate(self.now)
        product = make_product(self.set, name="Journey Together Booster Box")
        make_listing(product, self.shop, price="47.01", delivery="2.99")
        DailyLowestPrice.objects.create(product=product, date=today - timedelta(days=1), price="60.00")   # 16.7%
        self.assertEqual(self.run_once()[1].sent, [])

    def test_the_settings_page_opens_on_its_one_row_and_sends_a_test_post(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))
        page = self.client.get(reverse("admin:catalogue_discordsettings_changelist"))
        self.assertRedirects(page, reverse("admin:catalogue_discordsettings_change", args=[1]))
        form = self.client.get(reverse("admin:catalogue_discordsettings_change", args=[1]))
        self.assertContains(form, "Every 10 minutes")
        self.assertContains(form, "Send a test post")
        with mock.patch.object(discord, "send") as send:
            self.client.post(reverse("admin:catalogue_discordsettings_change", args=[1]), {
                "restocks": "on", "preorders": "on", "price_drops": "on", "drop_days": "on", "every_minutes": "30",
                "per_post": "5", "min_drop_percent": "10", "name": "Raptor News", "use_logo": "on", "send_test": "on",
            })
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[1]["username"], "Raptor News")
        controls = DiscordSettings.load()
        self.assertEqual((controls.every_minutes, controls.per_post, controls.min_drop_percent), (30, 5, 10))

    def test_the_footer_links_to_the_discord_server(self):
        page = self.client.get("/")
        self.assertContains(page, 'href="https://discord.gg/5YKc77s4d7"')
        self.assertContains(page, "RipRaptor on Discord (opens in a new tab)")


class SendTests(TestCase):
    def test_a_post_waits_for_discord_and_an_error_never_names_the_address(self):
        import json
        import urllib.error

        seen = {}

        class Answer:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"{}"

        def opened(request, timeout):
            seen.update(url=request.full_url, body=json.loads(request.data), method=request.get_method(),
                        agent=request.get_header("User-agent"), timeout=timeout)
            return Answer()

        with mock.patch("urllib.request.urlopen", opened):
            discord.send(HOOK, {"content": "hello"})
        self.assertEqual(seen["url"], HOOK + "?wait=true")
        self.assertEqual((seen["method"], seen["body"]), ("POST", {"content": "hello"}))
        self.assertTrue(seen["agent"].startswith("RipRaptor"))

        def refused(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 404, "Unknown Webhook", {}, None)

        with mock.patch("urllib.request.urlopen", refused), self.assertRaises(discord.DiscordError) as caught:
            discord.send(HOOK, {"content": "hello"})
        self.assertEqual(caught.exception.status, 404)
        self.assertNotIn("secret-token", str(caught.exception))
