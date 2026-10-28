"""
Guides: plain advice wired to RipRaptor's live prices (marketing's build spec, 27 Oct 2026).

A guide is a list of sections, each a question in the reader's words with its short answer first, then the
detail and what backs it: green and red flags, live price cards for chosen products, a "What is in the box"
card for a product type, the Fair Price Checker, and its sources. Everything is edited in Admin. A guide is
shown to the public only once it is published, and it can be published only when every section that states
facts has a source and the next review is at most text.REVIEW_DAYS after the last check (guides/text.py).
Staff see a draft at its own address with what still stops it.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse

# Addresses under /guides/ that are pages of their own, never a guide's.
RESERVED = {"fair-price", "gift-finder", "scalper-watch", "feedback", "pokemon", "magic", "one-piece", "lorcana",
            "yu-gi-oh"}


def not_reserved(slug):
    if slug in RESERVED:
        raise ValidationError(f"/guides/{slug}/ is kept for another page. Choose another address.")


class Guide(models.Model):
    class Audience(models.TextChoices):
        PARENTS = "parents", "Parents and gift buyers"
        COLLECTORS = "collectors", "New collectors"
        EVERYONE = "everyone", "Everyone"

    class Door(models.TextChoices):
        NONE = "", "Only the Guides page"
        BUYING = "buying", "Nowhere else: product pages with a price link to the Guides page"
        SOLD_OUT = "sold_out", "Sold-out product pages and the High price check"

    slug = models.SlugField(
        "address", max_length=60, unique=True, validators=[not_reserved],
        help_text="The guide is at /guides/address/. Short, lower case, no dates. Changing it after launch breaks "
                  "links people have shared.")
    title = models.CharField(max_length=120)
    search_title = models.CharField(
        "title in search results", max_length=70, blank=True,
        help_text="In the words parents type, under 50 characters: | RipRaptor is added after it and Google cuts "
                  "long titles. For example: Pokémon cards for Christmas: a parent's guide. Blank uses the title.")
    one_line_answer = models.CharField(
        max_length=200, help_text="Under the title, on the Guides page and in search results.")
    audience = models.CharField(max_length=10, choices=Audience.choices, default=Audience.EVERYONE)
    door = models.CharField(
        "linked from", max_length=10, choices=Door.choices, blank=True,
        help_text="Where else a link to this guide is shown. One guide per place: the first published one wins.")
    door_line = models.CharField(
        "link words", max_length=80, blank=True,
        help_text="The link shown there, for example: New to this? Read the parent's guide.")
    share_image = models.ImageField(
        upload_to="guides/", blank=True,
        help_text="1200 by 630 pixels, for WhatsApp and Facebook previews. Blank uses the site's own.")
    checklist_title = models.CharField(max_length=120, blank=True)
    checklist = models.TextField(
        blank=True, help_text="One item per line. Shown as a printable page at /guides/address/checklist/.")
    owner = models.CharField(max_length=80, blank=True, help_text="Who keeps this guide right. Not shown on the site.")
    reviewed_by = models.CharField(max_length=80, blank=True, help_text="Who did the last fact check. Not shown on the site.")
    last_reviewed = models.DateField("last checked", null=True, blank=True,
                                     help_text="Shown on the guide: Checked by RipRaptor on this date.")
    next_review = models.DateField(null=True, blank=True, help_text="At most three months after the last check.")
    is_published = models.BooleanField("published", default=False)
    published_at = models.DateTimeField(null=True, blank=True, editable=False)
    position = models.PositiveSmallIntegerField(default=0, help_text="Order on the Guides page, lowest first.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("web:guide", args=[self.slug])

    @property
    def checklist_items(self):
        from .text import lines

        return lines(self.checklist)


class Section(models.Model):
    """One question of a guide, its short answer first."""

    guide = models.ForeignKey(Guide, on_delete=models.CASCADE, related_name="sections")
    position = models.PositiveSmallIntegerField(default=0)
    question = models.CharField(max_length=160, help_text="In the reader's words, for example: Is this shop real?")
    short_answer = models.TextField(
        help_text="Two or three sentences: what to do, why, and something to do next. A parent who reads only "
                  "these should still get it right, and know the reason.")
    body = models.TextField(
        blank=True,
        help_text="The detail, saying why each piece of advice holds. A blank line starts a new paragraph. Lines starting with - are a list, lines "
                  "starting with 1. a numbered list. A link: [Citizens Advice](https://www.citizensadvice.org.uk/). "
                  "No links to shops: add the product as a price card instead.")
    states_facts = models.BooleanField(
        default=True,
        help_text="Money, legal or product facts. Needs at least one source before the guide can be published.")
    green_flags = models.TextField(blank=True, help_text="Good signs, one per line, each with its reason: "
                                   "Card or PayPal at checkout, so you can claim your money back.")
    red_flags = models.TextField(blank=True, help_text="Warning signs, one per line, each with its reason: "
                                 "Bank transfer only, because card and PayPal protection is quicker and more "
                                 "certain.")
    products = models.ManyToManyField(
        "catalogue.Product", blank=True, related_name="guide_sections", verbose_name="price cards",
        help_text="Live price cards: the cheapest delivered price today at every UK shop we track.")
    box_type = models.CharField(
        "what is in the box card", max_length=30, blank=True,
        help_text="A product type's own explainer, the one product pages show.")
    box_game = models.ForeignKey("catalogue.Game", on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="+", help_text="The game the box card describes, when it differs by game.")
    show_checker = models.BooleanField("Fair Price Checker here", default=False)
    sources = models.TextField(
        blank=True,
        help_text="One per line: title | address | date read. For example: "
                  "Citizens Advice: changing your mind | https://www.citizensadvice.org.uk/ | 27 Oct 2026")

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self):
        return self.question


class Vote(models.Model):
    """Was this useful? Yes and No for one guide on one day. Nothing about the visitor is kept."""

    guide = models.ForeignKey(Guide, on_delete=models.CASCADE, related_name="votes")
    date = models.DateField(db_index=True)
    useful = models.BooleanField()
    hits = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["guide", "date", "useful"], name="guides_vote_day")]


class PriceCheck(models.Model):
    """A product checked on the Fair Price Checker, by verdict, on one day: which products people ask about and
    what came of it, the shortlist for Scalper Watch. Not the price typed and nothing about the visitor."""

    date = models.DateField(db_index=True)
    product = models.ForeignKey("catalogue.Product", on_delete=models.CASCADE, related_name="+")
    verdict = models.CharField(max_length=10)
    hits = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Fair Price Checker use"
        verbose_name_plural = "Fair Price Checker uses"
        constraints = [models.UniqueConstraint(fields=["date", "product", "verdict"], name="guides_check_day")]
