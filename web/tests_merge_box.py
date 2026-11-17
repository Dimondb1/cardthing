"""Merge two products (web/merge_box.py), where a switched-off page's address leads (the product form in Admin) and
what a switched-off page answers (views.switched_off). The owner, 17 Nov 2026, on an audit that found the same One
Piece box on two pages with two prices and an address Google had shown answering not found: "Approve, add merge
box"."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from catalogue import addresses
from catalogue.models import CheckAnswer, DailyLowestPrice, Listing, NewPage, Product, ProductAlias, RemovedProduct
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

BOX = Product.Type.BOOSTER_BOX
PACK = Product.Type.BOOSTER_PACK


@override_settings(RIPRAPTOR_TWO_STEP_REQUIRED=False)
class MergeBoxTests(TestCase):
    def setUp(self):
        cache.clear()
        self.game = make_game(name="One Piece Card Game", slug="one-piece")
        self.set = make_set(self.game, name="One Piece Heroines Edition Vol.2", slug="heroines-edition-vol2", code="EB-05")
        self.keep = make_product(self.set, name="EB05 Heroines Edition Vol.2 Booster Box",
                                 slug="eb05-heroines-edition-vol2-booster-box", product_type=BOX)
        self.other = make_product(self.set, name="One Piece Heroines Edition Vol.2 Booster Box Extra Booster",
                                  slug="one-piece-heroines-edition-vol2-booster-box-extra-booster", product_type=BOX)
        self.harbour, self.tollgate, self.vault = (make_retailer(name) for name in ("Harbour Games", "Tollgate TCG", "The Card Vault"))
        make_listing(self.keep, self.harbour, price="119.95")
        make_listing(self.other, self.tollgate, price="199.00", delivery="5.99")
        make_listing(self.other, self.vault, price="234.99")
        self.url = reverse("merge_products")
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="x"))

    def show(self, keep, other):
        return self.client.get(self.url, {"keep": keep, "other": other})

    def merge(self, keep, other, **extra):
        return self.client.post(self.url, {"keep": keep, "other": other, **extra}, follow=True)

    def test_an_address_is_read_from_a_link_a_path_or_its_last_part(self):
        slug = self.keep.slug
        for pasted in (f"https://ripraptor.com/products/{slug}/", f"https://ripraptor.com/products/{slug}/?ask=1#offers",
                       f"/products/{slug}/", f"products/{slug}", slug, f"  {slug.upper()}  "):
            with self.subTest(pasted=pasted):
                self.assertEqual(addresses.slug_of(pasted), slug)
        for pasted in ("", "   ", "https://ripraptor.com/", "not a slug", "/products/"):
            with self.subTest(pasted=pasted):
                self.assertEqual(addresses.slug_of(pasted), "")

    def test_it_shows_both_pages_side_by_side_and_changes_nothing(self):
        html = self.show(f"https://ripraptor.com/products/{self.keep.slug}/", self.other.slug).content.decode()
        self.assertIn("EB05 Heroines Edition Vol.2 Booster Box", html)
        self.assertIn("One Piece Heroines Edition Vol.2 Booster Box Extra Booster", html)
        self.assertIn("1 with a price now, 1 listing it", html)
        self.assertIn("2 with a price now, 2 listing it", html)
        self.assertIn("£119.95 delivered", html)
        self.assertIn("£204.99 delivered", html)
        self.assertIn(f'value="Merge into {self.keep.name}"', html)
        self.assertEqual(Product.objects.filter(is_active=True).count(), 2)
        self.assertFalse(ProductAlias.objects.exists())

    def test_two_names_that_do_not_say_one_product_need_the_owners_tick(self):
        # Any two addresses can be pasted: another set's box is plainly said to be another product.
        other_set = make_set(self.game, name="Two Legends", slug="two-legends", code="OP-08")
        legends = make_product(other_set, name="Two Legends Booster Box", slug="two-legends-booster-box", product_type=BOX)
        html = self.show(self.keep.slug, legends.slug).content.decode()
        self.assertIn("Look at these first:", html)
        self.assertRegex(html, r"Their names do not say the same product to the site\.|The site&#x27;s own rules would not merge them")
        self.merge(self.keep.slug, legends.slug)
        legends.refresh_from_db()
        self.assertTrue(legends.is_active)

    def test_merging_moves_the_shops_and_history_and_leaves_a_redirect_with_undo(self):
        from django.utils import timezone

        DailyLowestPrice.objects.create(product=self.other, date=timezone.localdate(), price="204.99")
        # One Piece's "Extra Booster" names its EB sets and shops add it or leave it out, so the rules see these
        # two names as one product and no tick is asked for.
        response = self.merge(self.keep.slug, self.other.slug)
        self.assertContains(response, "Merged One Piece Heroines Edition Vol.2 Booster Box Extra Booster into "
                                      "EB05 Heroines Edition Vol.2 Booster Box.")
        self.assertEqual(set(Listing.objects.filter(product=self.keep).values_list("retailer__name", flat=True)),
                         {"Harbour Games", "Tollgate TCG", "The Card Vault"})
        self.other.refresh_from_db()
        self.assertFalse(self.other.is_active)
        old = self.client.get(f"/products/{self.other.slug}/")
        self.assertEqual((old.status_code, old["Location"]), (301, self.keep.get_absolute_url()))
        # Listed under Sorted for you as the owner's own merge, which Things to check can undo.
        answer = CheckAnswer.objects.get(kind=CheckAnswer.Kind.MERGE)
        self.assertTrue(answer.by_owner)
        self.assertEqual((answer.product, answer.other, answer.why), (self.keep, self.other, "You merged them by address"))

    def test_two_languages_or_two_games_are_never_merged(self):
        japanese = make_product(self.set, name="EB-05 Heroines Edition Vol.2 Japanese Booster Box",
                                slug="eb-05-heroines-edition-vol2-japanese-booster-box", product_type=BOX)
        html = self.show(self.keep.slug, japanese.slug).content.decode()
        self.assertIn("Cannot be done", html)
        self.assertIn("They are in different languages: English and Japanese. Each language keeps its own page.", html)
        self.assertNotIn('value="Merge into', html)
        self.merge(self.keep.slug, japanese.slug, same="yes")
        japanese.refresh_from_db()
        self.assertTrue(japanese.is_active)
        self.assertFalse(ProductAlias.objects.exists())
        magic = make_set(make_game(name="Magic: The Gathering", slug="magic-the-gathering"), name="Origins", slug="origins", code="ORI")
        elsewhere = make_product(magic, name="Origins Booster Box", slug="origins-booster-box", product_type=BOX)
        self.assertContains(self.show(self.keep.slug, elsewhere.slug), "They are in different games")
        self.assertContains(self.show(self.keep.slug, self.keep.slug), "Both addresses are the same product.")

    def test_a_box_and_a_pack_need_the_owners_tick(self):
        pack = make_product(self.set, name="EB05 Heroines Edition Vol.2 Booster Pack",
                            slug="eb05-heroines-edition-vol2-booster-pack", product_type=PACK)
        make_listing(pack, self.harbour, price="4.99")
        html = self.show(self.keep.slug, pack.slug).content.decode()
        self.assertIn("They are filed as different kinds:", html)
        self.assertIn("1 shop lists both.", html)
        self.assertIn("I have looked at both pages. They are the same product.", html)
        self.assertContains(self.merge(self.keep.slug, pack.slug), "Tick that they are the same product first, then merge.")
        pack.refresh_from_db()
        self.assertTrue(pack.is_active)
        self.merge(self.keep.slug, pack.slug, same="yes")
        pack.refresh_from_db()
        self.assertFalse(pack.is_active)

    def test_a_switched_off_page_only_sends_its_address_on(self):
        # The address Google had shown: the same Japanese pack lives at another address.
        live = make_product(self.set, name="EB 05 Heroines Edition Vol.2 Japanese Booster Pack",
                            slug="eb-05-heroines-edition-vol2-japanese-booster-pack", product_type=PACK)
        dead = make_product(self.set, name="EB05 Heroines Edition Vol.2 Japanese Booster Pack",
                            slug="eb05-heroines-edition-vol2-japanese-booster-pack", product_type=PACK, is_active=False)
        make_listing(dead, self.vault, price="4.00")
        html = self.show(live.slug, dead.slug).content.decode()
        self.assertIn("No, switched off", html)
        self.assertIn("only the address moves", html)
        self.assertIn(f'value="Send this address to {live.name}"', html)
        response = self.merge(live.slug, f"https://ripraptor.com/products/{dead.slug}/")
        self.assertContains(response, f"/products/{dead.slug}/ now leads to /products/{live.slug}/.")
        old = self.client.get(f"/products/{dead.slug}/")
        self.assertEqual((old.status_code, old["Location"]), (301, live.get_absolute_url()))
        # Its shops and prices stay where they are, off the site, and nothing is listed as a merge.
        self.assertEqual(Listing.objects.filter(product=live).count(), 0)
        self.assertFalse(CheckAnswer.objects.filter(kind=CheckAnswer.Kind.MERGE).exists())

    def test_an_address_that_is_no_product_at_all_can_be_sent_on(self):
        RemovedProduct.objects.create(slug="heroines-edition-vol2-extra-booster-box", name="Heroines", game=self.game)
        html = self.show(self.keep.slug, "heroines-edition-vol2-extra-booster-box").content.decode()
        self.assertIn("No product is at this address now.", html)
        self.merge(self.keep.slug, "heroines-edition-vol2-extra-booster-box")
        old = self.client.get("/products/heroines-edition-vol2-extra-booster-box/")
        self.assertEqual((old.status_code, old["Location"]), (301, self.keep.get_absolute_url()))

    def test_the_page_to_keep_must_be_on_the_site(self):
        Product.objects.filter(pk=self.keep.pk).update(is_active=False)
        self.assertContains(self.show(self.keep.slug, self.other.slug), "is switched off. Keep the page that is on the site.")
        self.assertContains(self.show("no-such-product", self.other.slug), "No product is at /products/no-such-product/.")
        self.assertContains(self.show(self.other.slug, ""), "Paste both addresses")
        self.merge(self.keep.slug, self.other.slug)
        self.other.refresh_from_db()
        self.assertTrue(self.other.is_active)

    def test_an_old_address_is_followed_to_the_page_it_leads_to(self):
        ProductAlias.objects.create(slug="heroines-box-old", product=self.other)
        html = self.show(self.keep.slug, "heroines-box-old").content.decode()
        self.assertIn("/products/heroines-box-old/ is an old address that leads to this page.", html)
        self.merge(self.keep.slug, "heroines-box-old", same="yes")
        self.other.refresh_from_db()
        self.assertFalse(self.other.is_active)
        self.assertEqual(ProductAlias.objects.get(slug="heroines-box-old").product, self.keep)
        # And one that already leads to the kept page has nothing left to do.
        self.assertContains(self.show(self.keep.slug, "heroines-box-old"), "already leads to EB05 Heroines Edition Vol.2 Booster Box.")

    def test_it_is_for_staff_only_never_indexed_and_linked_from_admin(self):
        response = self.show("", "")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertContains(self.client.get(reverse("admin:index")), f'href="{self.url}"')
        self.assertContains(self.client.get(reverse("checks")), "Merge two products by their addresses")
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.post(self.url, {"keep": self.keep.slug, "other": self.other.slug}).status_code, 302)
        self.other.refresh_from_db()
        self.assertTrue(self.other.is_active)


@override_settings(RIPRAPTOR_TWO_STEP_REQUIRED=False)
class SwitchedOffTests(TestCase):
    """A page that was on the site and is switched off: its address leads to the page the owner names, or says the
    page has gone, never a bare not found."""

    def setUp(self):
        cache.clear()
        self.game = make_game()
        self.set = make_set(self.game)
        self.etb = make_product(self.set, slug="prismatic-evolutions-elite-trainer-box")
        self.twin = make_product(self.set, name="Prismatic Evolutions ETB", slug="prismatic-evolutions-etb")
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="x"))

    def test_a_switched_off_page_says_it_has_gone_with_its_set_and_game(self):
        Product.objects.filter(pk=self.twin.pk).update(is_active=False)
        response = self.client.get("/products/prismatic-evolutions-etb/")
        self.assertEqual(response.status_code, 410)
        html = response.content.decode()
        self.assertIn("Prismatic Evolutions ETB is no longer listed", html)
        self.assertIn(f'href="{self.set.get_absolute_url()}"', html)
        self.assertIn(f'href="{self.game.get_absolute_url()}"', html)

    def test_a_page_still_waiting_for_its_first_check_stays_not_found(self):
        Product.objects.filter(pk=self.twin.pk).update(is_active=False)
        page = NewPage.objects.create(product=self.twin, status=NewPage.Status.WAITING)
        self.assertEqual(self.client.get("/products/prismatic-evolutions-etb/").status_code, 404)
        NewPage.objects.filter(pk=page.pk).update(status=NewPage.Status.UNSURE)
        self.assertEqual(self.client.get("/products/prismatic-evolutions-etb/").status_code, 404)
        # Decided not to be a sealed product: gone.
        NewPage.objects.filter(pk=page.pk).update(status=NewPage.Status.NOT_SEALED)
        self.assertEqual(self.client.get("/products/prismatic-evolutions-etb/").status_code, 410)
        self.assertEqual(self.client.get("/products/never-existed/").status_code, 404)

    def test_a_page_whose_game_is_switched_off_is_not_gone(self):
        from catalogue.models import Game

        Game.objects.filter(pk=self.game.pk).update(is_active=False)
        self.assertEqual(self.client.get(self.etb.get_absolute_url()).status_code, 404)

    def form(self, product, **changes):
        data = {
            "name": product.name, "slug": product.slug, "game": product.game_id, "product_set": product.product_set_id,
            "product_type": product.product_type, "image_url": "", "ean": "", "amazon_asin": "", "release_date": "",
            "rrp": "", "packs": "", "cards_per_pack": "", "send_to": "",
            "listings-TOTAL_FORMS": "0", "listings-INITIAL_FORMS": "0", "listings-MIN_NUM_FORMS": "0",
            "listings-MAX_NUM_FORMS": "1000",
        }
        if product.is_active:
            data["is_active"] = "on"
        data.update(changes)
        for key in [key for key, value in data.items() if value is None]:
            del data[key]
        return self.client.post(reverse("admin:catalogue_product_change", args=[product.pk]), data)

    def test_switching_a_page_off_in_admin_can_send_its_address_to_the_live_page(self):
        response = self.form(self.twin, is_active=None, send_to=f"https://ripraptor.com{self.etb.get_absolute_url()}")
        self.assertEqual(response.status_code, 302, response.content.decode()[:2000] if response.status_code != 302 else "")
        self.twin.refresh_from_db()
        self.assertFalse(self.twin.is_active)
        old = self.client.get("/products/prismatic-evolutions-etb/")
        self.assertEqual((old.status_code, old["Location"]), (301, self.etb.get_absolute_url()))
        # The form says where the address leads now.
        page = self.client.get(reverse("admin:catalogue_product_change", args=[self.twin.pk])).content.decode()
        self.assertIn("Switched off. Its address leads to Prismatic Evolutions Elite Trainer Box", page)
        # Switched on again, its address is its own.
        self.twin.refresh_from_db()
        self.form(self.twin, is_active="on")
        self.assertEqual(self.client.get("/products/prismatic-evolutions-etb/").status_code, 200)
        self.assertFalse(ProductAlias.objects.filter(slug="prismatic-evolutions-etb").exists())

    def test_the_address_to_send_to_must_be_a_live_page_and_only_when_switching_off(self):
        on_site = self.form(self.twin, send_to=self.etb.slug)
        self.assertContains(on_site, "Untick Show on site first, or leave this empty.")
        missing = self.form(self.twin, is_active=None, send_to="no-such-product")
        self.assertContains(missing, "No page on the site has that address.")
        itself = self.form(self.twin, is_active=None, send_to=self.twin.slug)
        self.assertContains(itself, "That is this page&#x27;s own address.")
        self.twin.refresh_from_db()
        self.assertTrue(self.twin.is_active)
        self.assertFalse(ProductAlias.objects.exists())
        # Left empty, switching off sends it nowhere: the address says the page has gone.
        self.form(self.twin, is_active=None)
        self.assertEqual(self.client.get("/products/prismatic-evolutions-etb/").status_code, 410)
