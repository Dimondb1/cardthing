from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalogue import mail, notify

from . import service
from .models import Conversation, Message

MAIL = {"RIPRAPTOR_ZEPTOMAIL_TOKEN": "tok", "RIPRAPTOR_MAIL_FROM": "alerts@ripraptor.com",
        "RIPRAPTOR_SITE_URL": "https://ripraptor.com", "RIPRAPTOR_INBOX_NOTIFY_EMAIL": "owner@example.com",
        "RIPRAPTOR_NTFY_TOPIC": "rr-test-topic", "RIPRAPTOR_NTFY_URL": "https://ntfy.sh"}


class Outbox:
    def __init__(self):
        self.sent = []

    def __call__(self, to, subject, text, html, opener=None, unsubscribe=""):
        self.sent.append({"to": to, "subject": subject, "text": text, "html": html})
        return {}


class Pushes:
    def __init__(self):
        self.sent = []

    def __call__(self, request, timeout=None):
        self.sent.append(request)
        return mock.MagicMock()


@override_settings(**MAIL)
class MessageUsTests(TestCase):
    def setUp(self):
        self.outbox = Outbox()
        self.pushes = Pushes()
        for patcher in (mock.patch.object(mail, "send", self.outbox),
                        mock.patch.object(notify.urllib.request, "urlopen", self.pushes)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def send(self, **data):
        return self.client.post(reverse("web:contact"), {"body": "The Roaring Moon price looks wrong", **data})

    def test_page_loads_and_footer_links_to_it(self):
        response = self.client.get(reverse("web:contact"))
        self.assertContains(response, "Message us")
        self.assertContains(self.client.get(reverse("web:about")), f'href="{reverse("web:contact")}"')

    def test_sending_starts_a_private_thread_and_remembers_it(self):
        response = self.send(name="Sam")
        conversation = Conversation.objects.get()
        self.assertRedirects(response, conversation.get_absolute_url() + "?sent=1#reply", fetch_redirect_response=False)
        self.assertEqual(response.cookies["rr_inbox"].value, conversation.token)
        self.assertTrue(response.cookies["rr_inbox"]["httponly"])
        self.assertEqual((conversation.name, conversation.email, conversation.unread), ("Sam", "", True))
        page = self.client.get(conversation.get_absolute_url() + "?sent=1")
        self.assertContains(page, "The Roaring Moon price looks wrong")
        self.assertContains(page, "Sent. Our reply will appear on this page.")
        self.assertContains(page, '<meta name="robots" content="noindex')
        self.assertContains(self.client.get(reverse("web:contact")), "Open your conversation")

    def test_owner_is_told_by_email_and_push(self):
        self.send(name="Sam")
        conversation = Conversation.objects.get()
        self.assertEqual(self.outbox.sent[0]["to"], "owner@example.com")
        self.assertIn("The Roaring Moon price looks wrong", self.outbox.sent[0]["text"])
        self.assertIn(f"/admin/inbox/conversation/{conversation.pk}/change/", self.outbox.sent[0]["text"])
        push = self.pushes.sent[0]
        self.assertEqual(push.full_url, "https://ntfy.sh/rr-test-topic")
        # ntfy.sh topics are public, so the push carries neither the name nor the words.
        self.assertNotIn(b"Roaring", push.data)
        self.assertNotIn("Sam", push.get_header("Title"))
        self.assertIn(f"/admin/inbox/conversation/{conversation.pk}/change/", push.get_header("Click"))

    def test_a_failed_notification_keeps_the_message(self):
        def broken(*args, **kwargs):
            raise mail.MailError("down")
        with mock.patch.object(mail, "send", broken), mock.patch.object(notify.urllib.request, "urlopen", side_effect=OSError("no")):
            self.send()
        self.assertEqual(Message.objects.count(), 1)

    def test_no_notification_settings_still_works(self):
        with self.settings(RIPRAPTOR_INBOX_NOTIFY_EMAIL="", RIPRAPTOR_NTFY_TOPIC=""):
            self.send()
        self.assertEqual((self.outbox.sent, self.pushes.sent, Message.objects.count()), ([], [], 1))

    def test_every_message_is_sent_even_with_crawl_pushes_off(self):
        # Messages are not crawl problems: neither the once-a-day rule nor RIPRAPTOR_CRAWL_PUSHES holds them back.
        with self.settings(RIPRAPTOR_CRAWL_PUSHES=False):
            self.send(name="Sam")
            self.client.post(Conversation.objects.get().get_absolute_url(), {"body": "Also Zatu has it cheaper"})
        self.assertEqual([p.get_header("Title") for p in self.pushes.sent], ["New RipRaptor message"] * 2)
        self.assertEqual(len(self.outbox.sent), 2)
        self.assertEqual(self.outbox.sent[0]["subject"], "New message from Sam")
        self.assertIn("Read and reply: https://ripraptor.com/admin/inbox/conversation/", self.outbox.sent[0]["text"])
        for push in self.pushes.sent:
            self.assertEqual(push.data, b"Open admin to read it.")
            self.assertNotIn("Zatu", push.get_header("Title"))

    def test_bad_input_is_refused_and_kept(self):
        self.assertContains(self.send(body="  "), "Write a message first.")
        response = self.send(email="not an email")
        self.assertContains(response, "That email address does not look right.")
        self.assertContains(response, "The Roaring Moon price looks wrong")
        self.send(website="http://spam.example")
        self.assertFalse(Conversation.objects.exists())

    def test_follow_ups_and_hourly_limit(self):
        self.send()
        conversation = Conversation.objects.get()
        Conversation.objects.update(unread=False)
        url = conversation.get_absolute_url()
        self.client.post(url, {"body": "Also Zatu has it cheaper"})
        conversation.refresh_from_db()
        self.assertTrue(conversation.unread)
        self.assertEqual(conversation.messages.count(), 2)
        for _ in range(service.PER_THREAD_PER_HOUR):
            self.client.post(url, {"body": "more"})
        self.assertEqual(conversation.messages.count(), service.PER_THREAD_PER_HOUR)
        self.assertContains(self.client.post(url, {"body": "more"}), "That is a lot of messages in an hour.")

    def test_flood_limit_on_new_threads(self):
        for _ in range(service.NEW_THREADS_PER_HOUR):
            service.start("hi")
        self.assertContains(self.send(), "Try again in an hour.")

    def test_closed_thread_takes_no_more(self):
        self.send()
        conversation = Conversation.objects.get()
        Conversation.objects.update(closed=True)
        response = self.client.post(conversation.get_absolute_url(), {"body": "hello"})
        self.assertContains(response, "This conversation is closed.")
        self.assertEqual(conversation.messages.count(), 1)

    def test_unknown_thread_is_not_found(self):
        self.assertEqual(self.client.get(reverse("web:contact_thread", args=["nope"])).status_code, 404)

    def test_threads_are_deleted_after_six_months(self):
        old = service.start("old", now=timezone.now() - timedelta(days=service.KEEP_DAYS + 1))
        service.start("new")
        self.assertFalse(Conversation.objects.filter(pk=old.pk).exists())
        self.assertEqual(Conversation.objects.count(), 1)

    def test_unsubscribe_link_deletes_the_email(self):
        self.send(email="Sam@Example.com")
        conversation = Conversation.objects.get()
        self.assertEqual(conversation.email, "sam@example.com")
        page = self.client.get(conversation.get_absolute_url())
        self.assertContains(page, "We will also email sam@example.com when we reply.")
        # Opening the link only asks: mail scanners open links and must not delete anything.
        page = self.client.get(conversation.get_absolute_url() + "?forget=1")
        self.assertContains(page, "Delete my email address")
        conversation.refresh_from_db()
        self.assertEqual(conversation.email, "sam@example.com")
        page = self.client.post(conversation.get_absolute_url(), {"forget": "1"})
        self.assertContains(page, "Your email address has been deleted.")
        conversation.refresh_from_db()
        self.assertEqual(conversation.email, "")
        self.assertEqual(conversation.messages.count(), 1)

    def test_robots_keeps_threads_out_of_search(self):
        self.assertContains(self.client.get("/robots.txt"), "Disallow: /contact/c/")


@override_settings(**MAIL)
class AdminReplyTests(TestCase):
    def setUp(self):
        self.outbox = Outbox()
        patcher = mock.patch.object(mail, "send", self.outbox)
        patcher.start()
        self.addCleanup(patcher.stop)
        with self.settings(RIPRAPTOR_INBOX_NOTIFY_EMAIL="", RIPRAPTOR_NTFY_TOPIC=""):
            self.conversation = service.start("Can you add Lorcana tins?", name="Sam", email="sam@example.com")
        self.admin = get_user_model().objects.create_superuser("ben", "ben@example.com", "pw")
        self.client.force_login(self.admin)
        self.url = reverse("admin:inbox_conversation_change", args=[self.conversation.pk])

    def post(self, reply, **extra):
        return self.client.post(self.url, {"reply": reply, **extra})

    def test_index_shows_new_messages(self):
        self.assertContains(self.client.get(reverse("admin:index")), "1 new message")

    def test_opening_marks_read(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Can you add Lorcana tins?")
        self.assertNotContains(page, "&lt;p&gt;")
        self.conversation.refresh_from_db()
        self.assertFalse(self.conversation.unread)

    def test_reply_is_stored_and_emailed(self):
        response = self.post("Added them today, thanks Sam.")
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        reply = self.conversation.messages.get(from_visitor=False)
        self.assertEqual(reply.body, "Added them today, thanks Sam.")
        sent = self.outbox.sent[-1]
        self.assertEqual(sent["to"], "sam@example.com")
        self.assertIn("Added them today", sent["text"])
        self.assertIn(self.conversation.get_absolute_url(), sent["text"])
        self.client.logout()
        page = self.client.get(self.conversation.get_absolute_url())
        self.assertContains(page, "Added them today, thanks Sam.")
        self.assertContains(page, "thread__msg--us")

    def test_owner_viewing_the_thread_does_not_take_it_as_their_own(self):
        response = self.client.get(self.conversation.get_absolute_url())
        self.assertContains(response, "Can you add Lorcana tins?")
        self.assertNotIn("rr_inbox", response.cookies)

    def test_reply_without_email_is_only_on_the_page(self):
        Conversation.objects.update(email="")
        self.conversation.refresh_from_db()
        self.post("Thanks")
        self.assertEqual(self.outbox.sent, [])
        self.assertTrue(self.conversation.messages.filter(from_visitor=False).exists())

    def test_saving_without_a_reply_adds_nothing(self):
        self.post("", closed="on")
        self.conversation.refresh_from_db()
        self.assertTrue(self.conversation.closed)
        self.assertEqual(self.conversation.messages.count(), 1)
