"""
Create the UK shops RipRaptor reads prices from, with their delivery rules.
Run check_delivery --apply afterwards to refresh the delivery figures.

Safe to run again: existing shops keep any changes made in admin. Delivery
figures come from each shop's own delivery page and the date they were read
is in the note, so they can be checked.
"""

from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand

from catalogue.models import Retailer

S = Retailer.Source
SHOPS = [
    # slug, name, website, source type, delivery, free over, note
    ("total-cards", "Total Cards", "https://totalcards.net/", S.SHOPIFY, "2.95", "20",
     "Standard £2.95 (basket check), free over £20 on selected orders (delivery page), read 30 Sep 2026"),
    ("gathering-games", "Gathering Games", "https://gatheringgames.co.uk/", S.SHOPIFY, "3.99", "100",
     "Standard £3.99 (basket check), free over £100 (delivery page), read 30 Sep 2026"),
    ("magic-madhouse", "Magic Madhouse", "https://magicmadhouse.co.uk/", S.WEBSITE, "0", "40",
     "Free over £40 (delivery page); standard charge not published as text, read 30 Sep 2026"),
    ("the-card-vault", "The Card Vault", "https://thecardvault.co.uk/", S.SHOPIFY, "3.95", "50",
     "Standard £3.95 (basket check), free over £50 (delivery page, confirmed by basket), read 30 Sep 2026"),
    ("lvl-up-gaming", "Lvl Up Gaming", "https://lvlupgaming.co.uk/", S.SHOPIFY, "3.00", None,
     "Standard £3.00 (basket check), read 30 Sep 2026"),
    ("zatu-games", "Zatu Games", "https://zatu.com/", S.SHOPIFY, "2.49", None,
     "Standard £2.49 (basket check), read 30 Sep 2026"),
    ("goblin-gaming", "Goblin Gaming", "https://www.goblingaming.co.uk/", S.SHOPIFY, "3.99", "75",
     "Standard £3.99 (basket check), free over £75 (delivery page, confirmed by basket), read 30 Sep 2026"),
    ("travelling-man", "Travelling Man", "https://travellingman.com/", S.SHOPIFY, "2.99", "40",
     "Standard £2.99 (basket check), free over £40 (delivery page), read 30 Sep 2026"),
    ("jet-cards", "JET Cards", "https://jetcards.uk/", S.SHOPIFY, "3.95", None,
     "Standard £3.95 (basket check), read 30 Sep 2026"),
    ("titan-cards", "Titan Cards", "https://titancards.co.uk/", S.SHOPIFY, "0", "30",
     "Free over £30 (delivery page); standard charge not yet confirmed, read 30 Sep 2026"),
    ("buy-any-cards", "Buy Any Cards", "https://buyanycards.co.uk/", S.SHOPIFY, "2.95", None,
     "Standard £2.95 (basket check), read 30 Sep 2026"),
    ("the-tcg-shop", "The TCG Shop", "https://www.thetcgshop.co.uk/", S.SHOPIFY, "0", None,
     "Delivery charge not yet confirmed"),
    ("double-sleeved", "Double Sleeved", "https://www.doublesleeved.co.uk/", S.SHOPIFY, "3.99", None,
     "Standard £3.99 (basket check), read 30 Sep 2026"),
    ("packrat", "Packrat", "https://packratt.co.uk/", S.WEBSITE, "4.15", "50",
     "Royal Mail Tracked 48 small parcel £4.15, free over £50 (delivery page), read 30 Sep 2026"),
    ("the-gamers-lodge", "The Gamers Lodge", "https://thegamerslodge.com/", S.SHOPIFY, "4.49", "150",
     "Standard £4.49 (basket check), free over £150 (delivery page), read 30 Sep 2026"),
    ("kongs-cards", "Kongs Cards", "https://kongscards.co.uk/", S.SHOPIFY, "0", "20",
     "Free UK delivery over £20 (basket check); charge under £20 not published, read 30 Sep 2026"),
    ("maxon-cards", "MaxOnCards", "https://maxoncards.co.uk/", S.WEBSITE, "3.99", "100",
     "Standard £3.99, free over £100 (delivery page), read 30 Sep 2026"),
    ("japan2uk", "Japan2UK", "https://www.japan2uk.com/", S.SHOPIFY, "2.99", "200",
     "Royal Mail Tracked 48 £2.99 (basket check), free over £200 (delivery page), read 30 Sep 2026"),
    ("iconic-trading-cards", "Iconic Trading Cards", "https://iconic-tcg.com/", S.SHOPIFY, "3.95", "90",
     "Sealed items £3.95, free over £90 (shop's FAQ; the basket check only sees the singles rate), read 30 Sep 2026"),
    ("card-empire", "Card Empire", "https://www.cardempire.co.uk/", S.SHOPIFY, "1.95", None,
     "£1.95 for orders under £20 (basket check); larger orders cost more, not published, read 30 Sep 2026"),
    ("griffins-gaming", "Griffins Gaming", "https://www.griffinsgaming.com/", S.WEBSITE, "4.00", "150",
     "Standard £4, free over £150 (delivery page), read 30 Sep 2026"),
    ("tayler-tcg", "Tayler TCG", "https://taylertcg.com/", S.SHOPIFY, "3.99", "50",
     "Royal Mail Tracked 48 £3.99, free over £50 (delivery page, confirmed by basket), read 30 Sep 2026"),
    ("shiny-vault", "Shiny Vault", "https://shinyvault.co.uk/", S.WEBSITE, "0", "75",
     "Free over £75 (delivery page); standard charge not published, read 30 Sep 2026"),
    ("monarch-cards", "Monarch Cards", "https://www.monarchcards.co.uk/", S.WEBSITE, "0", "150",
     "Free over £150 (delivery page); standard charge not published, read 30 Sep 2026"),
    ("castle-comics", "Castle Comics", "https://castlecomicsuk.co.uk/", S.SHOPIFY, "3.99", "150",
     "Standard £3.99, free over £150 (delivery page), read 30 Sep 2026"),
    ("120hp", "120HP", "https://www.120hp.co.uk/", S.SHOPIFY, "0", None,
     "Delivery charge not yet confirmed"),
    ("ancient-warrior", "Ancient Warrior", "https://www.ancientwarrior.co.uk/", S.SHOPIFY, "0", "200",
     "Free over £200 (delivery page); standard charge not published, read 1 Oct 2026"),
    ("unicorn-cards", "Unicorn Cards", "https://unicorncards.co.uk/", S.WEBSITE, "0", None,
     "Delivery charge not yet confirmed"),
    ("sports-cards-direct", "Sports Cards Direct", "https://www.sportscardsdirect.co.uk/", S.SHOPIFY, "5.49", "200",
     "DPD 1 to 2 day £5.49, free over £200 (shipping policy page), read 2 Oct 2026"),
]

