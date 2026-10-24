"""Posts to the owner's Discord: restocks, pre-orders opening, price drops and drop days, to each channel set up
in admin for them, each once."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import discord
from .models import DailyLowestPrice, DiscordChannel, DiscordPost, Listing, PreorderOpen, Restock, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set

HOOK = "https://discord.com/api/webhooks/1/secret-token"
DROPS_HOOK = "https://discord.com/api/webhooks/2/other-token"
ALL = {"restocks": True, "preorders": True, "price_drops": True, "drop_days": True}


class Outbox:
    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    def __call__(self, url, data):
        if self.fail:
            raise self.fail
        self.sent.append((url, data))

    def embeds(self, url=None):
        return [embed for to, data in self.sent if url in (None, to) for embed in data.get("embeds", [])]

    def titles(self, url=None):
        """What each post says it is and what it is about: "Back in stock: <product>"."""
        return [f"{embed['author']['name']}: {embed['title']}" for embed in self.embeds(url)]


@override_settings(RIPRAPTOR_SITE_URL="https://ripraptor.com")
class DiscordTests(TestCase):
    def setUp(self):
        self.now = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
        self.game = make_game()
        self.set = make_set(self.game)
        self.shop = make_retailer("Harbour Games")

    def channel(self, label="#alerts", hook=HOOK, **fields):
        return DiscordChannel.objects.create(label=label, webhook_url=hook, **{**ALL, **fields})

    def restock(self, name, minutes_ago=10, availability=Listing.Availability.IN_STOCK, model=Restock, retailer=None,
                product_set=None):
        product = make_product(product_set or self.set, name=name)
        listing = make_listing(product, retailer or self.shop, price="47.01", delivery="2.99", availability=availability)
        return model.objects.create(product=product, retailer=retailer or self.shop, listing=listing,
                                    at=self.now - timedelta(minutes=minutes_ago), price=Decimal("50.00"))

    def run_once(self, minutes_later=0, outbox=None):
        outbox = outbox or Outbox()
        return discord.run(now=self.now + timedelta(minutes=minutes_later), post=outbox), outbox

    def test_a_restock_says_what_the_price_check_says_and_a_high_price_is_not_posted(self):
        self.channel()
        self.run_once()   # the first run only notes what is there
        today = timezone.localdate(self.now)
        for name, usual in (("Prismatic Evolutions Elite Trainer Box", "45.00"), ("Destined Rivals Elite Trainer Box", "42.00")):
            restock = self.restock(name, minutes_ago=2)
            make_listing(restock.product, make_retailer(f"Northgate {usual}"), price="60.00",
                         availability=Listing.Availability.OUT_OF_STOCK)
            DailyLowestPrice.objects.bulk_create([
                DailyLowestPrice(product=restock.product, date=today - timedelta(days=back), price=Decimal(usual))
                for back in range(1, 21)
            ])
        _sent, outbox = self.run_once()
        # £50.00 is 11% over a usual £45.00, which is above average, and 19% over a usual £42.00, which is high:
        # the owner wants nothing massively overpriced shared.
        [(_url, data)] = outbox.sent
        [embed] = data["embeds"]
        self.assertEqual(embed["title"], "Prismatic Evolutions Elite Trainer Box")
        fields = {f["name"]: f["value"] for f in embed["fields"]}
        self.assertEqual(fields["Price check"], "**Above average**\nAverage £45.00 over 20 days")

    def test_a_restock_says_how_long_it_was_gone_and_how_long_the_last_stock_lasted(self):
        # The owner: "I would like to know when they went out of stock and when they've been in stock". From
        # what we saw: never a count of units, which no shop we read gives.
        from . import pricing

        self.channel()
        self.run_once()
        product = make_product(self.set, name="Ascended Heroes Elite Trainer Box")
        listing = make_listing(product, self.shop, price="50.00", delivery="0.00")

        def check(availability, at):
            listing.refresh_from_db()
            pricing.record_check(listing, price=Decimal("50.00"), delivery_cost=Decimal("0.00"),
                                 availability=availability, checked_at=at)

        out, back = Listing.Availability.OUT_OF_STOCK, Listing.Availability.IN_STOCK
        check(out, self.now - timedelta(days=5))
        check(back, self.now - timedelta(days=4))                      # a restock: gone a day
        check(out, self.now - timedelta(days=4) + timedelta(hours=6))  # sold out again in 6 hours
        check(back, self.now - timedelta(minutes=10))                  # back now, after 3 days and 18 hours
        listing.refresh_from_db()
        self.assertEqual(listing.sold_out_at, self.now - timedelta(days=4) + timedelta(hours=6))
        restock = Restock.objects.filter(listing=listing).first()
        self.assertEqual(restock.out_since, self.now - timedelta(days=4) + timedelta(hours=6))
        make_listing(product, make_retailer("Northgate Cards"), price="55.00", availability=out)
        _sent, outbox = self.run_once()
        [embed] = outbox.embeds()
        fields = [(f["name"], f["value"]) for f in embed["fields"]]
        # Two shops list it and there are too few days for a Price check yet, so no Price check box.
        self.assertEqual(fields, [("UK shops with it", "1 of 2"), ("Out of stock for", "3 days"),
                                  ("Restocks in 30 days", "2"), ("Last stock lasted", "6 hours")])
        # Seen only coming back, never going: nothing said about how long it was gone.
        first = self.restock("Black Bolt Booster Box", minutes_ago=2)
        names = [f["name"] for f in self.run_once(minutes_later=10)[1].embeds()[0]["fields"]]
        self.assertNotIn("Out of stock for", names)
        self.assertNotIn("Last stock lasted", names)
        self.assertIsNone(first.out_since)

    def test_a_post_says_the_day_date_and_time_in_uk_time(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        # 23:30 on Friday in UTC is already Saturday in London in summer.
        self.assertEqual(discord.when_words(datetime(2026, 7, 3, 23, 30, tzinfo=ZoneInfo("UTC"))), "Sat 4 Jul, 00:30")
        self.assertEqual(discord.when_words(datetime(2026, 10, 24, 14, 5, tzinfo=ZoneInfo("Europe/London"))),
                         "Sat 24 Oct, 14:05")

    def test_how_long_reads_in_the_plainest_unit(self):
        self.assertEqual([discord.span_words(timedelta(minutes=n)) for n in (0, 1, 45, 60, 61 * 5, 47 * 60, 50 * 60)],
                         ["1 minute", "1 minute", "45 minutes", "1 hour", "5 hours", "47 hours", "2 days"])

    def test_nothing_is_posted_until_a_channel_is_set_up(self):
        self.restock("Surging Sparks Booster Box")
        sent, outbox = self.run_once()
        self.assertEqual((sent, outbox.sent), ({}, []))
        out = StringIO()
        call_command("post_discord", stdout=out)
        self.assertIn("No Discord channel is set up", out.getvalue())

    def test_a_first_run_only_notes_what_is_there_then_each_restock_posts_once(self):
        self.channel()
        self.restock("Surging Sparks Booster Box")
        sent, outbox = self.run_once()
        self.assertEqual((sent["#alerts"]["restock"], outbox.sent), (0, []))
        fresh = self.restock("Prismatic Evolutions Elite Trainer Box", minutes_ago=2)
        sent, outbox = self.run_once()
        self.assertEqual(sent["#alerts"]["restock"], 1)
        [(url, data)] = outbox.sent
        self.assertEqual(url, HOOK)
        [embed] = data["embeds"]
        # RipRaptor's look: what happened beside the logo, the product as the title, the price in bold.
        logo = "https://ripraptor.com/static/img/apple-touch-icon.png"
        page = (f"https://ripraptor.com{fresh.product.get_absolute_url()}"
                "?utm_source=discord&utm_medium=social&utm_campaign=alerts")
        self.assertEqual(embed["author"], {"name": "Back in stock", "url": page, "icon_url": logo})
        self.assertEqual(embed["title"], "Prismatic Evolutions Elite Trainer Box")
        self.assertEqual(embed["description"],
                         f"**£50.00** delivered, checked {discord.when_words(fresh.at)}.\n"
                         f"[Compare every UK shop on RipRaptor →]({page})")
        self.assertEqual(embed["url"], page)
        self.assertEqual(embed["color"], 0xE5470E)
        # Only one UK shop lists it here, so the Price check box says its price has nothing to be compared with,
        # and the shops box counts it in stock at that one.
        self.assertEqual(embed["fields"], [
            {"name": "Price check", "value": "Only one UK shop lists it, so nothing to compare with.", "inline": True},
            {"name": "UK shops with it", "value": "1 of 1", "inline": True},
        ])
        self.assertIn("commission", embed["footer"]["text"])
        self.assertEqual(embed["footer"]["icon_url"], logo)
        # Never the shop: people compare every shop on the page.
        self.assertNotIn("Harbour", str(data))
        # A product's name can never ping the server; the webhook's own name shows, with RipRaptor's logo.
        self.assertEqual(data["allowed_mentions"], {"parse": []})
        self.assertNotIn("username", data)
        self.assertTrue(data["avatar_url"].endswith("/static/img/apple-touch-icon.png"))
        # Posted once.
        self.assertEqual(self.run_once(minutes_later=10)[1].sent, [])

    def test_each_channel_gets_only_its_own_updates_and_games(self):
        stock = self.channel("#restocks", restocks=True, preorders=False, price_drops=False, drop_days=False)
        other = make_game(name="One Piece Card Game", slug="one-piece", short_name="One Piece")
        pokemon_only = self.channel("#pokemon-preorders", hook=DROPS_HOOK, restocks=False, price_drops=False,
                                    drop_days=False)
        pokemon_only.games.set([self.game])
        self.run_once()
        self.restock("Destined Rivals Booster Box", minutes_ago=3)
        self.restock("Black Bolt Booster Box", minutes_ago=2, model=PreorderOpen, availability=Listing.Availability.PREORDER)
        self.restock("OP-15 Booster Box", minutes_ago=1, model=PreorderOpen, availability=Listing.Availability.PREORDER,
                     product_set=make_set(other, name="OP-15", slug="op-15"))
        sent, outbox = self.run_once()
        self.assertEqual(outbox.titles(HOOK), ["Back in stock: Destined Rivals Booster Box"])
        self.assertEqual(outbox.titles(DROPS_HOOK), ["Pre-orders open: Black Bolt Booster Box"])
        self.assertEqual(set(sent), {stock.label, pokemon_only.label})
        # A channel added later starts quietly too: it never gets what the others already had.
        self.channel("#everything", hook="https://discord.com/api/webhooks/3/third")
        self.assertEqual(self.run_once(minutes_later=10)[1].titles("https://discord.com/api/webhooks/3/third"), [])

    def test_a_channel_for_one_set_posts_only_that_sets_news_and_the_set_can_be_swapped(self):
        # The owner: "a Pokémon 30th restock discord ... when 30th is no longer in most demand we can swap it".
        celebration = make_set(self.game, name="30th Celebration", slug="30th-celebration", code="CEL")
        heroes = make_set(self.game, name="Ascended Heroes", slug="ascended-heroes", code="ASC")
        channel = self.channel("#30th-restocks", restocks=True, preorders=False, price_drops=False, drop_days=False)
        channel.games.set([self.game])
        channel.sets.set([celebration])
        self.run_once()
        self.restock("30th Celebration Booster Bundle", minutes_ago=3, product_set=celebration)
        self.restock("Ascended Heroes Elite Trainer Box", minutes_ago=2, product_set=heroes)
        self.restock("Prismatic Evolutions Booster Box", minutes_ago=1)
        self.assertEqual(self.run_once()[1].titles(HOOK), ["Back in stock: 30th Celebration Booster Bundle"])
        # Swapped to another set: its restocks post from then on, those of the last hour still in stock too, and
        # 30th Celebration's no more.
        channel.sets.set([heroes])
        self.restock("30th Celebration Elite Trainer Box", minutes_ago=0, product_set=celebration)
        self.restock("Ascended Heroes Booster Bundle", minutes_ago=0, product_set=heroes)
        self.assertEqual(self.run_once(minutes_later=10)[1].titles(HOOK),
                         ["Back in stock: Ascended Heroes Booster Bundle",
                          "Back in stock: Ascended Heroes Elite Trainer Box"])

    def test_a_channel_switched_off_posts_nothing(self):
        self.channel(is_active=False)
        self.restock("Surging Sparks Booster Box")
        sent, outbox = self.run_once()
        self.assertEqual((sent, outbox.sent), ({}, []))
        self.assertFalse(DiscordPost.objects.exists())

    def test_only_what_is_still_true_and_never_a_marketplace(self):
        self.channel()
        self.run_once()
        self.restock("Gone Again Booster Box", availability=Listing.Availability.OUT_OF_STOCK)
        self.restock("Old News Booster Box", minutes_ago=60 * 7)
        ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        self.restock("Marketplace Booster Box", model=PreorderOpen, retailer=ebay, availability=Listing.Availability.PREORDER)
        opening = self.restock("Destined Rivals Booster Box", model=PreorderOpen, availability=Listing.Availability.PREORDER)
        sent, outbox = self.run_once()
        self.assertEqual(outbox.titles(), [f"Pre-orders open: {opening.product.name}"])
        self.assertEqual(sent["#alerts"], {"restock": 0, "preorder": 1, "price_drop": 0, "drop_day": 0})

    def test_a_busy_hour_posts_the_channels_limit_and_the_rest_next_time(self):
        self.channel(per_post=3)
        self.run_once()
        for n in range(5):
            self.restock(f"Booster Box {n}", minutes_ago=n + 1)
        first = self.run_once()[1]
        self.assertEqual((len(first.sent), len(first.embeds())), (1, 3))
        # The rest at the next run, ten minutes on (the default how often).
        self.assertEqual(self.run_once(minutes_later=5)[1].sent, [])
        self.assertEqual(len(self.run_once(minutes_later=10)[1].embeds()), 2)

    def test_how_often_name_and_picture_are_the_channels(self):
        self.channel(every_minutes=60, per_post=2, username="Raptor News", use_logo=False, restocks=False)
        self.run_once()
        self.restock("Off Update Booster Box")
        for n in range(3):
            self.restock(f"Opening {n}", minutes_ago=n + 1, model=PreorderOpen, availability=Listing.Availability.PREORDER)
        sent, outbox = self.run_once()
        self.assertNotIn("restock", sent["#alerts"])
        [(_url, data)] = outbox.sent
        self.assertEqual(outbox.titles(), ["Pre-orders open: Opening 0", "Pre-orders open: Opening 1"])
        self.assertEqual(data["username"], "Raptor News")
        self.assertNotIn("avatar_url", data)
        # Not again until an hour has passed: then the one left over goes.
        self.assertEqual(self.run_once(minutes_later=30)[1].sent, [])
        self.assertEqual(self.run_once(minutes_later=58)[1].titles(), ["Pre-orders open: Opening 2"])

    def test_too_many_posts_stops_the_channel_and_nothing_is_lost(self):
        self.channel()
        self.run_once()
        self.restock("Surging Sparks Booster Box")
        busy = Outbox(fail=discord.DiscordError("Discord answered 429", 429))
        sent, _ = self.run_once(outbox=busy)
        self.assertEqual(sent["#alerts"], {})
        self.assertFalse(DiscordPost.objects.filter(kind="restock").exists())
        self.assertEqual(len(self.run_once()[1].embeds()), 1)

    def test_price_drops_at_or_above_the_channels_smallest_drop(self):
        self.channel(min_drop_percent=5)
        self.channel("#big-drops", hook=DROPS_HOOK, min_drop_percent=20)
        self.run_once()
        today = timezone.localdate(self.now)
        # An RRP to measure the new price by: without any, a drop is not posted (tests_rrp.py).
        dropped = make_product(self.set, name="Journey Together Booster Box", rrp=Decimal("59.99"))
        make_listing(dropped, self.shop, price="47.01", delivery="2.99")
        DailyLowestPrice.objects.create(product=dropped, date=today - timedelta(days=1), price="60.00")   # 16.7%
        small = make_product(self.set, name="Black Bolt Booster Box")
        make_listing(small, self.shop, price="58.01", delivery="2.99")
        DailyLowestPrice.objects.create(product=small, date=today - timedelta(days=1), price="62.00")    # 1.6%
        outbox = self.run_once()[1]
        [embed] = outbox.embeds(HOOK)
        self.assertEqual((embed["author"]["name"], embed["title"]), ("Price drop", "Journey Together Booster Box"))
        self.assertTrue(embed["description"].startswith("Now **£50.00** delivered, was £60.00 yesterday. Checked "))
        self.assertEqual(embed["fields"], [{"name": "Price check", "value": "**Fair price**\nRRP £59.99", "inline": True}])
        self.assertEqual(outbox.embeds(DROPS_HOOK), [])
        self.assertEqual(self.run_once(minutes_later=10)[1].sent, [])

    def test_a_set_out_today_goes_out_from_eight(self):
        self.channel("#drops", hook=DROPS_HOOK, restocks=False, preorders=False, price_drops=False)
        today = timezone.localdate(self.now)
        out = make_set(self.game, name="Mega Evolution", slug="mega-evolution", code="MEG", release_date=today)
        make_listing(make_product(out, name="Mega Evolution Booster Box"), self.shop, price="147.01", delivery="2.99")
        early = self.now.replace(hour=7)
        discord.run(now=early - timedelta(minutes=30), post=Outbox())   # set up, before anything is due
        before = Outbox()
        discord.run(now=early, post=before)
        self.assertEqual(before.sent, [])
        after = Outbox()
        discord.run(now=self.now, post=after)
        [embed] = after.embeds(DROPS_HOOK)
        self.assertEqual((embed["author"]["name"], embed["title"]), ("Out today", "Mega Evolution (Pokémon)"))
        self.assertTrue(embed["description"].startswith("Cheapest from **£150.00** delivered, checked "))
        self.assertEqual(embed["url"], f"https://ripraptor.com{out.get_absolute_url()}"
                                       "?utm_source=discord&utm_medium=social&utm_campaign=alerts")

    @override_settings(RIPRAPTOR_DISCORD_WEBHOOK=HOOK)
    def test_the_address_in_env_gets_everything_until_a_channel_is_added(self):
        self.run_once()
        self.restock("Surging Sparks Booster Box", minutes_ago=2)
        sent, outbox = self.run_once()
        self.assertEqual((sent, outbox.titles(HOOK)), ({".env": {"restock": 1, "preorder": 0, "price_drop": 0, "drop_day": 0}},
                                                       ["Back in stock: Surging Sparks Booster Box"]))
        self.channel("#restocks", hook=DROPS_HOOK, preorders=False, price_drops=False, drop_days=False)
        self.assertEqual(list(self.run_once(minutes_later=10)[0]), ["#restocks"])

    def test_the_test_command_says_hello_to_each_channel_and_never_prints_the_address(self):
        self.channel()
        self.channel("#drops", hook=DROPS_HOOK)
        with mock.patch.object(discord, "send") as send:
            out = StringIO()
            call_command("post_discord", "--test", stdout=out)
        self.assertEqual(sorted(call.args[0] for call in send.call_args_list), [HOOK, DROPS_HOOK])
        self.assertIn("RipRaptor is connected", send.call_args_list[0].args[1]["content"])
        self.assertEqual(out.getvalue().strip(), "Hello posted to 2 Discord channels.")
        with mock.patch.object(discord, "send", side_effect=discord.DiscordError("Discord answered 404", 404)):
            out = StringIO()
            call_command("post_discord", "--test", stdout=out)
        self.assertIn("Not posted: Discord answered 404", out.getvalue())
        self.assertNotIn("secret-token", out.getvalue())

    def test_the_footer_links_to_the_discord_server(self):
        page = self.client.get("/")
        self.assertContains(page, 'href="https://discord.gg/5YKc77s4d7"')
        self.assertContains(page, "RipRaptor on Discord (opens in a new tab)")


class ChannelAdminTests(TestCase):
    """A page per channel: its webhook (typed like a password, never shown again), its updates, how often and
    how it looks, and a test post."""

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))
        self.game = make_game()

    def form(self, **fields):
        data = {"label": "#restocks", "webhook_url": HOOK, "is_active": "on", "restocks": "on", "min_drop_percent": "5",
                "every_minutes": "10", "per_post": "8", "username": "", "use_logo": "on"}
        data.update(fields)
        return {key: value for key, value in data.items() if value is not None}

    def test_adding_a_channel_checks_the_address_and_never_shows_it_again(self):
        add = reverse("admin:catalogue_discordchannel_add")
        page = self.client.get(add)
        self.assertContains(page, 'type="password"')
        self.assertContains(page, "Every 10 minutes")
        refused = self.client.post(add, self.form(webhook_url="https://example.com/not-a-webhook"))
        self.assertContains(refused, "That is not a Discord webhook address")
        nothing_ticked = self.client.post(add, self.form(restocks=None))
        self.assertContains(nothing_ticked, "Tick at least one update for this channel.")
        self.client.post(add, self.form(games=[self.game.pk]))
        channel = DiscordChannel.objects.get()
        self.assertEqual((channel.webhook_url, list(channel.games.all())), (HOOK, [self.game]))
        change = reverse("admin:catalogue_discordchannel_change", args=[channel.pk])
        page = self.client.get(change).content.decode()
        self.assertNotIn("secret-token", page)
        self.assertIn("ends ...oken", page)
        listing = self.client.get(reverse("admin:catalogue_discordchannel_changelist")).content.decode()
        self.assertIn("Back in stock", listing)
        self.assertNotIn("secret-token", listing)
        # Saving without typing it again keeps the address.
        self.client.post(change, self.form(webhook_url="", every_minutes="60", price_drops="on"))
        channel.refresh_from_db()
        self.assertEqual((channel.webhook_url, channel.every_minutes, channel.price_drops), (HOOK, 60, True))

    def test_a_set_is_chosen_by_typing_its_name_and_the_list_names_it(self):
        celebration = make_set(self.game, name="30th Celebration", slug="30th-celebration", code="CEL")
        add = reverse("admin:catalogue_discordchannel_add")
        page = self.client.get(add).content.decode()
        self.assertIn('name="sets"', page)
        self.assertIn("admin-autocomplete", page)
        found = self.client.get(reverse("admin:autocomplete"), {"term": "30th", "app_label": "catalogue",
                                                                  "model_name": "discordchannel", "field_name": "sets"})
        self.assertEqual([row["text"] for row in found.json()["results"]], ["30th Celebration"])
        self.client.post(add, self.form(games=[self.game.pk], sets=[celebration.pk]))
        channel = DiscordChannel.objects.get()
        self.assertEqual(list(channel.sets.all()), [celebration])
        listing = self.client.get(reverse("admin:catalogue_discordchannel_changelist")).content.decode()
        self.assertIn("Back in stock (30th Celebration only)", listing)

    def test_a_test_post_goes_to_that_channel_only(self):
        channel = DiscordChannel.objects.create(label="#restocks", webhook_url=HOOK, restocks=True)
        DiscordChannel.objects.create(label="#drops", webhook_url=DROPS_HOOK, drop_days=True)
        with mock.patch.object(discord, "send") as send:
            self.client.post(reverse("admin:catalogue_discordchannel_change", args=[channel.pk]),
                             self.form(webhook_url="", username="Raptor News", send_test="on"))
        [call] = send.call_args_list
        self.assertEqual(call.args[0], HOOK)
        self.assertEqual(call.args[1]["username"], "Raptor News")


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
