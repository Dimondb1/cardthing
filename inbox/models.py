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
    """A shop asking to be listed, from the List your shop page (inbox/shops.py).

    Its website is checked at once by the shop checker (catalogue/shop_adding.py, ``check``), which never adds it:
    the owner approves it in admin, and only then is the shop added, its discount code saved and one welcome email
    sent to the address given. Nothing else is ever emailed to it. Deleted a year after it is marked handled, or
    after two years if it never is (purge, run by tidy_all).
    """

    name = models.CharField(max_length=80)
    shop_url = models.CharField("shop website", max_length=300)
    domain = models.CharField(max_length=253, db_index=True, editable=False,
                              help_text="The website's domain without www, so one shop asks once a month.")
    email = models.EmailField(max_length=254)
    message = models.TextField(max_length=1000, blank=True)
    discount_code = models.CharField(max_length=40, blank=True,
                                     help_text="A code the shop offers RipRaptor shoppers, saved on the shop when you "
                                               "approve it.")
    discount_terms = models.CharField(max_length=120, blank=True, help_text="What the code gives, as the shop put it.")
    shop_check = models.ForeignKey("catalogue.ShopToAdd", null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="enquiries", editable=False,
                              help_text="The shop checker's look at the website.")
    retailer = models.ForeignKey("catalogue.Retailer", null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="+", editable=False, verbose_name="shop",
                                 help_text="The shop on RipRaptor, once approved.")
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)
    welcome_sent_at = models.DateTimeField("welcome email sent", null=True, blank=True, editable=False)
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
    email_found_on = models.URLField(
        "address found on", blank=True,
        help_text="The page of the shop's own site the address was found on (inbox/shop_emails.py). Empty for an "
                  "address you typed; cleared when you change the address.",
    )
    email_looked_at = models.DateTimeField("looked for an address", null=True, blank=True, editable=False)
    email_note = models.CharField(max_length=120, blank=True, editable=False,
                                  help_text="Why the last look for an address found none.")
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


class OutreachMail(models.Model):
    """The one row of settings for sending shop outreach emails from the app (inbox/sending.py). The owner, 3 Nov 2026,
    after a test through their Zoho mailbox timed out on the server: "I want to use zepto to send out". So it sends
    through ZeptoMail by default, the way the alert emails already leave the server (over the web, which the server can
    reach), from an address on the site's domain with replies to the owner's inbox. Their own Zoho mailbox (over SMTP)
    stays as the other choice, for a server that can reach Zoho's mail server. Its password (an app password), and a
    separate ZeptoMail token if one is given, are never kept here: they live in files only the site can read
    (RIPRAPTOR_OUTREACH_PASSWORD_FILE, RIPRAPTOR_OUTREACH_TOKEN_FILE)."""

    class Service(models.TextChoices):
        ZEPTOMAIL = "zeptomail", "ZeptoMail, as the alert emails"
        MAILBOX = "mailbox", "Your Zoho mailbox"

    on = models.BooleanField("send from RipRaptor", default=False,
                             help_text="Ticked: Send now appears on every shop email. Unticked: only Open in your mail app.")
    service = models.CharField(
        "send through", max_length=10, choices=Service.choices, default=Service.ZEPTOMAIL,
        help_text="ZeptoMail works from RipRaptor's server. Your Zoho mailbox needs the server to reach Zoho's mail "
                  "server, which many hosts block.")
    host = models.CharField("mail server", max_length=100, default="smtp.zoho.com",
                            help_text="Zoho's: smtp.zoho.com. Use smtp.zoho.eu if your Zoho account is on the EU site.")
    port = models.PositiveIntegerField(default=465, help_text="465 for Zoho (the usual) or 587.")
    username = models.EmailField(
        "your mailbox", blank=True,
        help_text="Only for sending through your Zoho mailbox: its address, such as info@ripraptor.com.")
    from_name = models.CharField("sent as", max_length=80, default="Ben Dimond", help_text="The name shops see.")
    from_address = models.EmailField(
        "sent from", blank=True,
        help_text="The address shops see, such as info@ripraptor.com. Through ZeptoMail it must be on ripraptor.com. "
                  "Through your mailbox, leave empty to send from the mailbox, or use an alias you added in Zoho.")
    reply_to = models.EmailField(
        "replies go to", blank=True,
        help_text="Where shops' replies and your copies go. Leave empty for the Sent from address. Use an inbox you "
                  "read.")
    copy_to_me = models.BooleanField("send me a copy", default=True,
                                     help_text="Each email is also copied to the Replies go to address, so the full email is "
                                               "in your inbox.")
    daily_limit = models.PositiveSmallIntegerField(
        "most a day", default=30,
        help_text="No more than this many shop emails in a day, sent or marked as sent. It keeps the sender "
                  "trusted and the emails personal.")
    password_hint = models.CharField(max_length=4, blank=True, editable=False)
    token_hint = models.CharField(max_length=4, blank=True, editable=False)
    password_saved_at = models.DateTimeField(null=True, blank=True, editable=False)
    problem = models.CharField(max_length=300, blank=True, editable=False,
                               help_text="Why the last send or test failed.")

    class Meta:
        verbose_name = "sending shop emails"
        verbose_name_plural = "sending shop emails"

    def __str__(self):
        return "Sending shop emails"

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]

    @classmethod
    def current(cls):
        """The row, or the defaults unsaved: a page that only reads it never writes."""
        return cls.objects.filter(pk=1).first() or cls(pk=1)

    @property
    def by_zeptomail(self):
        return self.service == self.Service.ZEPTOMAIL

    @property
    def sender(self):
        """The address the email is from. Through ZeptoMail it is never the mailbox, which may not be on the domain."""
        return self.from_address if self.by_zeptomail else (self.from_address or self.username)

    @property
    def inbox(self):
        """Where replies, the copies and the tests go."""
        return self.reply_to or self.sender


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
