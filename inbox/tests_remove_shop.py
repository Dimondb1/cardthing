"""Remove my shop (inbox/shops.py ask_removal, take_off), beside List your shop. The owner, 14 Nov 2026, taking the
Shops we compare page down: "just leave a remove shop request next to the list my shop button". Nothing changes until
the owner checks the request is the shop's own and takes it off in admin; nothing is ever emailed to the address."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail as django_mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalogue.models import Retailer
from catalogue.testing import make_retailer

from . import shops
from .models import ShopContact, ShopRemoval

URL = reverse("web:remove_shop")


@override_settings(RIPRAPTOR_NTFY_TOPIC="")
class RemoveCase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.shop = make_retailer("Example Cards", website="https://www.examplecards.co.uk/")
        self.told = self.enterContext(mock.patch.object(shops.notify, "owner"))

    def ask(self, address="203.0.113.7", **fields):
        data = {"name": "Sam Example", "shop_url": "examplecards.co.uk", "email": "hello@examplecards.co.uk",
                "message": "We would rather not be listed.", **fields}
        return self.client.post(URL, data, REMOTE_ADDR=address)


class AskTests(RemoveCase):
    def test_the_page_is_a_plain_form_kept_out_of_search(self):
        html = self.client.get(URL).content.decode()
        self.assertIn("<h1 class=\"page-title\">Remove my shop</h1>", html)
        self.assertIn(f'<form class="contact-form" method="post" action="{URL}">', html)
        for name in ("name", "shop_url", "email", "message"):
            self.assertIn(f'name="{name}"', html)
        self.assertIn("Ask to be removed", html)
        self.assertIn('content="noindex', html)

    def test_a_request_is_kept_matched_to_the_shop_and_the_owner_told_but_nothing_changes(self):
        response = self.ask()
        self.assertRedirects(response, reverse("web:remove_shop_thanks"), fetch_redirect_response=False)
        removal = ShopRemoval.objects.get()
        self.assertEqual((removal.domain, removal.email, removal.retailer), ("examplecards.co.uk",
                                                                              "hello@examplecards.co.uk", self.shop))
        self.assertTrue(shops.email_matches(removal))
        self.told.assert_called_once()
        subject = self.told.call_args.args[0]
        self.assertEqual(subject, "Shop asking to be removed: examplecards.co.uk")
        self.assertIn("anyone could ask to remove a rival", self.told.call_args.kwargs["detail"])
        self.shop.refresh_from_db()
        self.assertTrue(self.shop.is_active)
        self.assertEqual(django_mail.outbox, [])   # nothing to the address given
        thanks = self.client.get(reverse("web:remove_shop_thanks"))
        self.assertContains(thanks, "Once we have checked the request comes from the shop, we take its prices off")

    def test_a_wrong_field_says_which(self):
        response = self.ask(shop_url="not a website")
        self.assertContains(response, "That website address does not look right.")
        self.assertEqual(ShopRemoval.objects.count(), 0)

    def test_asking_twice_or_as_a_robot_stores_nothing_more(self):
        self.ask()
        self.ask()
        self.ask(website="spam.example")
        self.assertEqual(ShopRemoval.objects.count(), 1)
        self.assertEqual(self.told.call_count, 1)

    def test_one_address_cannot_send_many(self):
        for n in range(shops.PER_ADDRESS_HOUR):
            self.ask(shop_url=f"shop{n}.co.uk")
        self.assertContains(self.ask(shop_url="one-more.co.uk"), "We have had a lot of requests just now.")
        self.assertEqual(ShopRemoval.objects.count(), shops.PER_ADDRESS_HOUR)

    def test_a_shop_not_on_ripraptor_is_kept_without_a_match(self):
        self.ask(shop_url="unknown-shop.co.uk", email="me@unknown-shop.co.uk")
        self.assertIsNone(ShopRemoval.objects.get().retailer)


class TakeOffTests(RemoveCase):
    def setUp(self):
        super().setUp()
        self.ask()
        self.removal = ShopRemoval.objects.get()

    def test_taking_it_off_switches_the_shop_off_and_stops_emails(self):
        self.assertEqual(shops.take_off(self.removal), self.shop)
        self.shop.refresh_from_db()
        self.assertFalse(self.shop.is_active)
        self.assertEqual(ShopContact.objects.get(retailer=self.shop).status, ShopContact.Status.DO_NOT_EMAIL)
        self.removal.refresh_from_db()
        self.assertIsNotNone(self.removal.removed_at)
        self.assertIsNotNone(self.removal.handled_at)

    def test_with_no_shop_matched_nothing_changes(self):
        ShopRemoval.objects.filter(pk=self.removal.pk).update(retailer=None)
        self.removal.refresh_from_db()
        with self.assertRaises(shops.NotReady):
            shops.take_off(self.removal)
        self.assertTrue(Retailer.objects.get(pk=self.shop.pk).is_active)

    def test_the_owner_takes_it_off_from_admin(self):
        owner = get_user_model().objects.create_superuser("owner", "o@example.com", "pw")
        self.client.force_login(owner)
        page = self.client.get(reverse("admin:inbox_shopremoval_change", args=[self.removal.pk]))
        self.assertContains(page, "On the shop&#x27;s own domain (examplecards.co.uk)")
        response = self.client.post(reverse("admin:inbox_shopremoval_changelist"), {
            "action": "take_off", "_selected_action": [self.removal.pk]}, follow=True)
        self.assertContains(response, "Taken off RipRaptor: Example Cards.")
        self.assertFalse(Retailer.objects.get(pk=self.shop.pk).is_active)

    def test_old_requests_are_deleted_with_the_enquiries(self):
        ShopRemoval.objects.update(handled_at=timezone.now() - timedelta(days=shops.HANDLED_KEEP_DAYS + 1))
        self.assertEqual(shops.purge(), 1)
        self.assertFalse(ShopRemoval.objects.exists())


class LinkTests(RemoveCase):
    def test_remove_my_shop_sits_beside_list_your_shop(self):
        home = self.client.get(reverse("web:home")).content.decode()
        footer = home[home.index('class="site-footer__nav"'):]
        self.assertLess(footer.index(reverse("web:list_shop")), footer.index(URL))
        self.assertIn(f'<a href="{URL}" rel="nofollow">Remove my shop</a>', footer)
        about = self.client.get(reverse("web:about")).content.decode()
        self.assertIn(f'<a class="button button--quiet" href="{URL}" rel="nofollow">Remove my shop</a>', about)
        listing = self.client.get(reverse("web:list_shop")).content.decode()
        self.assertIn("Already on RipRaptor and want to come off?", listing)

    def test_the_terms_say_what_a_removal_request_keeps(self):
        self.assertContains(self.client.get(reverse("web:terms")),
                            "If you ask us to remove your shop, we keep the same details")
