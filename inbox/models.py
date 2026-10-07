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