# Added only once the Amazon API keys are set. Delivery figures are Amazon's
# published standard rates for items it dispatches; sellers' own charges vary.
AMAZON = ("amazon", "Amazon", "https://www.amazon.co.uk/", S.AMAZON, "4.99", "35",
          "Standard £4.99, free over £35 for items dispatched by Amazon (Amazon delivery rates page), read 1 Oct 2026")

# Added only once the eBay keys are set. Delivery comes with each listing.
EBAY = ("ebay", "eBay", "https://www.ebay.co.uk/", S.EBAY, "0", None,
        "Delivery is read from each listing, to a London postcode")

# Shops that show each visitor their own currency: the address that switches to pounds.
SESSIONS = {
    "unicorn-cards": "https://unicorncards.co.uk/changecurrency/3?returnUrl=%2F",
}

# Shopify shops read from one collection instead of the whole store: Zatu sells thousands of board
# games, puzzles and books, and all its card products sit in one collection.
COLLECTIONS = {
    "zatu-games": "trading-card-games",
}

# Shops that were set up before and must not be shown: prices in another currency,
# or not a stockist the owner wants compared (Asmodee UK is the distributor's own store).
HIDDEN = ["poke-collect", "asmodee-uk"]


class Command(BaseCommand):
    help = "Add the shops RipRaptor reads prices from. Run import_prices afterwards."

    def handle(self, *args, **options):
        shops = list(SHOPS)
        if settings.RIPRAPTOR_AMAZON_ACCESS_KEY and settings.RIPRAPTOR_AMAZON_PARTNER_TAG:
            shops.append(AMAZON)
        if settings.RIPRAPTOR_EBAY_APP_ID and settings.RIPRAPTOR_EBAY_CAMPAIGN_ID:
            shops.append(EBAY)
        for slug, name, website, source, cost, free, note in shops:
            existing = Retailer.objects.filter(website=website).first() or Retailer.objects.filter(slug=slug).first()
            session_url = SESSIONS.get(slug, "")
            collection = COLLECTIONS.get(slug, "")
            if existing:
                changed = []
                if existing.name != name or existing.slug != slug:
                    existing.name, existing.slug = name, slug
                    changed += ["name", "slug"]
                if session_url and existing.session_url != session_url:
                    existing.session_url = session_url
                    changed.append("session_url")
                if collection and not existing.collection:
                    existing.collection = collection
                    changed.append("collection")
                if changed:
                    existing.save(update_fields=changed)
                self.stdout.write(f"{name}: already set up")
                continue
            Retailer.objects.create(
                slug=slug, name=name, website=website, source_type=source, source_url=website, session_url=session_url,
                collection=collection,
                delivery_cost=Decimal(cost), free_delivery_over=Decimal(free) if free else None, delivery_note=note,
            )
            self.stdout.write(f"{name}: added")
        for slug in HIDDEN:
            hidden = Retailer.objects.filter(slug=slug, is_active=True).first()
            if hidden:
                hidden.listings.all().delete()
                hidden.is_active = False
                hidden.save(update_fields=["is_active"])
                self.stdout.write(f"{hidden.name}: hidden")
