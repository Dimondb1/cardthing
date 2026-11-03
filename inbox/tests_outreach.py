"""Shop outreach: drafts filled in with each shop's own figures, sent from the owner's own mailbox."""

from datetime import date, timedelta
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalogue.models import Listing, OutboundClick, Retailer
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from content.models import SiteContent

from . import outreach
from .models import OutreachEmail, ShopContact


class OutreachTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        game = make_game()
        product_set = make_set(game)
        cls.etb = make_product(product_set)
        cls.booster = make_product(product_set, name="Prismatic Evolutions Booster Bundle")
        cls.tin = make_product(product_set, name="Prismatic Evolutions Mini Tin")
        cls.spc = make_product(product_set, name="Prismatic Evolutions Super Premium Collection")
        cls.shop = make_retailer("Harbour Games")
        cls.other = make_retailer("Quay Cards")
        cls.ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY, website="https://www.ebay.co.uk/")
        make_listing(cls.etb, cls.shop)
        make_listing(cls.booster, cls.shop, availability=Listing.Availability.OUT_OF_STOCK)
        # Not counted as listed: one not checked for days, one kept out of the comparison, one switched off.
        make_listing(cls.tin, cls.shop, hours_ago=24 * 30)
        make_listing(cls.spc, cls.shop, sanity=Listing.Sanity.EXCLUDED)
        make_listing(make_product(product_set, name="Prismatic Evolutions Binder Collection"), cls.shop,
                     is_active=False)
        make_listing(cls.etb, cls.other)

    def click(self, product, days_ago=1, retailer=None, suspect=False):
        click = OutboundClick.objects.create(product=product, retailer=retailer or self.shop, suspect=suspect)
        OutboundClick.objects.filter(pk=click.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
        return click


class FiguresTests(OutreachTestCase):
    def test_counts_people_in_each_window_and_listings_shown(self):
        for days in (1, 2, 3):
            self.click(self.etb, days)
        self.click(self.booster, 10)
        self.click(self.booster, 45)
        self.click(self.tin, 60)
        first = self.click(self.tin, 200)
        self.click(self.spc, 1, suspect=True)          # a direct hit, never counted
        self.click(self.etb, 1, retailer=self.other)   # another shop's
        with self.assertNumQueries(4):
            found = outreach.figures(self.shop)
        self.assertEqual((found.visitors_month, found.visitors_quarter, found.visitors_total), (4, 6, 7))
        self.assertEqual(found.since, timezone.localdate(OutboundClick.objects.get(pk=first.pk).created_at))
        # Most clicked in 3 months: the ETB (3), then the bundle (2), then the tin (1). The 200-day click is out.
        self.assertEqual(found.top_products, (self.etb.name, self.booster.name, self.tin.name))
        self.assertEqual((found.listed, found.in_stock), (2, 1))

    def test_a_shop_nobody_clicked(self):
        found = outreach.figures(self.other)
        self.assertEqual((found.visitors_month, found.visitors_total, found.since, found.top_products), (0, 0, None, ()))
        subject, body = outreach.draft(self.other, "report", found)
        # No shoppers sent: no numbers in the subject, no sentence on shoppers or clicks, and nothing said of nought.
        self.assertEqual(subject, "A quick update from RipRaptor")
        for left_out in ("sent", "clicking", "none in particular"):
            self.assertNotIn(left_out, body)
        self.assertNotIn(" \n", body)

    def test_list_counts_match_the_figures_in_one_query(self):
        self.click(self.etb, 1)
        self.click(self.etb, 100)
        self.click(self.etb, 1, suspect=True)
        outreach.ensure_contacts()
        with self.assertNumQueries(1):
            rows = {c.retailer_id: (c.visitors_quarter, c.listed)
                    for c in outreach.with_counts(ShopContact.objects.all())}
        self.assertEqual(rows, {self.shop.pk: (1, 2), self.other.pk: (0, 1)})

    def test_names_read_as_a_list(self):
        self.assertEqual(outreach.listed_names([]), "")
        self.assertEqual(outreach.listed_names(["A"]), "A")
        self.assertEqual(outreach.listed_names(["A", "B"]), "A and B")
        self.assertEqual(outreach.listed_names(["A", "B", "C"]), "A, B and C")


@override_settings(RIPRAPTOR_SITE_URL="https://ripraptor.example")
class DraftTests(OutreachTestCase):
    def test_every_draft_is_filled_in_and_ends_with_the_way_to_say_no(self):
        for days in (1, 2):
            self.click(self.etb, days)
        self.click(self.booster, 50)
        found = outreach.figures(self.shop)
        optout = "If you'd rather I didn't email again, just reply and say so, and I won't."
        for kind, _name in outreach.DRAFTS:
            with self.subTest(kind):
                subject, body = outreach.draft(self.shop, kind, found)
                self.assertNotIn("{", subject + body)
                self.assertTrue(body.startswith("Hi all at Harbour Games,\n\n"))
                self.assertTrue(body.endswith(f"Thanks,\nBen Dimond\nRipRaptor, https://ripraptor.example\n\n{optout}"))
                self.assertIn("got 2 of your products", body)
                # Friendly, not shouting: no capital-letter STOP, no exclamation marks, no formal greeting.
                self.assertNotIn("STOP", body + subject)
                self.assertNotIn("!", body + subject)
                self.assertNotIn("Hello", body)
        subject, body = outreach.draft(self.shop, "affiliate", found)
        self.assertEqual(subject, "Do you run an affiliate programme?")
        self.assertIn("In the last 3 months we've sent you 3 shoppers.", body)
        _subject, body = outreach.draft(self.shop, "report", found)
        self.assertIn("In the last 30 days we've sent 2 shoppers your way, and 3 over the last 3 months.", body)
        self.assertIn(f"People have been clicking on {self.etb.name} and {self.booster.name} the most.", body)
        self.assertIn("We've got 2 of your products on the site, 1 of them in stock when we last looked.", body)

    def test_the_badge_offer_links_to_the_code_and_says_nothing_of_shoppers_never_sent(self):
        subject, body = outreach.draft(self.shop, "badge", outreach.figures(self.shop))
        self.assertEqual(subject, "A little RipRaptor badge for Harbour Games")
        self.assertIn("You'll find it and the code here: https://ripraptor.example/list-your-shop/#badge", body)
        # Never a condition: said plainly in every email that offers the badge.
        self.assertIn("It's completely up to you, and your listing stays just the same either way.", body)
        # Nobody clicked through: the shop is not told nought, and no line ends in a space.
        self.assertIn("We've got 2 of your products on there.\n\n", body)
        self.assertNotIn("we've sent you", body)
        self.assertNotIn(" \n", body)
        self.click(self.etb, 1)
        self.assertIn("In the last 3 months we've sent you 1 shopper.", outreach.draft(self.shop, "badge", outreach.figures(self.shop))[1])
        self.click(self.booster, 2)
        self.assertIn("In the last 3 months we've sent you 2 shoppers.", outreach.draft(self.shop, "badge", outreach.figures(self.shop))[1])

    def test_the_update_email_is_the_owners_wording_and_never_tells_a_shop_nought(self):
        for days in (1, 2):
            self.click(self.etb, days)
        self.click(self.booster, 50)
        subject, body = outreach.draft(self.shop, "update", outreach.figures(self.shop))
        self.assertEqual(subject, "A quick hello from RipRaptor")
        self.assertEqual(body, (
            "Hi all at Harbour Games,\n\n"
            "Ben here from RipRaptor (https://ripraptor.example), the UK site that compares prices on sealed trading "
            "card products. In the last 30 days we've sent 2 shoppers your way, and 3 over the last 3 months. People "
            f"have been clicking on {self.etb.name} and {self.booster.name} the most.\n\n"
            "We've got 2 of your products on there, 1 of them in stock when we last looked. If a price, stock level or "
            "link ever looks wrong, just reply and I'll put it right.\n\n"
            "We've also made a small badge that says you're listed with us, in a light and a dark version. If you "
            "fancy having it on your site, you'll find the code here: https://ripraptor.example/list-your-shop/#badge"
            "\n\nIt's completely up to you, and your listing stays just the same either way. It's lovely to have you "
            "on RipRaptor \U0001F60A\n\nThanks,\nBen Dimond\nRipRaptor, https://ripraptor.example\n\n"
            "If you'd rather I didn't email again, just reply and say so, and I won't."))
        # Nobody clicked: no shoppers sentence at all, and no line ends in a space.
        _subject, body = outreach.draft(self.other, "update", outreach.figures(self.other))
        self.assertIn("sealed trading card products.\n\nWe've got 1 of your products on there", body)
        self.assertNotIn("sent", body)
        self.assertNotIn("clicking", body)
        self.assertNotIn(" \n", body)

    def test_the_update_says_what_is_true_when_the_shoppers_came_before_or_all_within_30_days(self):
        def line(**counts):
            found = outreach.Figures(**counts, top_products=("A",) if counts.get("visitors_quarter") else ())
            return outreach.draft(self.shop, "update", found)[1].split("\n\n")[1]

        short = ("Ben here from RipRaptor (https://ripraptor.example), the UK site that compares prices on sealed "
                 "trading card products. ")
        self.assertEqual(line(visitors_month=1, visitors_quarter=1),
                         short + "In the last 30 days we've sent 1 shopper your way. People have been clicking on A the most.")
        self.assertEqual(line(visitors_month=0, visitors_quarter=7),
                         short + "In the last 3 months we've sent you 7 shoppers. People have been clicking on A the most.")
        self.assertEqual(line(visitors_month=1200, visitors_quarter=1500),
                         short + "In the last 30 days we've sent 1,200 shoppers your way, and 1,500 over the last 3 "
                                 "months. People have been clicking on A the most.")

    def test_figures_for_many_shops_are_the_same_as_for_each_in_three_queries(self):
        for days in (1, 2, 3):
            self.click(self.etb, days)
        self.click(self.booster, 10)
        self.click(self.booster, 45)
        self.click(self.tin, 60)
        self.click(self.tin, 200)
        self.click(self.spc, 1, suspect=True)
        self.click(self.etb, 1, retailer=self.other)
        with self.assertNumQueries(3):
            many = outreach.bulk_figures([self.shop.pk, self.other.pk, self.ebay.pk])
        for retailer in (self.shop, self.other, self.ebay):
            self.assertEqual(many[retailer.pk], outreach.figures(retailer), retailer.name)

    def test_the_owners_wording_is_used(self):
        for key, kind, content in (("outreach.listing.subject", "text", "Feed for {shop}"),
                                   ("outreach.optout", "text", "Reply STOP for no more.")):
            SiteContent.objects.update_or_create(key=key, defaults={"label": key, "section": "outreach", "kind": kind,
                                                                   "content": content})
        subject, body = outreach.draft(self.shop, "listing", outreach.figures(self.shop))
        self.assertEqual(subject, "Feed for Harbour Games")
        self.assertTrue(body.endswith("Reply STOP for no more."))

    def test_large_numbers_have_commas(self):
        found = outreach.Figures(visitors_month=1200, visitors_quarter=34567, visitors_total=34567, listed=1500)
        values = outreach.values(self.shop, found)
        self.assertEqual((values["visitors_month"], values["visitors_quarter"], values["listed"]),
                         ("1,200", "34,567", "1,500"))

    def test_mailto_opens_the_draft_in_the_mail_app(self):
        link = outreach.mailto("hello@harbour.example", "Shoppers & you?", "Hello,\n\nLine two\nThanks")
        parts = urlsplit(link)
        self.assertEqual((parts.scheme, parts.path), ("mailto", "hello@harbour.example"))
        query = parse_qs(parts.query)
        self.assertEqual(query["subject"], ["Shoppers & you?"])
        self.assertEqual(query["body"], ["Hello,\r\n\r\nLine two\r\nThanks"])
        self.assertNotIn(" ", link)
        self.assertTrue(outreach.mailto("", "S", "B").startswith("mailto:?subject=S"))


class MarkSentTests(OutreachTestCase):
    def setUp(self):
        outreach.ensure_contacts()
        self.contact = ShopContact.objects.get(retailer=self.shop)

    def test_mailto_without_a_body_carries_only_the_address_and_subject(self):
        self.assertEqual(outreach.mailto("hello@harbour.example", "A badge for Harbour Games"),
                         "mailto:hello@harbour.example?subject=A%20badge%20for%20Harbour%20Games")
        self.assertEqual(outreach.mailto("", "Hi"), "mailto:?subject=Hi")
        self.assertIn("&body=Hello%0D%0A%0D%0AThere", outreach.mailto("a@b.example", "Hi", "Hello\n\nThere"))

    def test_first_email_sets_emailed_and_a_follow_up(self):
        now = timezone.now()
        outreach.mark_sent(self.contact, "affiliate", "Subject", now)
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.status, ShopContact.Status.EMAILED)
        self.assertEqual(self.contact.last_emailed_at, now)
        self.assertEqual(self.contact.follow_up_on, timezone.localdate(now) + timedelta(days=14))
        email = OutreachEmail.objects.get()
        self.assertEqual((email.contact, email.kind, email.subject), (self.contact, "affiliate", "Subject"))

    def test_a_reply_or_a_live_link_keeps_its_status_and_a_later_follow_up_stays(self):
        later = timezone.localdate() + timedelta(days=60)
        for status in (ShopContact.Status.REPLIED, ShopContact.Status.AFFILIATE):
            with self.subTest(status):
                self.contact.status, self.contact.follow_up_on = status, later
                self.contact.save()
                outreach.mark_sent(self.contact, "report", "Subject")
                self.contact.refresh_from_db()
                self.assertEqual((self.contact.status, self.contact.follow_up_on), (status, later))

    def test_a_shop_that_said_no_before_is_emailed_again(self):
        self.contact.status = ShopContact.Status.DECLINED
        self.contact.follow_up_on = date(2020, 1, 1)
        self.contact.save()
        outreach.mark_sent(self.contact, "report", "Subject")
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.status, ShopContact.Status.EMAILED)
        self.assertEqual(self.contact.follow_up_on, timezone.localdate() + timedelta(days=14))

    def test_do_not_email_is_refused(self):
        self.contact.status = ShopContact.Status.DO_NOT_EMAIL
        self.contact.save()
        self.assertFalse(outreach.can_email(self.contact))
        with self.assertRaises(ValueError):
            outreach.mark_sent(self.contact, "report", "Subject")
        with self.assertRaises(ValueError):
            outreach.mark_sent(ShopContact.objects.get(retailer=self.other), "newsletter", "Subject")
        self.assertFalse(OutreachEmail.objects.exists())

    def test_follow_ups_due_are_emailed_shops_with_no_reply(self):
        today = timezone.localdate()
        ShopContact.objects.filter(retailer=self.shop).update(status=ShopContact.Status.EMAILED, follow_up_on=today)
        ShopContact.objects.filter(retailer=self.other).update(status=ShopContact.Status.REPLIED, follow_up_on=today)
        self.assertEqual(outreach.follow_ups_due(), 1)
        self.assertEqual(outreach.follow_ups_due(today - timedelta(days=1)), 0)


