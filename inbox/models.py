import secrets

from django.db import models
from django.utils import timezone


def thread_token():
    return secrets.token_urlsafe(24)


class Conversation(models.Model):
    """One visitor's thread of messages with RipRaptor.

    Nobody signs in. The visitor keeps the private link to the thread, and an
    email address is optional: given, it is used only to say a reply is
    waiting. Threads are deleted six months after their last message.
    """

    token = models.CharField(max_length=40, unique=True, default=thread_token, editable=False)
    name = models.CharField(max_length=80, blank=True)
    email = models.EmailField(max_length=254, blank=True, help_text="Optional. Only used to tell them you replied.")
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    last_message_at = models.DateTimeField(default=timezone.now, editable=False)
    unread = models.BooleanField(default=True, help_text="A visitor message you have not opened yet.")
    closed = models.BooleanField(default=False, help_text="Closed threads take no new visitor messages.")

    class Meta:
        ordering = ["-unread", "-last_message_at"]
        verbose_name = "conversation"

    def __str__(self):
        first = self.messages.order_by("created_at").first()
        who = self.name or "Visitor"
        return f"{who}: {first.body[:60]}" if first else who

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("web:contact_thread", args=[self.token])


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    from_visitor = models.BooleanField(default=True)
    body = models.TextField(max_length=3000)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return ("Visitor" if self.from_visitor else "RipRaptor") + f": {self.body[:60]}"


class ShopEnquiry(models.Model):
    """A shop asking to be listed, from the List your shop page.

    Kept to answer it and for nothing else: nothing is ever emailed to the address given, and the shop's
    website is never fetched until the owner adds the shop themselves. Deleted a year after it is marked
    handled, or after two years if it never is (inbox/shops.py purge, run by tidy_all).
    """

    name = models.CharField(max_length=80)
    shop_url = models.CharField("shop website", max_length=300)
    domain = models.CharField(max_length=253, db_index=True, editable=False,
                              help_text="The website's domain without www, so one shop asks once a month.")
    email = models.EmailField(max_length=254)
    message = models.TextField(max_length=1000, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False, db_index=True)
    handled_at = models.DateTimeField(null=True, blank=True, help_text="When you dealt with it. Blank while it waits.")
    note = models.TextField(blank=True, help_text="Your own note. Never shown to the shop.")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "shop enquiry"
        verbose_name_plural = "shop enquiries"

    def __str__(self):
        return f"{self.name}: {self.domain}"


class ShopContact(models.Model):
    """Who to email at a shop RipRaptor lists, and where things stand with them (inbox/outreach.py).

    The owner writes to shops from their own mailbox: the Shop outreach page in admin fills in an email with the
    shop's own figures and opens it in their mail app, and replies are handled there. Only a business address is
    kept, typed by the owner, and nothing is ever sent from the site. "Do not email" is kept for good, so a shop
    that asked never hears from RipRaptor again.
    """

    class Status(models.TextChoices):
        NEW = "new", "Not contacted"
        EMAILED = "emailed", "Emailed"
        REPLIED = "replied", "Replied"
        AFFILIATE = "affiliate", "Affiliate link live"
        DECLINED = "declined", "Not interested"
        DO_NOT_EMAIL = "do_not_email", "Do not email"

    retailer = models.OneToOneField("catalogue.Retailer", on_delete=models.CASCADE, related_name="contact",
                                    verbose_name="shop")
    email = models.EmailField(
        max_length=254, blank=True,
        help_text="The shop's business address, as its website gives it (info@, hello@). Kept only to write to them.",
    )
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.NEW, db_index=True)
    notes = models.TextField(blank=True, help_text="Your own notes: who you spoke to, what they said. Never shown.")
    follow_up_on = models.DateField("follow up on", null=True, blank=True,
                                    help_text="Set to two weeks on when you mark an email as sent, unless set.")
    last_emailed_at = models.DateTimeField("last emailed", null=True, blank=True, editable=False)

    class Meta:
        ordering = ["retailer__name"]
        verbose_name = "shop outreach"
        verbose_name_plural = "shop outreach"

    def __str__(self):
        return str(self.retailer)


class OutreachEmail(models.Model):
    """One email the owner marked as sent to a shop: which draft and its subject. The email itself is in the
    owner's own Sent folder."""

    contact = models.ForeignKey(ShopContact, on_delete=models.CASCADE, related_name="emails")
    kind = models.CharField(max_length=12)
    subject = models.CharField(max_length=200)
    sent_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-sent_at"]

    def __str__(self):
        return f"{self.contact}: {self.subject}"
