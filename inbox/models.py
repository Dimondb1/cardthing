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