class AdminTests(OutreachTestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("owner", "o@example.com", "pw"))

    def change_url(self, retailer):
        return reverse("admin:inbox_shopcontact_change", args=[ShopContact.objects.get(retailer=retailer).pk])

    def test_the_list_has_a_row_for_every_shop_but_the_marketplaces(self):
        self.click(self.etb, 1)
        response = self.client.get(reverse("admin:inbox_shopcontact_changelist"))
        self.assertEqual(set(ShopContact.objects.values_list("retailer__name", flat=True)),
                         {"Harbour Games", "Quay Cards"})
        self.assertContains(response, "Harbour Games")
        self.assertContains(response, "Shoppers (3 months)")
        self.assertNotContains(response, ">eBay<")

    def test_the_shops_sending_most_come_first(self):
        self.click(self.etb, 1)
        for days in (1, 2):
            self.click(self.etb, days, retailer=self.other)
        html = self.client.get(reverse("admin:inbox_shopcontact_changelist")).content.decode()
        self.assertLess(html.index(">Quay Cards<"), html.index(">Harbour Games<"))

    def test_the_list_takes_the_same_queries_for_more_shops(self):
        url = reverse("admin:inbox_shopcontact_changelist")
        self.client.get(url)
        # The session and user, the new shops' rows, two counts, the rows with their counts, the session saved.
        with self.assertNumQueries(9):
            self.client.get(url)
        for n in range(5):
            make_listing(self.etb, make_retailer(f"Shop {n}"))
        self.client.get(url)
        with self.assertNumQueries(9):
            self.client.get(url)

    def test_the_change_page_shows_figures_drafts_and_mail_links(self):
        ShopContact.objects.update_or_create(retailer=self.shop, defaults={"email": "hello@harbour.example"})
        self.click(self.etb, 1)
        response = self.client.get(self.change_url(self.shop))
        self.assertContains(response, "Shoppers sent: 1 in the last 30 days, 1 in the last 3 months")
        self.assertContains(response, "Listed now: 2 products, 1 in stock.")
        self.assertContains(response, 'href="mailto:hello@harbour.example?subject=Do%20you%20run%20an%20affiliate%20programme%3F')
        for kind, name in outreach.DRAFTS:
            self.assertContains(response, name)
            self.assertContains(response, f'name="_sent" value="{kind}"')
        self.assertContains(response, "just reply and say so")
        self.assertNotContains(response, "No email address saved")
        # Save is the first button in the form, so Enter saves rather than marking a draft as sent.
        html = response.content.decode()
        self.assertLess(html.index('name="_save"'), html.index('name="_sent"'))

    def test_mark_as_sent_saves_the_form_and_records_the_email(self):
        outreach.ensure_contacts()
        url = self.change_url(self.shop)
        response = self.client.post(url, {"email": "hello@harbour.example", "status": "new", "follow_up_on": "",
                                          "notes": "Asked Sam", "_sent": "affiliate"}, follow=True)
        self.assertRedirects(response, url)
        contact = ShopContact.objects.get(retailer=self.shop)
        self.assertEqual((contact.email, contact.notes, contact.status),
                         ("hello@harbour.example", "Asked Sam", ShopContact.Status.EMAILED))
        self.assertEqual(contact.follow_up_on, timezone.localdate() + timedelta(days=14))
        self.assertEqual(OutreachEmail.objects.get().subject,
                         "Do you run an affiliate programme?")
        self.assertContains(response, "Marked as sent to Harbour Games. Follow up on")
        self.assertContains(response, "Ask for an affiliate link. Do you run an affiliate programme?")

    def test_saving_without_the_button_records_nothing(self):
        outreach.ensure_contacts()
        self.client.post(self.change_url(self.shop), {"email": "", "status": "replied", "notes": "", "_save": "Save"})
        self.assertFalse(OutreachEmail.objects.exists())
        self.assertEqual(ShopContact.objects.get(retailer=self.shop).status, ShopContact.Status.REPLIED)

    def test_do_not_email_shows_no_drafts_and_records_nothing(self):
        ShopContact.objects.update_or_create(retailer=self.shop, defaults={"status": ShopContact.Status.DO_NOT_EMAIL})
        url = self.change_url(self.shop)
        response = self.client.get(url)
        self.assertContains(response, "Harbour Games asked not to be emailed.")
        self.assertNotContains(response, "mailto:")
        self.assertNotContains(response, 'name="_sent"')
        response = self.client.post(url, {"email": "", "status": "do_not_email", "notes": "", "_sent": "report"},
                                    follow=True)
        self.assertContains(response, "asked not to be emailed, so nothing was recorded.")
        self.assertFalse(OutreachEmail.objects.exists())

    def test_rows_cannot_be_added_or_deleted(self):
        outreach.ensure_contacts()
        contact = ShopContact.objects.get(retailer=self.shop)
        self.assertEqual(self.client.get(reverse("admin:inbox_shopcontact_add")).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:inbox_shopcontact_delete", args=[contact.pk])).status_code, 403)

    def test_the_admin_home_says_who_to_follow_up(self):
        home = reverse("admin:index")
        self.assertContains(self.client.get(home), "Shop outreach: write to shops with their own figures")
        outreach.ensure_contacts()
        ShopContact.objects.filter(retailer=self.shop).update(status=ShopContact.Status.EMAILED,
                                                              follow_up_on=timezone.localdate())
        response = self.client.get(home)
        self.assertContains(response, "1 shop to follow up")
        self.assertContains(response, reverse("admin:inbox_shopcontact_changelist") + "?follow_up=due")
        listed = self.client.get(reverse("admin:inbox_shopcontact_changelist") + "?follow_up=due")
        self.assertContains(listed, "1 shop outreach")

    def test_nothing_is_sent_from_the_site(self):
        from catalogue import mail

        outreach.ensure_contacts()
        with mock.patch.object(mail, "send") as send:
            self.client.post(self.change_url(self.shop), {"email": "hello@harbour.example", "status": "new",
                                                          "notes": "", "_sent": "report"})
        send.assert_not_called()
        self.assertEqual(OutreachEmail.objects.count(), 1)


@override_settings(RIPRAPTOR_SITE_URL="https://ripraptor.example")
class UpdateEmailsPageTests(OutreachTestCase):
    """The Update and badge emails page: one email per shop worth writing to, to send from the owner's own mailbox."""

    def setUp(self):
        self.owner = get_user_model().objects.create_superuser("owner", "owner@example.com", "pw")
        self.client.force_login(self.owner)
        self.url = reverse("admin:inbox_shopcontact_update_emails")

    def contact(self, shop):
        return ShopContact.objects.get(retailer=shop)

    def test_the_shop_list_links_to_it(self):
        response = self.client.get(reverse("admin:inbox_shopcontact_changelist"))
        self.assertContains(response, f'<a href="{self.url}">Update and badge emails</a>')

    def test_every_shop_with_something_listed_has_its_email_the_biggest_senders_first(self):
        for days in (1, 2):
            self.click(self.etb, days, retailer=self.other)
        self.click(self.etb, 1)
        response = self.client.get(self.url)
        html = response.content.decode()
        self.assertContains(response, "2 shops here, 2 with no address saved yet.")
        self.assertContains(response, "A quick hello from RipRaptor")
        self.assertContains(response, "Hi all at Harbour Games,")
        self.assertContains(response, "https://ripraptor.example/list-your-shop/#badge")
        self.assertNotContains(response, "eBay")
        self.assertLess(html.index("Quay Cards</h2>"), html.index("Harbour Games</h2>"))
        # Each shop has its own form: Save address is the first button so Enter saves rather than marks as sent.
        for shop in (self.shop, self.other):
            form = html[html.index(f'id="shop-{self.contact(shop).pk}"'):]
            form = form[:form.index("</form>")]
            self.assertLess(form.index('name="_save"'), form.index('name="_sent"'))
            self.assertIn("csrfmiddlewaretoken", form)
            self.assertIn('href="mailto:?subject=A%20quick%20hello%20from%20RipRaptor', form)

    def test_shops_with_nothing_listed_or_that_asked_not_to_be_emailed_are_left_out(self):
        nothing = make_retailer("Empty Cards")
        gone = make_retailer("Closed Cards", is_active=False)
        make_listing(self.etb, gone)
        outreach.ensure_contacts()
        ShopContact.objects.filter(retailer=self.other).update(status=ShopContact.Status.DO_NOT_EMAIL)
        response = self.client.get(self.url)
        self.assertContains(response, "1 shop here")
        self.assertContains(response, "Harbour Games</h2>")
        for name in (nothing.name, gone.name, "Quay Cards"):
            self.assertNotContains(response, f"{name}</h2>")

    def test_a_saved_address_fills_in_the_mail_link(self):
        outreach.ensure_contacts()
        contact = self.contact(self.shop)
        response = self.client.post(self.url, {"contact": contact.pk, "email": " hello@harbour.example ", "_save": "1"},
                                    follow=True)
        self.assertRedirects(response, f"{self.url}#shop-{contact.pk}", fetch_redirect_response=False)
        self.assertEqual(self.contact(self.shop).email, "hello@harbour.example")
        self.assertFalse(OutreachEmail.objects.exists())
        self.assertContains(response, "Saved the address for Harbour Games.")
        self.assertContains(response, 'href="mailto:hello@harbour.example?subject=A%20quick%20hello')
        self.assertContains(response, "1 with no address saved yet")

    def test_a_bad_address_is_refused_and_kept_out(self):
        outreach.ensure_contacts()
        contact = self.contact(self.shop)
        response = self.client.post(self.url, {"contact": contact.pk, "email": "not an address", "_sent": "update"},
                                    follow=True)
        self.assertContains(response, "that email address does not look right, so nothing was saved.")
        self.assertEqual(self.contact(self.shop).email, "")
        self.assertFalse(OutreachEmail.objects.exists())

    def test_mark_as_sent_records_it_and_the_shop_drops_off_the_list(self):
        outreach.ensure_contacts()
        contact = self.contact(self.shop)
        response = self.client.post(self.url, {"contact": contact.pk, "email": "hello@harbour.example",
                                               "_sent": "update"}, follow=True)
        self.assertRedirects(response, self.url)
        self.assertContains(response, "Marked as sent to Harbour Games. Follow up on")
        contact = self.contact(self.shop)
        self.assertEqual((contact.email, contact.status, contact.follow_up_on),
                         ("hello@harbour.example", ShopContact.Status.EMAILED, timezone.localdate() + timedelta(days=14)))
        sent = OutreachEmail.objects.get()
        self.assertEqual((sent.kind, sent.subject), ("update", "A quick hello from RipRaptor"))
        self.assertNotContains(response, "Harbour Games</h2>")
        self.assertContains(response, "Quay Cards</h2>")
        # Show the ones already sent too, with when.
        shown = self.client.get(self.url, {"show": "all"})
        self.assertContains(shown, "Harbour Games</h2>")
        self.assertContains(shown, f"update sent {timezone.localdate():%-d %b %Y}")
        self.assertContains(shown, "Hide the ones already sent")
        # Every shop done: it says so.
        self.client.post(self.url, {"contact": self.contact(self.other).pk, "email": "", "_sent": "update"})
        self.assertContains(self.client.get(self.url), "Nobody left to email.")

    def test_a_shop_that_asked_not_to_be_emailed_records_nothing_even_if_posted(self):
        outreach.ensure_contacts()
        ShopContact.objects.filter(retailer=self.shop).update(status=ShopContact.Status.DO_NOT_EMAIL)
        response = self.client.post(self.url, {"contact": self.contact(self.shop).pk, "email": "", "_sent": "update"},
                                    follow=True)
        self.assertContains(response, "asked not to be emailed, so nothing was recorded.")
        self.assertFalse(OutreachEmail.objects.exists())

    def test_an_unknown_shop_is_a_404_and_only_staff_get_in(self):
        self.assertEqual(self.client.post(self.url, {"contact": "999999", "_save": "1"}).status_code, 404)
        self.assertEqual(self.client.post(self.url, {"contact": "x", "_save": "1"}).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(get_user_model().objects.create_user("clerk", password="pw", is_staff=True))
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_the_page_takes_the_same_queries_for_more_shops(self):
        def count():
            from django.db import connection
            from django.test.utils import CaptureQueriesContext

            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get(self.url).status_code, 200)
            return len(queries)

        self.client.get(self.url)   # makes the rows
        before = count()
        for n in range(8):
            shop = make_retailer(f"Extra Cards {n}")
            make_listing(self.etb, shop)
            self.click(self.etb, 1, retailer=shop)
        self.client.get(self.url)
        self.assertEqual(count(), before)

    def test_nothing_is_sent_from_the_site(self):
        from django.core import mail

        outreach.ensure_contacts()
        self.client.post(self.url, {"contact": self.contact(self.shop).pk, "email": "hello@harbour.example",
                                    "_sent": "update"})
        self.assertEqual(mail.outbox, [])


class CopyBeforeOpeningTests(OutreachTestCase):
    """Some mail apps (Zoho Mail on Android) run a filled-in message's lines together, so Open in your mail app
    copies the message and opens the app with only the address and subject: the owner pastes it, line breaks kept."""

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("owner", "owner@example.com", "pw"))
        outreach.ensure_contacts()
        ShopContact.objects.filter(retailer=self.shop).update(email="hello@harbour.example")

    def test_every_draft_on_a_shops_page_copies_its_message_first(self):
        contact = ShopContact.objects.get(retailer=self.shop)
        html = self.client.get(reverse("admin:inbox_shopcontact_change", args=[contact.pk])).content.decode()
        self.assertIn("js/copy-mail.js", html)
        for kind, _name in outreach.DRAFTS:
            with self.subTest(kind):
                self.assertIn(f'<textarea readonly id="body-{kind}"', html)
                link = html[html.index(f'data-copy-from="body-{kind}"'):]
                link = link[:link.index(">")]
                # The link itself still carries the whole message; the short one has the address and subject only.
                self.assertRegex(link, r'data-short="mailto:hello@harbour\.example\?subject=[^"&]+"')
                self.assertNotIn("body=", link[link.index("data-short"):])
        self.assertIn("press and hold in the empty message and choose Paste", html)

    def test_the_all_shops_page_does_too(self):
        html = self.client.get(reverse("admin:inbox_shopcontact_update_emails")).content.decode()
        contact = ShopContact.objects.get(retailer=self.shop)
        self.assertIn("js/copy-mail.js", html)
        self.assertIn(f'<textarea readonly id="body-{contact.pk}"', html)
        link = html[html.index(f'data-copy-from="body-{contact.pk}"'):]
        link = link[:link.index(">")]
        self.assertRegex(link, r'data-short="mailto:hello@harbour\.example\?subject=A%20quick%20hello[^"&]*"')
        self.assertIn("press and hold in the empty message and choose <strong>Paste</strong>", html)

    def test_the_script_copies_then_opens_and_falls_back_to_the_full_link(self):
        from pathlib import Path

        from django.conf import settings

        script = (Path(settings.BASE_DIR) / "web" / "static" / "js" / "copy-mail.js").read_text()
        self.assertIn("navigator.clipboard.writeText(box.value)", script)
        self.assertIn("window.location.href = link.dataset.short", script)
        self.assertIn("window.location.href = link.href", script)
