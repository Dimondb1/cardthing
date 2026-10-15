"""The owner's spreadsheet made and undone: each kind of change leaves what his own button leaves, and still
stands after the hourly tidy-up and the shop's next read; the file is made in order, a change the site
changed since he checked is left alone, and a run cut short carries on without making anything twice."""

import fcntl
import hashlib
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from . import checks, data_jobs, judge, owner_edits, types, worker
from .importers import Offer, apply_offers
from .models import (
    CheckAnswer, ClaudeAsk, DailyLowestPrice, DataChange, DataExport, DataImport, Listing, Product, ProductAlias,
    ProductSet, ShopPage, ShopProduct,
)
from .testing import excel_resave, make_listing, make_product, make_retailer
from .tests_owner_import import Base as PlanBase

Kind = DataChange.Kind
State = DataChange.State
Outcome = DataChange.Outcome
Upload = DataImport.Status
Status = ShopProduct.Status


class Base(PlanBase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_superuser("owner", "owner@example.com", "pw")
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        # Test processes share one cache folder, so each test has its own runner lock and import lock: a runner
        # in another process must never hold this test's.
        settings = override_settings(RIPRAPTOR_CACHE_DIR=folder.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.import_lock = str(Path(folder.name) / "import.lock")
        patcher = mock.patch.object(worker, "IMPORT_LOCK", self.import_lock)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The test database is in memory: there is nothing to copy.
        patcher = mock.patch.object(data_jobs, "call_command")
        self.backup = patcher.start()
        self.addCleanup(patcher.stop)

    def upload(self, data):
        """An upload as the page takes it, checked by the runner."""
        upload = DataImport.objects.create(created_by=self.owner, file_name="data.xlsx", size_bytes=len(data),
                                           sha256=hashlib.sha256(data).hexdigest(), upload=data)
        call_command("data_files")
        upload.refresh_from_db()
        return upload

    def apply(self, upload, include_held=False):
        """His Make changes button, then the runner."""
        DataImport.objects.filter(pk=upload.pk).update(status=Upload.APPLY_QUEUED, include_held=include_held)
        call_command("data_files")
        upload.refresh_from_db()
        return upload

    def undo(self, upload):
        DataImport.objects.filter(pk=upload.pk).update(status=Upload.UNDO_QUEUED)
        call_command("data_files")
        upload.refresh_from_db()
        return upload

    def make(self, data, *edits, include_held=False, **options):
        """Edit the file as Excel saves it, upload it, check it and make its changes."""
        upload = self.upload(excel_resave(data, edits, **options))
        self.assertEqual(upload.status, Upload.CHECKED, upload.error)
        upload = self.apply(upload, include_held)
        self.assertEqual(upload.status, Upload.APPLIED)
        return upload

    def changes(self, upload, **filters):
        return list(upload.changes.filter(**filters).order_by("step", "seq"))

    def made(self, upload, kind=None):
        """The one change of this kind the file made."""
        found = self.changes(upload, **({"kind": kind} if kind else {}))
        self.assertEqual(len(found), 1, [(c.kind, c.state, c.outcome, c.what, c.why, c.outcome_note) for c in found])
        change = found[0]
        self.assertEqual(change.outcome, Outcome.APPLIED, (change.what, change.why, change.outcome_note))
        return change

    def tidy(self):
        call_command("tidy_all", stdout=StringIO())

    def answered(self, row_key, kind=ClaudeAsk.Kind.PAIR):
        return ClaudeAsk.objects.create(kind=kind, row_key=row_key, fingerprint="f", model_asked="m", effort="low",
                                        outcome=ClaudeAsk.Outcome.ANSWERED, verdict="same")


class ProductEditTests(Base):
    def test_a_rename(self):
        _export, data = self.download()
        upload = self.make(data, ("Products", self.etb.pk, "Name", "Surging Sparks ETB!"))
        self.made(upload)
        etb = Product.objects.get(pk=self.etb.pk)
        # Tidied as the hourly tidy-up tidies it, so it is never renamed again; its address stays.
        self.assertEqual((etb.name, etb.slug), ("Surging Sparks ETB", "ssp-etb"))
        entry = LogEntry.objects.get(object_id=str(etb.pk))
        self.assertEqual((entry.user, entry.action_flag), (self.owner, CHANGE))
        self.assertIn("Name", entry.get_change_message())
        self.tidy()
        self.assertEqual(Product.objects.get(pk=self.etb.pk).name, "Surging Sparks ETB")

    def test_renames_the_hourly_merge_would_join_are_not_made(self):
        twin = make_product(self.set, name="Surging Sparks Booster Bundle", slug="ssp-bundle", product_type="bundle",
                            release_date=date(2024, 11, 8))
        Product.objects.filter(pk=self.etb.pk).update(product_type="bundle")
        _export, data = self.download()
        upload = self.make(data, ("Products", self.etb.pk, "Name", "Surging Sparks: Booster Bundle"),
                           ("Products", self.box.pk, "Name", "Surging Sparks Booster Bundle"))
        self.assertEqual({(c.state, c.outcome) for c in self.changes(upload)}, {(State.PROBLEM, Outcome.NONE)})
        self.tidy()
        self.assertTrue(set(Product.objects.values_list("name", flat=True)).issuperset({
            "Surging Sparks Elite Trainer Box", "Surging Sparks Booster Box", twin.name}))

    def test_a_type_is_kept_from_the_hourly_retype(self):
        _export, data = self.download()
        self.made(self.make(data, ("Products", self.etb.pk, "Type", "Booster bundle")))
        self.assertEqual(Product.objects.get(pk=self.etb.pk).product_type, "bundle")
        entry = LogEntry.objects.get(object_id=str(self.etb.pk))
        self.assertIn("Product type", entry.change_message)
        self.assertIn(self.etb.pk, types.owner_typed())
        # The name says Elite Trainer Box, so without his LogEntry the hourly retype would put it back.
        self.tidy()
        self.assertEqual(Product.objects.get(pk=self.etb.pk).product_type, "bundle")

    def test_a_set_and_a_set_added_in_the_same_file(self):
        _export, data = self.download()
        upload = self.make(
            data, ("Products", self.etb.pk, "Set", "Destined Rivals"),
            insert_rows=[("Sets", {"Set id": "new1", "Game": "Pokémon", "Name": "Destined Rivals", "Code": "DRI",
                                   "Release date": "30/05/2025"})])
        added = self.made(upload, Kind.NEW_SET)
        rivals = ProductSet.objects.get(name="Destined Rivals")
        self.assertEqual((added.object_id, rivals.code, rivals.release_date, rivals.release_date_source),
                         (rivals.pk, "DRI", date(2025, 5, 30), "owner"))
        self.made(upload, Kind.PRODUCT_FIELDS)
        self.assertEqual(Product.objects.get(pk=self.etb.pk).product_set, rivals)
        self.tidy()
        self.assertEqual(Product.objects.get(pk=self.etb.pk).product_set, rivals)

    def test_a_set_of_another_game_and_a_clear_the_hourly_filing_undoes_are_not_made(self):
        _export, data = self.download()
        upload = self.make(data, ("Products", self.etb.pk, "Set", "Foundations"),
                           ("Products", self.box.pk, "Set", ""))
        self.assertEqual({c.state for c in self.changes(upload)}, {State.PROBLEM})
        self.assertEqual(set(Product.objects.filter(pk__in=[self.etb.pk, self.box.pk]).values_list("product_set", flat=True)),
                         {self.set.pk})

    def test_a_language_goes_in_the_name_and_stays(self):
        _export, data = self.download()
        self.made(self.make(data, ("Products", self.etb.pk, "Language", "Japanese")))
        etb = Product.objects.get(pk=self.etb.pk)
        self.assertEqual((etb.name, etb.language), ("Surging Sparks Elite Trainer Box (Japanese)", "ja"))
        self.tidy()
        self.assertEqual(Product.objects.get(pk=self.etb.pk).language, "ja")

    def test_a_barcode(self):
        _export, data = self.download()
        upload = self.make(data, ("Products", self.etb.pk, "Barcode", "5053222123458"),
                           ("Products", self.etb.pk, "Release date", "15/11/2026"))
        self.assertEqual({c.outcome for c in self.changes(upload)}, {Outcome.APPLIED})
        etb = Product.objects.get(pk=self.etb.pk)
        self.assertEqual((etb.ean, etb.release_date), ("5053222123458", date(2026, 11, 15)))
        taken = self.make(data, ("Products", self.box.pk, "Barcode", "5053222123458"))
        self.assertEqual(self.changes(taken)[0].state, State.PROBLEM)
        self.tidy()
        self.assertEqual(Product.objects.get(pk=self.etb.pk).ean, "5053222123458")
        self.assertEqual(Product.objects.get(pk=self.box.pk).ean, "0820650851247")


class NewProductTests(Base):
    def test_a_new_row_named_by_a_merge_a_move_and_a_link(self):
        item = ShopProduct.objects.create(retailer=self.harbour, title="SSP Tin", url="https://harbour.example/p/tin",
                                          price=Decimal("12.99"), status=Status.REVIEW, last_seen=timezone.now())
        tin = make_product(self.set, name="Surging Sparks Tin Pokemon Centre", product_type="tin",
                           release_date=date(2024, 11, 8))
        wrong = make_listing(tin, self.shop, url="https://solent.example/products/777")
        _export, data = self.download()
        upload = self.make(
            data, ("Shop items", item.pk, "Link to product id", "new1"), ("Listings", wrong.pk, "Product id", "new1"),
            ("Products", tin.pk, "Same as product id", "new1"),
            insert_rows=[("Products", {"Product id": "new1", "Game": "Pokémon", "Name": "Surging Sparks Mini Tin",
                                       "Type": "Tin"})])
        self.assertEqual({c.outcome for c in self.changes(upload)}, {Outcome.APPLIED},
                         [(c.what, c.outcome_note) for c in self.changes(upload)])
        added = Product.objects.get(name="Surging Sparks Mini Tin")
        self.assertEqual(self.made(upload, Kind.NEW_PRODUCT).object_id, added.pk)
        self.assertEqual((added.slug, added.product_type, added.game, added.is_active),
                         ("surging-sparks-mini-tin", "tin", self.game, True))
        self.assertEqual(LogEntry.objects.get(object_id=str(added.pk), action_flag=ADDITION).user, self.owner)
        self.assertEqual(Listing.objects.get(pk=wrong.pk).product, added)
        self.assertEqual(Listing.objects.get(retailer=self.harbour, url=item.url).product, added)
        self.assertEqual(CheckAnswer.objects.get(kind=CheckAnswer.Kind.MERGE).product, added)
        self.assertFalse(Product.objects.get(pk=tin.pk).is_active)
        # It has shops now, so the hourly tidy-up keeps it though it has no release date.
        self.tidy()
        self.assertTrue(Product.objects.get(pk=added.pk).is_active)

    def test_a_link_to_a_new_row_that_was_not_added_is_not_made(self):
        item = ShopProduct.objects.create(retailer=self.harbour, title="SSP Tin", url="https://harbour.example/p/tin",
                                          price=Decimal("12.99"), status=Status.REVIEW, last_seen=timezone.now())
        _export, data = self.download()
        upload = self.upload(excel_resave(data, [("Shop items", item.pk, "Link to product id", "new1")], insert_rows=[(
            "Products", {"Product id": "new1", "Game": "Pokémon", "Name": "Surging Sparks Mini Tin", "Type": "Tin"})]))
        with mock.patch.object(owner_edits, "create_product", side_effect=owner_edits.Refused("Not today.")):
            upload = self.apply(upload)
        notes = [(c.kind, c.outcome, c.outcome_note) for c in self.changes(upload)]
        self.assertEqual(notes, [(Kind.NEW_PRODUCT, Outcome.SKIPPED, "Not today."), (Kind.LINK, Outcome.SKIPPED,
                                  "The new product new1 was not added, so this was not made either.")])
        self.assertEqual(ShopProduct.objects.get(pk=item.pk).status, Status.REVIEW)

    def test_two_uploads_of_one_download_add_its_new_row_once(self):
        _export, data = self.download()
        new_row = ("Products", {"Product id": "new1", "Game": "Pokémon", "Name": "Black Bolt Elite Trainer Box",
                                "Type": "Elite Trainer Box", "Release date": "18/07/2025"})
        first = self.upload(excel_resave(data, insert_rows=[new_row]))
        second = self.upload(excel_resave(data, [("Products", self.etb.pk, "Name", "Surging Sparks ETB")],
                                          insert_rows=[new_row]))
        added = self.made(self.apply(first), Kind.NEW_PRODUCT).object_id
        second = self.apply(second)
        self.assertEqual(list(Product.objects.filter(name="Black Bolt Elite Trainer Box").values_list("pk", flat=True)),
                         [added])
        change = self.changes(second, kind=Kind.NEW_PRODUCT)[0]
        self.assertEqual((change.outcome, change.outcome_note),
                         (Outcome.SKIPPED, f"Another upload of this download added new1 already, as {added}."))

    def test_a_file_made_again_is_already_so(self):
        _export, data = self.download()
        edited = excel_resave(data, [("Products", self.etb.pk, "Name", "Surging Sparks ETB")], insert_rows=[(
            "Products", {"Product id": "new1", "Game": "Pokémon", "Name": "Surging Sparks Mini Tin", "Type": "Tin",
                         "Release date": "15/11/2026"})])
        first = self.apply(self.upload(edited))
        added = self.made(first, Kind.NEW_PRODUCT).object_id
        again = self.upload(edited)
        self.assertEqual({c.state for c in self.changes(again)}, {State.SAME})
        self.assertEqual(self.changes(again, kind=Kind.NEW_PRODUCT)[0].why, f"Already added (id {added}).")
        self.assertEqual(again.counts["summary"], "This file makes no changes. 2 are already so.")


class MergeTests(Base):
    def setUp(self):
        super().setUp()
        self.display = make_product(self.set, name="Surging Sparks Booster Display", slug="ssp-display",
                                    product_type="booster_box", release_date=date(2024, 11, 8))
        self.display_page = make_listing(self.display, self.harbour, url="https://harbour.example/p/display")

    def same_as(self, data, *pairs, **options):
        return self.make(data, *[("Products", other.pk, "Same as product id", str(keep.pk)) for other, keep in pairs],
                         **options)

    def test_a_merge_he_can_undo_on_things_to_check(self):
        from . import autopilot

        ask = self.answered(judge.pair_key(self.box, self.display))
        export, data = self.download()
        change = self.made(self.same_as(data, (self.display, self.box)))
        answer = CheckAnswer.objects.get(kind=CheckAnswer.Kind.MERGE)
        self.assertEqual((answer.product, answer.other, answer.by_owner, answer.undo_note["keep"]),
                         (self.box, self.display, False, self.box.pk))
        self.assertEqual(answer.why, f"From your spreadsheet {export.reference}, Products row {change.row}")
        self.assertEqual(change.answer, answer)
        # Listed under Sorted for you with Undo, and never counted as Claude's act.
        self.assertIn(answer, checks.recent_answers())
        self.assertNotIn(answer, judge.claudes_acts())
        ask.refresh_from_db()
        self.assertEqual(ask.owner_answer, "same")
        self.assertFalse(Product.objects.get(pk=self.display.pk).is_active)
        self.assertEqual(Listing.objects.get(pk=self.display_page.pk).product_id, self.box.pk)
        response = self.client.get(reverse("web:product", args=[self.display.slug]))
        self.assertEqual((response.status_code, response["Location"]), (301, self.box.get_absolute_url()))
        self.tidy()
        self.assertFalse(Product.objects.get(pk=self.display.pk).is_active)
        self.assertTrue(autopilot.undo(answer))
        self.assertTrue(Product.objects.get(pk=self.display.pk).is_active)
        self.assertEqual(Listing.objects.get(pk=self.display_page.pk).product_id, self.display.pk)

    def test_a_chain_lands_on_the_last_and_a_circle_is_not_made(self):
        third = make_product(self.set, name="Surging Sparks Booster Box 36", slug="ssp-36", product_type="booster_box",
                             release_date=date(2024, 11, 8))
        _export, data = self.download()
        circle = self.same_as(data, (self.box, self.display), (self.display, self.box))
        self.assertEqual({(c.state, c.outcome) for c in self.changes(circle)}, {(State.PROBLEM, Outcome.NONE)})
        self.same_as(data, (third, self.display), (self.display, self.box))
        self.assertEqual(set(CheckAnswer.objects.filter(kind=CheckAnswer.Kind.MERGE).values_list("product", "other")),
                         {(self.box.pk, third.pk), (self.box.pk, self.display.pk)})

    def test_held_when_a_shop_sells_both_and_made_with_the_second_button(self):
        make_listing(self.display, self.shop, url="https://solent.example/products/display")
        _export, data = self.download()
        edited = excel_resave(data, [("Products", self.display.pk, "Same as product id", str(self.box.pk))])
        upload = self.apply(self.upload(edited))
        change = self.changes(upload)[0]
        self.assertEqual((change.state, change.outcome), (State.HELD, Outcome.NONE))
        self.assertTrue(Product.objects.get(pk=self.display.pk).is_active)
        self.made(self.apply(self.upload(edited), include_held=True))
        self.assertFalse(Product.objects.get(pk=self.display.pk).is_active)

    def test_changes_that_need_a_held_merge_wait_for_it(self):
        # The merge is held: Solent sells both. The barcode is free and the link lands on the box only once the
        # display is merged away, so Make N changes must not make them alone.
        Product.objects.filter(pk=self.box.pk).update(ean="")
        Product.objects.filter(pk=self.display.pk).update(ean="5053222123458")
        make_listing(self.display, self.shop, url="https://solent.example/products/display")
        kingdom = make_retailer("Kingdom Cards")
        item = ShopProduct.objects.create(retailer=kingdom, title="SSP Display", url="https://kingdom.example/p/9",
                                          price=Decimal("120.00"), status=Status.REVIEW, suggested=self.display,
                                          last_seen=timezone.now())
        _export, data = self.download()
        edited = excel_resave(data, [("Products", self.display.pk, "Same as product id", str(self.box.pk)),
                                     ("Products", self.box.pk, "Barcode", "5053222123458"),
                                     ("Shop items", item.pk, "Decision", "Link")])
        upload = self.apply(self.upload(edited))
        self.assertEqual({(c.kind, c.state, c.outcome) for c in self.changes(upload)},
                         {(Kind.MERGE, State.HELD, Outcome.NONE), (Kind.PRODUCT_FIELDS, State.HELD, Outcome.NONE),
                          (Kind.LINK, State.HELD, Outcome.NONE)})
        self.assertEqual(Product.objects.get(pk=self.box.pk).ean, "")
        self.assertEqual(ShopProduct.objects.get(pk=item.pk).status, Status.REVIEW)
        # With the second button all three are made together.
        upload = self.apply(self.upload(edited), include_held=True)
        self.assertEqual({c.outcome for c in self.changes(upload)}, {Outcome.APPLIED})
        self.assertEqual(list(Product.objects.filter(is_active=True, ean="5053222123458").values_list("pk", flat=True)),
                         [self.box.pk])
        self.assertEqual(Listing.objects.get(retailer=kingdom, url=item.url).product_id, self.box.pk)

    def test_a_hidden_product_is_merged_and_stays_hidden_once_undone(self):
        Product.objects.filter(pk=self.display.pk).update(is_active=False)
        _export, data = self.download()
        upload = self.same_as(data, (self.display, self.box))
        self.made(upload)
        self.assertEqual(Listing.objects.get(pk=self.display_page.pk).product_id, self.box.pk)
        self.assertTrue(ProductAlias.objects.filter(slug=self.display.slug, product=self.box).exists())
        self.undo(upload)
        self.assertFalse(Product.objects.get(pk=self.display.pk).is_active)
        self.assertEqual(Listing.objects.get(pk=self.display_page.pk).product_id, self.display.pk)

    def test_ebay_listing_both_does_not_hold_it(self):
        make_listing(self.display, self.ebay, url="https://www.ebay.co.uk/itm/1")
        make_listing(self.box, self.ebay, url="https://www.ebay.co.uk/itm/2")
        _export, data = self.download()
        self.made(self.same_as(data, (self.display, self.box)))

    def test_held_for_differing_barcodes(self):
        Product.objects.filter(pk=self.display.pk).update(ean="5053222123458")
        _export, data = self.download()
        edited = excel_resave(data, [("Products", self.display.pk, "Same as product id", str(self.box.pk))])
        held = self.changes(self.apply(self.upload(edited)))[0]
        self.assertEqual((held.state, held.outcome), (State.HELD, Outcome.NONE))
        self.assertIn("different barcodes", held.why)
        self.made(self.apply(self.upload(edited), include_held=True))
        # The kept product keeps its own barcode; the other's goes with it, switched off.
        self.assertEqual(Product.objects.get(pk=self.box.pk).ean, "0820650851247")

    def test_held_for_a_pair_kept_apart_which_the_merge_then_undoes(self):
        apart = CheckAnswer.objects.create(kind=CheckAnswer.Kind.APART, by_owner=True, what="Apart", product=self.box,
                                           other=self.display)
        _export, data = self.download()
        edited = excel_resave(data, [("Products", self.display.pk, "Same as product id", str(self.box.pk))])
        held = self.changes(self.apply(self.upload(edited)))[0]
        self.assertEqual((held.state, held.outcome, held.why),
                         (State.HELD, Outcome.NONE, "You said before that these are not the same product."))
        self.made(self.apply(self.upload(edited), include_held=True))
        apart.refresh_from_db()
        self.assertIsNotNone(apart.undone_at)
        self.assertNotIn(frozenset((self.box.pk, self.display.pk)), checks.kept_apart())

    def test_across_languages_is_not_made(self):
        japanese = make_product(self.set, name="Surging Sparks Booster Box (Japanese)", slug="ssp-jp",
                                product_type="booster_box", release_date=date(2024, 11, 8))
        _export, data = self.download()
        upload = self.same_as(data, (japanese, self.box))
        self.assertEqual(self.changes(upload)[0].state, State.PROBLEM)
        self.assertTrue(Product.objects.get(pk=japanese.pk).is_active)


class ApartTests(Base):
    def test_not_the_same_as(self):
        # The loose rule pairs these; the hourly strict merge does not.
        twin = make_product(self.set, name="Surging Sparks Elite Trainer Box Exclusive", release_date=date(2024, 11, 8))
        self.assertTrue(any({keep.pk, *(o.pk for o in others)} == {self.etb.pk, twin.pk}
                            for keep, others in checks.duplicates()))
        ask = self.answered(judge.pair_key(self.etb, twin))
        _export, data = self.download()
        change = self.made(self.make(data, ("Products", twin.pk, "Not the same as", str(self.etb.pk))))
        answer = CheckAnswer.objects.get(kind=CheckAnswer.Kind.APART)
        self.assertEqual((change.answer, answer.by_owner, {answer.product_id, answer.other_id}),
                         (answer, True, {self.etb.pk, twin.pk}))
        self.assertEqual(checks.duplicates(), [])
        ask.refresh_from_db()
        self.assertEqual(ask.owner_answer, "different")
        self.tidy()
        self.assertEqual(Product.objects.filter(pk__in=[self.etb.pk, twin.pk], is_active=True).count(), 2)


class ListingTests(Base):
    # A title the importer links to the box by its name alone, when nothing else decides.
    BOX_TITLE = "Pokemon Surging Sparks Booster Box"

    def read(self, retailer, url, title=BOX_TITLE, price="50.00", **kwargs):
        """The shop's next read, with this one offer."""
        return apply_offers(retailer, [Offer(title=title, url=url, price=Decimal(price), shop_type="Booster Box",
                                             **kwargs)], complete=False)

    def test_a_move(self):
        from . import pricing, sanity

        row = ShopProduct.objects.create(retailer=self.shop, title="SSP ETB", url=self.listing.url,
                                         status=Status.LINKED, suggested=self.box, price=Decimal("50"))
        page = ShopPage.objects.create(retailer=self.shop, url=self.listing.url, product=self.box)
        pricing.update_daily_lowest(self.box)
        today = timezone.localdate()
        _export, data = self.download()
        with mock.patch.object(sanity, "judge_product", wraps=sanity.judge_product) as judged:
            upload = self.make(data, ("Listings", self.listing.pk, "Product id", str(self.etb.pk)))
        change = self.made(upload)
        self.assertEqual(change.undo, {"from": self.box.pk, "to": self.etb.pk})
        self.assertEqual(Listing.objects.get(pk=self.listing.pk).product, self.etb)
        self.assertEqual(ShopProduct.objects.get(pk=row.pk).suggested, self.etb)
        self.assertEqual(ShopPage.objects.get(pk=page.pk).product, self.etb)
        self.assertTrue({self.box.pk, self.etb.pk} <= {call.args[0] for call in judged.call_args_list})
        # The box's low today came from this price: it goes, and the elite trainer box gets it.
        self.assertFalse(DailyLowestPrice.objects.filter(product=self.box, date=today).exists())
        self.assertEqual(DailyLowestPrice.objects.get(product=self.etb, date=today).price, Decimal("50.00"))
        self.tidy()
        self.read(self.shop, self.listing.url)
        self.assertEqual(Listing.objects.get(pk=self.listing.pk).product, self.etb)
        self.assertFalse(Listing.objects.filter(product=self.box, retailer=self.shop).exists())

    def test_moves_that_would_not_stick_are_not_made(self):
        on_ebay = make_listing(self.box, self.ebay, url="https://www.ebay.co.uk/itm/1")
        magic = make_listing(self.etb, self.harbour, url="https://harbour.example/p/mtg-foundations-play-booster-box")
        _export, data = self.download()
        upload = self.make(data, ("Listings", on_ebay.pk, "Product id", str(self.etb.pk)),
                           ("Listings", magic.pk, "Product id", str(self.box.pk)))
        self.assertEqual(sorted(c.state for c in self.changes(upload)), sorted([State.PROBLEM, State.HELD]))
        self.assertEqual(Listing.objects.get(pk=on_ebay.pk).product, self.box)
        self.assertEqual(Listing.objects.get(pk=magic.pk).product, self.etb)

    def test_a_move_onto_a_product_that_gained_the_shop_since_is_left(self):
        _export, data = self.download()
        upload = self.upload(excel_resave(data, [("Listings", self.listing.pk, "Product id", str(self.etb.pk))]))
        there = make_listing(self.etb, self.shop, url="https://solent.example/products/etb")
        change = self.changes(self.apply(upload))[0]
        self.assertEqual((change.outcome, change.outcome_note), (
            Outcome.SKIPPED, f"Solent Cards already has a page on {self.etb.pk}: {there.url}. Either that page or "
                             "this one is a different product."))
        self.assertEqual(Listing.objects.get(pk=self.listing.pk).product, self.box)

    def test_not_this_product(self):
        from . import pricing

        pricing.update_daily_lowest(self.box)
        _export, data = self.download()
        change = self.made(self.make(data, ("Listings", self.listing.pk, "Not this product", "Yes")))
        self.assertFalse(Listing.objects.filter(pk=self.listing.pk).exists())
        row = ShopProduct.objects.get(retailer=self.shop, url=self.listing.url)
        self.assertEqual((row.status, row.source, row.suggested, row.price),
                         (Status.IGNORED, ShopProduct.Source.IMPORT, self.box, Decimal("50.00")))
        self.assertEqual((change.undo["product"], change.undo["row"], change.undo["created"]),
                         (self.box.pk, row.pk, True))
        self.assertFalse(DailyLowestPrice.objects.filter(product=self.box, date=timezone.localdate()).exists())
        self.tidy()
        products = Product.objects.count()
        # The shop's next read gives the same title, which names the box: it is never matched by name again.
        _found, _updated, unmatched = self.read(self.shop, self.listing.url)
        self.assertFalse(Listing.objects.filter(retailer=self.shop).exists())
        self.assertEqual(Product.objects.count(), products)
        self.assertTrue(any(self.listing.url in line for line in unmatched))

    def test_not_this_product_is_not_for_ebay(self):
        on_ebay = make_listing(self.box, self.ebay, url="https://www.ebay.co.uk/itm/1")
        _export, data = self.download()
        upload = self.make(data, ("Listings", on_ebay.pk, "Not this product", "Yes"))
        self.assertEqual(self.changes(upload)[0].state, State.PROBLEM)
        self.assertTrue(Listing.objects.filter(pk=on_ebay.pk).exists())

    def test_hide(self):
        ask = self.answered(f"offer:{self.listing.pk}", kind=ClaudeAsk.Kind.OFFER)
        _export, data = self.download()
        change = self.made(self.make(data, ("Listings", self.listing.pk, "Show on site", "No")))
        self.assertFalse(Listing.objects.get(pk=self.listing.pk).is_active)
        answer = CheckAnswer.objects.get(kind=CheckAnswer.Kind.HIDE)
        self.assertEqual((change.answer, answer.by_owner, answer.listing_id), (answer, True, self.listing.pk))
        ask.refresh_from_db()
        self.assertEqual(ask.owner_answer, "hid")
        self.tidy()
        self.read(self.shop, self.listing.url, ean="0820650851247")
        self.assertFalse(Listing.objects.get(pk=self.listing.pk).is_active)

    def test_show_undoes_a_hide_and_otherwise_shows_and_judges(self):
        from . import sanity

        hidden = make_listing(self.etb, self.harbour, url="https://harbour.example/p/etb", is_active=False)
        Listing.objects.filter(pk=self.listing.pk).update(is_active=False)
        by_autopilot = CheckAnswer.objects.create(kind=CheckAnswer.Kind.HIDE, what="Hid", listing=self.listing,
                                                  product=self.box)
        _export, data = self.download()
        with mock.patch.object(sanity, "judge_product", wraps=sanity.judge_product) as judged:
            upload = self.make(data, ("Listings", self.listing.pk, "Show on site", "Yes"),
                               ("Listings", hidden.pk, "Show on site", "Yes"))
        self.assertEqual({c.outcome for c in self.changes(upload)}, {Outcome.APPLIED})
        by_autopilot.refresh_from_db()
        self.assertIsNotNone(by_autopilot.undone_at)
        self.assertEqual(Listing.objects.filter(pk__in=[self.listing.pk, hidden.pk], is_active=True).count(), 2)
        self.assertIn(self.etb.pk, {call.args[0] for call in judged.call_args_list})
        self.tidy()
        self.assertEqual(Listing.objects.filter(pk__in=[self.listing.pk, hidden.pk], is_active=True).count(), 2)


class ShopItemTests(Base):
    def setUp(self):
        super().setUp()
        self.seen = timezone.now() - timedelta(days=2)
        self.item = ShopProduct.objects.create(
            retailer=self.harbour, title="SSP ETB", url="https://harbour.example/p/etb", price=Decimal("44.99"),
            availability=Listing.Availability.IN_STOCK, status=Status.REVIEW, suggested=self.etb, confidence=70,
            last_seen=self.seen)

    def decide(self, data, decision, product=None):
        edits = [("Shop items", self.item.pk, "Decision", decision)]
        if product is not None:
            edits.append(("Shop items", self.item.pk, "Link to product id", str(product)))
        return self.make(data, *edits)

    def test_a_priced_link_shows_the_price_as_seen_then(self):
        ask = self.answered(f"found:{self.item.pk}", kind=ClaudeAsk.Kind.FOUND)
        _export, data = self.download()
        change = self.made(self.decide(data, "Link"))
        listing = Listing.objects.get(product=self.etb, retailer=self.harbour)
        self.assertEqual((listing.price, listing.url, listing.last_checked), (Decimal("44.99"), self.item.url, self.seen))
        self.item.refresh_from_db()
        self.assertEqual((self.item.status, self.item.suggested), (Status.LINKED, self.etb))
        self.assertEqual((change.undo["listing"], change.undo["status"], change.undo["suggested"]),
                         (listing.pk, Status.REVIEW, self.etb.pk))
        ask.refresh_from_db()
        self.assertEqual(ask.owner_answer, "same")
        self.tidy()
        apply_offers(self.harbour, [Offer(title="SSP ETB", url=self.item.url, price=Decimal("45.50"))], complete=False)
        self.assertEqual(Listing.objects.get(pk=listing.pk).product, self.etb)
        self.assertEqual(Listing.objects.get(pk=listing.pk).price, Decimal("45.50"))

    def test_a_link_to_another_product_with_no_price_waits_for_the_shops_read(self):
        # An address that does not say ETB, which the hourly tidy would hold against the box.
        ShopProduct.objects.filter(pk=self.item.pk).update(price=None, url="https://harbour.example/p/9")
        self.item.refresh_from_db()
        page = ShopPage.objects.create(retailer=self.harbour, url=self.item.url, last_fetched_at=timezone.now())
        _export, data = self.download()
        self.made(self.decide(data, None, product=self.box.pk))
        self.assertFalse(Listing.objects.filter(retailer=self.harbour).exists())
        self.item.refresh_from_db()
        self.assertEqual((self.item.status, self.item.suggested, self.item.price), (Status.LINKED, self.box, None))
        page.refresh_from_db()
        self.assertEqual((page.product, page.last_fetched_at), (self.box, None))
        self.tidy()
        apply_offers(self.harbour, [Offer(title="SSP ETB", url=self.item.url, price=Decimal("120"))], complete=False)
        self.assertEqual(Listing.objects.get(retailer=self.harbour).product, self.box)

    def test_a_link_to_a_product_with_another_page_at_the_shop_is_not_made(self):
        _export, data = self.download()
        upload = self.upload(excel_resave(data, [("Shop items", self.item.pk, "Decision", "Link")]))
        there = make_listing(self.etb, self.harbour, url="https://harbour.example/p/etb-two")
        change = self.changes(self.apply(upload))[0]
        self.assertEqual(change.outcome, Outcome.SKIPPED)
        self.assertIn(f"already shows Surging Sparks Elite Trainer Box on {there.url}", change.outcome_note)
        self.assertEqual(ShopProduct.objects.get(pk=self.item.pk).status, Status.REVIEW)

    def test_not_ours_and_look_again(self):
        ask = self.answered(f"found:{self.item.pk}", kind=ClaudeAsk.Kind.FOUND)
        ignored = ShopProduct.objects.create(retailer=self.harbour, title="SSP sleeves", url="https://harbour.example/p/s",
                                             status=Status.IGNORED)
        _export, data = self.download()
        upload = self.make(data, ("Shop items", self.item.pk, "Decision", "Not ours"),
                           ("Shop items", ignored.pk, "Decision", "Look again"))
        self.assertEqual({c.outcome for c in self.changes(upload)}, {Outcome.APPLIED})
        self.assertEqual(ShopProduct.objects.get(pk=self.item.pk).status, Status.IGNORED)
        self.assertEqual(ShopProduct.objects.get(pk=ignored.pk).status, Status.REVIEW)
        ask.refresh_from_db()
        self.assertEqual(ask.owner_answer, "different")


class LeftOutTests(Base):
    def setUp(self):
        super().setUp()
        from .models import ImportRun

        self.url = "https://harbour.example/products/illustration"
        ImportRun.objects.create(retailer=self.harbour, finished_at=timezone.now(), unmatched=(
            f"Pokemon Surging Sparks Illustration Box [no barcode] {self.url}\n"
            "Pokemon Surging Sparks Mega Bundle [0820650851230] https://harbour.example/products/mega"))

    def test_a_left_out_link_waits_and_the_next_read_adds_it_whatever_its_title(self):
        page = ShopPage.objects.create(retailer=self.harbour, url=self.url, last_fetched_at=timezone.now())
        _export, data = self.download(left_out_retailer=self.harbour)
        self.made(self.make(data, ("Left out", self.url, "Link to product id", str(self.etb.pk))))
        row = ShopProduct.objects.get(retailer=self.harbour, url=self.url)
        self.assertEqual((row.status, row.suggested, row.price, row.confidence, row.source, row.shop_ean),
                         (Status.LINKED, self.etb, None, 100, ShopProduct.Source.IMPORT, ""))
        page.refresh_from_db()
        self.assertEqual((page.product, page.last_fetched_at), (self.etb, None))
        self.tidy()
        # A title the rules turn away: the waiting row is matched before any rule reads it.
        apply_offers(self.harbour, [Offer(title="Mystery Bundle of Things", url=self.url, price=Decimal("30"))],
                     complete=False)
        self.assertEqual(Listing.objects.get(retailer=self.harbour).product, self.etb)

    def test_a_left_out_item_not_ours(self):
        _export, data = self.download(left_out_retailer=self.harbour)
        self.made(self.make(data, ("Left out", self.url, "Decision", "Not ours")))
        row = ShopProduct.objects.get(retailer=self.harbour, url=self.url)
        self.assertEqual((row.status, row.suggested), (Status.IGNORED, None))


class OrderTests(Base):
    def test_one_file_made_in_order(self):
        """A new product, a rename and a barcode on the product kept, a take-off of a shop both products share,
        a merge into the product kept, and a link to the product merged away: the barcode ends on the keeper,
        the merge is made, and the link lands on the keeper."""
        other = make_product(self.set, name="Surging Sparks Booster Display", slug="ssp-display",
                             product_type="booster_box", ean="5053222123458", release_date=date(2024, 11, 8))
        shared = make_listing(other, self.shop, url="https://solent.example/products/display")
        Product.objects.filter(pk=self.box.pk).update(ean="")
        item = ShopProduct.objects.create(retailer=self.harbour, title="SSP Display", url="https://harbour.example/p/d",
                                          price=Decimal("90"), status=Status.REVIEW, suggested=other,
                                          last_seen=timezone.now())
        _export, data = self.download()
        upload = self.make(
            data,
            ("Products", self.box.pk, "Name", "Surging Sparks Booster Box 36 Packs"),
            ("Products", self.box.pk, "Barcode", "5053222123458"),
            ("Listings", shared.pk, "Not this product", "Yes"),
            ("Products", other.pk, "Same as product id", str(self.box.pk)),
            ("Shop items", item.pk, "Link to product id", str(other.pk)),
            insert_rows=[("Products", {"Product id": "new1", "Game": "Pokémon", "Name": "Surging Sparks Mini Tin",
                                       "Type": "Tin", "Release date": "15/11/2026"})],
        )
        made = self.changes(upload)
        self.assertEqual([(c.kind, c.outcome) for c in made], [
            (Kind.NEW_PRODUCT, Outcome.APPLIED), (Kind.PRODUCT_FIELDS, Outcome.APPLIED),
            (Kind.PRODUCT_FIELDS, Outcome.APPLIED), (Kind.TAKE_OFF, Outcome.APPLIED), (Kind.MERGE, Outcome.APPLIED),
            (Kind.LINK, Outcome.APPLIED)], [(c.what, c.outcome_note) for c in made])
        self.assertEqual([c.applied_at for c in made], sorted(c.applied_at for c in made))
        box = Product.objects.get(pk=self.box.pk)
        self.assertEqual((box.name, box.ean, box.is_active), ("Surging Sparks Booster Box 36 Packs", "5053222123458", True))
        self.assertEqual(Product.objects.get(pk=other.pk).ean, "")
        self.assertFalse(Listing.objects.filter(pk=shared.pk).exists())
        self.assertEqual(Listing.objects.get(retailer=self.harbour, url=item.url).product, box)
        self.assertTrue(Product.objects.filter(name="Surging Sparks Mini Tin").exists())
        self.tidy()
        box.refresh_from_db()
        self.assertEqual((box.name, box.ean, box.is_active), ("Surging Sparks Booster Box 36 Packs", "5053222123458", True))


class RunnerTests(Base):
    def dated(self, n):
        return [make_product(self.set, name=f"Surging Sparks Collection {i + 1:02d}", product_type="collection_box",
                             release_date=date(2024, 11, 8)) for i in range(n)]

    def test_a_download_then_a_check_and_a_check_again(self):
        export = DataExport.objects.create(created_by=self.owner)
        call_command("data_files")
        export.refresh_from_db()
        self.assertEqual((export.status, export.attempts), (DataExport.Status.READY, 0))
        data = bytes(DataExport.objects.values_list("workbook", flat=True).get(pk=export.pk))
        upload = self.upload(excel_resave(data, [("Products", self.etb.pk, "Name", "Surging Sparks ETB"),
                                                 ("Products", self.box.pk, "Same as product id", "999999")]))
        self.assertEqual((upload.status, upload.export, upload.total, len(upload.check_token)),
                         (Upload.CHECKED, export, 1, 12))
        self.assertEqual(upload.counts["state"], {State.READY: 1, State.PROBLEM: 1})
        self.assertEqual(upload.counts["kind"], {"name": 1})
        self.assertEqual(upload.counts["summary"], "This file makes 1 change: 1 name. 1 could not be made.")
        self.assertEqual(Product.objects.get(pk=self.etb.pk).name, "Surging Sparks Elite Trainer Box")
        # Check again: a fresh list and a fresh token, so an apply from the older preview is refused.
        token = upload.check_token
        DataImport.objects.filter(pk=upload.pk).update(status=Upload.QUEUED)
        call_command("data_files")
        upload.refresh_from_db()
        self.assertEqual((upload.status, upload.changes.count()), (Upload.CHECKED, 2))
        self.assertNotEqual(upload.check_token, token)

    def test_a_file_that_cannot_be_read_or_is_no_longer_kept_is_refused(self):
        refused = self.upload(b"PK\x03\x04 not a workbook")
        self.assertEqual(refused.status, Upload.REFUSED)
        self.assertIn("Save it as Excel Workbook (.xlsx)", refused.error)
        gone = DataImport.objects.create(created_by=self.owner, file_name="old.xlsx", sha256="x")
        call_command("data_files")
        gone.refresh_from_db()
        self.assertEqual((gone.status, gone.error), (Upload.REFUSED, data_jobs.NO_UPLOAD))

    def test_a_change_the_site_made_since_the_check_is_left_alone(self):
        _export, data = self.download()
        upload = self.upload(excel_resave(data, [("Products", self.etb.pk, "Name", "Surging Sparks ETB"),
                                                 ("Products", self.box.pk, "Name", "Surging Sparks Booster Box 36")]))
        Product.objects.filter(pk=self.etb.pk).update(name="Surging Sparks ETB (Pokemon Center)")
        upload = self.apply(upload)
        changed, renamed = sorted(self.changes(upload), key=lambda c: c.object_id != self.etb.pk)
        self.assertEqual((changed.outcome, changed.outcome_note), (Outcome.SKIPPED, data_jobs.CHANGED_SINCE))
        self.assertEqual(renamed.outcome, Outcome.APPLIED)
        self.assertEqual(Product.objects.get(pk=self.etb.pk).name, "Surging Sparks ETB (Pokemon Center)")
        self.assertEqual(Product.objects.get(pk=self.box.pk).name, "Surging Sparks Booster Box 36")
        self.assertEqual((upload.total, upload.done, upload.counts["outcome"]),
                         (2, 2, {Outcome.APPLIED: 1, Outcome.SKIPPED: 1}))

    def edited(self, products):
        _export, data = self.download()
        return excel_resave(data, [("Products", p.pk, "Release date", f"{i + 1:02d}/12/2026")
                                   for i, p in enumerate(products)])

    def each_made_once(self, products):
        for product in products:
            self.assertEqual(LogEntry.objects.filter(object_id=str(product.pk), action_flag=CHANGE).count(), 1)
            self.assertEqual(Product.objects.get(pk=product.pk).release_date.month, 12)

    def test_a_run_killed_part_way_carries_on_and_makes_nothing_twice(self):
        products = self.dated(15)
        upload = self.upload(self.edited(products))
        real, calls = owner_edits.save_product, []

        def killed_at_the_eleventh(*args, **kwargs):
            calls.append(args[0])
            if len(calls) == 11:
                raise SystemExit("killed")
            return real(*args, **kwargs)

        DataImport.objects.filter(pk=upload.pk).update(status=Upload.APPLY_QUEUED)
        with mock.patch.object(owner_edits, "save_product", side_effect=killed_at_the_eleventh), \
                self.assertRaises(SystemExit):
            call_command("data_files")
        upload.refresh_from_db()
        self.assertEqual((upload.status, upload.done), (Upload.APPLYING, 10))
        self.assertEqual(upload.changes.filter(outcome=Outcome.APPLIED).count(), 10)
        # Its heartbeat has stopped: the next start takes it up again where it stopped.
        DataImport.objects.filter(pk=upload.pk).update(heartbeat_at=timezone.now() - timedelta(minutes=6))
        call_command("data_files")
        upload.refresh_from_db()
        self.assertEqual((upload.status, upload.attempts, upload.done, upload.total), (Upload.APPLIED, 1, 15, 15))
        self.each_made_once(products)

    def test_the_hourly_tidy_leaves_a_file_cut_short_alone_and_wakes_its_runner(self):
        # A new product with no release date, kept by the page the file moves onto it after adding it.
        tin = make_product(self.set, name="Surging Sparks Tin Pokemon Centre", product_type="tin",
                           release_date=date(2024, 11, 8))
        page = make_listing(tin, self.shop, url="https://solent.example/products/777")
        _export, data = self.download()
        upload = self.upload(excel_resave(data, [("Listings", page.pk, "Product id", "new1")], insert_rows=[(
            "Products", {"Product id": "new1", "Game": "Pokémon", "Name": "Surging Sparks Mini Tin", "Type": "Tin"})]))
        DataImport.objects.filter(pk=upload.pk).update(status=Upload.APPLY_QUEUED)
        # A deploy stops the runner straight after the new product is added.
        with mock.patch.object(owner_edits, "move_listing", side_effect=SystemExit("killed")), \
                self.assertRaises(SystemExit):
            call_command("data_files")
        added = Product.objects.get(name="Surging Sparks Mini Tin")
        out = StringIO()
        with mock.patch.object(data_jobs, "wake_runner") as woke:
            call_command("tidy_all", stdout=out)
        self.assertTrue(Product.objects.filter(pk=added.pk).exists())
        self.assertEqual(woke.call_args.args[0][0][0], Upload.APPLYING)
        self.assertIn(f"spreadsheet upload {upload.pk} is part made", out.getvalue())
        # Taken up again, the file is finished, and the next hour tidies as usual.
        DataImport.objects.filter(pk=upload.pk).update(heartbeat_at=timezone.now() - timedelta(minutes=6))
        call_command("data_files")
        upload.refresh_from_db()
        self.assertEqual(upload.status, Upload.APPLIED)
        self.assertEqual(Listing.objects.get(pk=page.pk).product_id, added.pk)
        out = StringIO()
        call_command("tidy_all", stdout=out)
        self.assertNotIn("part made", out.getvalue())
        self.assertTrue(Product.objects.filter(pk=added.pk).exists())

    def test_an_error_between_changes_is_tried_again_at_once(self):
        products = self.dated(15)
        upload = self.upload(self.edited(products))
        real, calls = data_jobs.make, []

        def fails_once(*args):
            calls.append(1)
            if len(calls) == 11:
                raise RuntimeError("the database went away")
            return real(*args)

        with mock.patch.object(data_jobs, "make", side_effect=fails_once), \
                self.assertLogs("catalogue.data_jobs", "ERROR"):
            upload = self.apply(upload)
        self.assertEqual((upload.status, upload.attempts, upload.done), (Upload.APPLIED, 1, 15))
        self.each_made_once(products)

    def test_a_database_held_past_the_busy_wait_is_waited_for_not_failed(self):
        from django.db import OperationalError

        _export, data = self.download()
        edited = excel_resave(data, [("Products", self.etb.pk, "Name", "Surging Sparks ETB")])
        real = owner_edits.apply_change

        def locked(times):
            calls = []

            def apply_change(*args):
                calls.append(1)
                if len(calls) <= times:
                    raise OperationalError("database is locked")
                return real(*args)
            return apply_change

        # A deploy's VACUUM holds it for a while: the change waits, then is made.
        with mock.patch.object(owner_edits, "apply_change", side_effect=locked(4)), \
                mock.patch("catalogue.pricing.time.sleep"), mock.patch.object(data_jobs, "sleep") as paused:
            upload = self.apply(self.upload(edited))
        paused.assert_called_with(data_jobs.LOCKED_PAUSE_SECONDS)
        self.assertEqual((upload.status, upload.attempts), (Upload.APPLIED, 0))
        self.made(upload)
        # Held through every try: not made and not failed, queued again with no stop counted.
        Product.objects.filter(pk=self.etb.pk).update(name="Surging Sparks Elite Trainer Box")
        with mock.patch.object(owner_edits, "apply_change", side_effect=locked(10 ** 6)), \
                mock.patch("catalogue.pricing.time.sleep"), mock.patch.object(data_jobs, "sleep"), \
                self.assertLogs("catalogue.data_jobs", "WARNING"):
            upload = self.apply(self.upload(edited))
        self.assertEqual((upload.status, upload.attempts), (Upload.APPLY_QUEUED, 0))
        self.assertEqual(self.changes(upload)[0].outcome, Outcome.NONE)
        call_command("data_files")
        upload.refresh_from_db()
        self.assertEqual(upload.status, Upload.APPLIED)
        self.made(upload)
        # A heartbeat that cannot be written is skipped, never a stop.
        with mock.patch.object(DataImport.objects, "filter", side_effect=OperationalError("database is locked")), \
                self.assertLogs("catalogue.pricing", "WARNING"):
            data_jobs.beat(upload)

    def test_a_change_that_fails_is_listed_and_the_rest_are_made(self):
        _export, data = self.download()
        upload = self.upload(excel_resave(data, [("Products", self.etb.pk, "Name", "Surging Sparks ETB"),
                                                 ("Products", self.box.pk, "Name", "Surging Sparks Booster Box 36")]))
        real = owner_edits.save_product

        def breaks_on_the_box(pk, *args):
            if pk == self.box.pk:
                raise ValueError("a bug")
            return real(pk, *args)

        with mock.patch.object(owner_edits, "save_product", side_effect=breaks_on_the_box), \
                self.assertLogs("catalogue.data_jobs", "ERROR"):
            upload = self.apply(upload)
        outcomes = {c.object_id: (c.outcome, c.outcome_note) for c in self.changes(upload)}
        self.assertEqual(outcomes[self.etb.pk], (Outcome.APPLIED, ""))
        self.assertEqual(outcomes[self.box.pk], (Outcome.FAILED, "This could not be made (ValueError). The site's log "
                                                                 "has the details."))
        self.assertEqual(Product.objects.get(pk=self.box.pk).name, "Surging Sparks Booster Box")

    def test_a_stopped_job_is_taken_up_again_and_given_up_after_three_stops(self):
        stale, alive = timezone.now() - timedelta(minutes=6), timezone.now() - timedelta(minutes=1)
        _export, data = self.download()
        checking = DataImport.objects.create(file_name="a.xlsx", sha256="a", upload=data, status=Upload.CHECKING,
                                             heartbeat_at=stale)
        applying = DataImport.objects.create(file_name="b.xlsx", sha256="b", status=Upload.APPLYING, heartbeat_at=stale,
                                             attempts=2)
        running = DataImport.objects.create(file_name="c.xlsx", sha256="c", status=Upload.APPLYING, heartbeat_at=alive)
        building = DataExport.objects.create(status=DataExport.Status.BUILDING, heartbeat_at=stale, attempts=2)
        call_command("data_files")
        for row in (checking, applying, running, building):
            row.refresh_from_db()
        self.assertEqual((checking.status, checking.attempts), (Upload.CHECKED, 1))
        self.assertEqual((applying.status, applying.attempts, applying.error),
                         (Upload.FAILED, 3, "Stopped three times. Download a fresh file and try again."))
        self.assertEqual((running.status, running.attempts), (Upload.APPLYING, 0))
        self.assertEqual((building.status, building.error), (DataExport.Status.FAILED, data_jobs.STOPPED))

    def test_it_waits_for_a_shop_read_to_finish(self):
        _export, data = self.download()
        upload = self.upload(excel_resave(data, [("Products", self.etb.pk, "Name", "Surging Sparks ETB")]))
        notes = []
        with open(self.import_lock, "w") as read:
            fcntl.flock(read, fcntl.LOCK_EX | fcntl.LOCK_NB)

            def read_finishes(seconds):
                notes.append(DataImport.objects.get(pk=upload.pk).note)
                fcntl.flock(read, fcntl.LOCK_UN)

            with mock.patch.object(data_jobs, "sleep", side_effect=read_finishes) as waited:
                upload = self.apply(upload)
        waited.assert_called_once_with(data_jobs.LOCK_POLL_SECONDS)
        self.assertEqual(notes, ["Waiting for a shop read to finish"])
        self.assertEqual((upload.status, upload.note), (Upload.APPLIED, ""))
        self.assertEqual(Product.objects.get(pk=self.etb.pk).name, "Surging Sparks ETB")

    def test_a_wait_past_the_end_of_the_run_leaves_it_queued(self):
        _export, data = self.download()
        upload = self.upload(excel_resave(data, [("Products", self.etb.pk, "Name", "Surging Sparks ETB")]))
        DataImport.objects.filter(pk=upload.pk).update(status=Upload.APPLYING)
        with open(self.import_lock, "w") as read:
            fcntl.flock(read, fcntl.LOCK_EX | fcntl.LOCK_NB)
            data_jobs.apply_import(DataImport.objects.get(pk=upload.pk), deadline=timezone.now())
        upload.refresh_from_db()
        self.assertEqual((upload.status, upload.note), (Upload.APPLY_QUEUED, data_jobs.WAITING_FOR_READ))
        self.assertEqual(upload.changes.exclude(outcome=Outcome.NONE).count(), 0)

    def test_the_lists_are_cleared_once_at_the_end(self):
        item = ShopProduct.objects.create(retailer=self.harbour, title="SSP ETB", url="https://harbour.example/p/etb",
                                          price=Decimal("44.99"), status=Status.REVIEW, suggested=self.etb,
                                          last_seen=timezone.now())
        _export, data = self.download()
        upload = self.upload(excel_resave(data, [("Products", self.etb.pk, "Name", "Surging Sparks ETB"),
                                                 ("Listings", self.listing.pk, "Show on site", "No"),
                                                 ("Shop items", item.pk, "Decision", "Link")]))
        with mock.patch.object(data_jobs, "clear_list_caches") as cleared:
            upload = self.apply(upload)
        self.assertEqual(upload.counts["outcome"], {Outcome.APPLIED: 3})
        cleared.assert_called_once_with(force=True)

    def test_a_copy_of_the_database_first_when_the_file_merges(self):
        display = make_product(self.set, name="Surging Sparks Booster Display", slug="ssp-display",
                               product_type="booster_box", release_date=date(2024, 11, 8))
        _export, data = self.download()
        self.make(data, ("Products", self.etb.pk, "Name", "Surging Sparks ETB"))
        self.backup.assert_not_called()
        self.make(data, ("Products", display.pk, "Same as product id", str(self.box.pk)))
        self.backup.assert_called_once()
        self.assertEqual(self.backup.call_args.args, ("backup_db",))
        # Not again while the newest copy is under six hours old.
        with tempfile.NamedTemporaryFile() as copy, mock.patch.object(data_jobs, "newest_backup",
                                                                      return_value=Path(copy.name)):
            self.make(data, ("Listings", self.listing.pk, "Not this product", "Yes"))
        self.backup.assert_called_once()


class UndoTests(Base):
    def test_undo_this_file(self):
        display = make_product(self.set, name="Surging Sparks Booster Display", slug="ssp-display",
                               product_type="booster_box", release_date=date(2024, 11, 8))
        display_page = make_listing(display, self.harbour, url="https://harbour.example/p/display")
        third = make_product(self.set, name="Surging Sparks Booster Box 36", slug="ssp-36", product_type="booster_box",
                             release_date=date(2024, 11, 8))
        third_page = make_listing(third, self.ebay, url="https://www.ebay.co.uk/itm/36")
        zatu = make_retailer("Zatu Games")
        moving = make_listing(self.etb, zatu, url="https://zatu.example/p/12345")
        item = ShopProduct.objects.create(retailer=self.shop, title="SSP ETB", url="https://solent.example/products/etb",
                                          price=Decimal("44.99"), status=Status.REVIEW, suggested=self.etb,
                                          last_seen=timezone.now())
        _export, data = self.download()
        upload = self.make(
            data,
            ("Products", self.etb.pk, "Name", "Surging Sparks ETB"),
            ("Products", self.box.pk, "Release date", "01/12/2024"),
            ("Products", display.pk, "Same as product id", str(self.box.pk)),
            ("Products", third.pk, "Same as product id", str(self.box.pk)),
            ("Listings", self.listing.pk, "Show on site", "No"),
            ("Listings", moving.pk, "Product id", str(self.box.pk)),
            ("Shop items", item.pk, "Decision", "Link"),
        )
        self.assertEqual({c.outcome for c in self.changes(upload)}, {Outcome.APPLIED},
                         [(c.what, c.why, c.outcome_note) for c in self.changes(upload)])
        self.assertEqual(Listing.objects.get(pk=third_page.pk).product_id, self.box.pk)
        # Changed by hand since the file was made: that one stays as he left it.
        Product.objects.filter(pk=self.box.pk).update(release_date=date(2025, 1, 1))
        upload = self.undo(upload)
        self.assertEqual(upload.status, Upload.UNDONE)
        self.assertEqual(upload.counts["undo"], {Outcome.UNDONE: 6, Outcome.UNDO_SKIPPED: 1})
        etb = Product.objects.get(pk=self.etb.pk)
        self.assertEqual(etb.name, "Surging Sparks Elite Trainer Box")
        self.assertEqual(LogEntry.objects.filter(object_id=str(etb.pk), action_flag=CHANGE, user=self.owner).count(), 2)
        skipped = upload.changes.get(outcome=Outcome.UNDO_SKIPPED)
        self.assertEqual((skipped.object_id, skipped.outcome_note), (self.box.pk, data_jobs.UNDO_CHANGED))
        self.assertEqual(Product.objects.get(pk=self.box.pk).release_date, date(2025, 1, 1))
        # Both merges undone, the later one first: the earlier could not be undone before it.
        merges = list(upload.changes.filter(kind=Kind.MERGE).order_by("seq"))
        self.assertEqual([m.outcome for m in merges], [Outcome.UNDONE, Outcome.UNDONE])
        self.assertGreater(merges[0].undone_at, merges[1].undone_at)
        self.assertEqual(Product.objects.filter(pk__in=[display.pk, third.pk], is_active=True).count(), 2)
        self.assertEqual(Listing.objects.get(pk=display_page.pk).product_id, display.pk)
        self.assertEqual(Listing.objects.get(pk=third_page.pk).product_id, third.pk)
        self.assertFalse(CheckAnswer.objects.filter(kind=CheckAnswer.Kind.MERGE, undone_at__isnull=True).exists())
        self.assertFalse(ProductAlias.objects.filter(slug__in=[display.slug, third.slug]).exists())
        self.assertTrue(Listing.objects.get(pk=self.listing.pk).is_active)
        self.assertIsNotNone(CheckAnswer.objects.get(kind=CheckAnswer.Kind.HIDE).undone_at)
        self.assertEqual(Listing.objects.get(pk=moving.pk).product_id, self.etb.pk)
        item.refresh_from_db()
        self.assertEqual((item.status, item.suggested_id), (Status.REVIEW, self.etb.pk))
        self.assertFalse(Listing.objects.filter(retailer=self.shop, url=item.url).exists())

    def test_undo_takes_away_a_listing_the_shops_read_added_to_a_waiting_link(self):
        item = ShopProduct.objects.create(retailer=self.harbour, title="SSP ETB", url="https://harbour.example/p/9",
                                          status=Status.REVIEW, suggested=self.etb, last_seen=timezone.now())
        _export, data = self.download()
        upload = self.make(data, ("Shop items", item.pk, "Decision", "Link"))
        self.assertFalse(Listing.objects.filter(retailer=self.harbour).exists())
        apply_offers(self.harbour, [Offer(title="SSP ETB", url=item.url, price=Decimal("45.50"))], complete=False)
        self.assertEqual(Listing.objects.get(retailer=self.harbour).product, self.etb)
        self.assertEqual(self.changes(self.undo(upload))[0].outcome, Outcome.UNDONE)
        self.assertFalse(Listing.objects.filter(retailer=self.harbour).exists())
        self.assertEqual(ShopProduct.objects.get(pk=item.pk).status, Status.REVIEW)
        # Back to waiting for him, the next read adds nothing.
        apply_offers(self.harbour, [Offer(title="SSP ETB", url=item.url, price=Decimal("44.00"))], complete=False)
        self.assertFalse(Listing.objects.filter(retailer=self.harbour, product=self.etb).exists())

    def test_undo_takes_away_what_the_file_added_and_puts_pages_back(self):
        from .models import ImportRun

        url, mega = "https://harbour.example/products/illustration", "https://harbour.example/products/mega"
        ImportRun.objects.create(retailer=self.harbour, finished_at=timezone.now(), unmatched=(
            f"Pokemon Surging Sparks Illustration Box [no barcode] {url}\n"
            f"Pokemon Surging Sparks Mega Bundle [0820650851230] {mega}"))
        ignored = ShopProduct.objects.create(retailer=self.harbour, title="SSP sleeves", url="https://harbour.example/s",
                                             status=Status.REVIEW, suggested=self.box)
        hidden = make_listing(self.etb, self.ebay, url="https://www.ebay.co.uk/itm/9", is_active=False)
        _export, data = self.download(left_out_retailer=self.harbour)
        upload = self.make(
            data, ("Listings", self.listing.pk, "Not this product", "Yes"),
            ("Left out", url, "Link to product id", str(self.etb.pk)), ("Left out", mega, "Decision", "Not ours"),
            ("Shop items", ignored.pk, "Decision", "Not ours"), ("Listings", hidden.pk, "Show on site", "Yes"),
            insert_rows=[("Sets", {"Set id": "new1", "Game": "Pokémon", "Name": "Destined Rivals", "Code": "DRI"}),
                         ("Products", {"Product id": "new1", "Game": "Pokémon", "Name": "Destined Rivals Mini Tin",
                                       "Type": "Tin", "Set": "new1", "Release date": "30/05/2025"})])
        self.assertEqual({c.outcome for c in self.changes(upload)}, {Outcome.APPLIED},
                         [(c.what, c.why, c.outcome_note) for c in self.changes(upload)])
        added = Product.objects.get(name="Destined Rivals Mini Tin")
        self.assertEqual(added.product_set.name, "Destined Rivals")
        self.assertTrue(Listing.objects.get(pk=hidden.pk).is_active)
        upload = self.undo(upload)
        self.assertEqual(upload.counts["undo"], {Outcome.UNDONE: 7})
        self.assertFalse(Product.objects.filter(pk=added.pk).exists())
        self.assertFalse(ProductSet.objects.filter(name="Destined Rivals").exists())
        self.assertFalse(ShopProduct.objects.filter(retailer=self.harbour, url__in=[url, mega]).exists())
        # Shown again by the file, hidden again by its undo, as his own Hide.
        self.assertFalse(Listing.objects.get(pk=hidden.pk).is_active)
        self.assertTrue(CheckAnswer.objects.get(kind=CheckAnswer.Kind.HIDE, listing=hidden).by_owner)
        self.assertEqual(ShopProduct.objects.get(pk=ignored.pk).status, Status.REVIEW)
        # The page taken off waits for the shop's next read, which adds it again at the price it reads.
        row = ShopProduct.objects.get(retailer=self.shop, url=self.listing.url)
        self.assertEqual((row.status, row.price, row.suggested), (Status.LINKED, None, self.box))
        self.assertFalse(Listing.objects.filter(retailer=self.shop).exists())
        apply_offers(self.shop, [Offer(title="Anything at all", url=self.listing.url, price=Decimal("52"))],
                     complete=False)
        self.assertEqual(Listing.objects.get(retailer=self.shop).product, self.box)

    def test_a_sets_name_and_date_and_who_gave_the_date(self):
        ProductSet.objects.filter(pk=self.bolt.pk).update(release_date_source="pokemon.com")
        _export, data = self.download()
        upload = self.make(data, ("Sets", self.bolt.pk, "Name", "Black Bolt (SV10.5)"),
                           ("Sets", self.bolt.pk, "Release date", "25/07/2025"))
        self.assertEqual({c.outcome for c in self.changes(upload)}, {Outcome.APPLIED})
        bolt = ProductSet.objects.get(pk=self.bolt.pk)
        self.assertEqual((bolt.name, bolt.slug, bolt.release_date, bolt.release_date_source),
                         ("Black Bolt (SV10.5)", "black-bolt", date(2025, 7, 25), "owner"))
        self.assertEqual(LogEntry.objects.filter(object_id=str(bolt.pk), user=self.owner).count(), 2)
        self.undo(upload)
        bolt.refresh_from_db()
        self.assertEqual((bolt.name, bolt.release_date, bolt.release_date_source),
                         ("Black Bolt", date(2025, 7, 18), "pokemon.com"))

    def test_a_merge_built_on_since_waits_for_that_one(self):
        from .management.commands.merge_duplicates import merge_undoable

        display = make_product(self.set, name="Surging Sparks Booster Display", slug="ssp-display",
                               product_type="booster_box", release_date=date(2024, 11, 8))
        later = make_product(self.set, name="Surging Sparks Booster Box Case", slug="ssp-case",
                             product_type="booster_box", release_date=date(2024, 11, 8))
        _export, data = self.download()
        upload = self.make(data, ("Products", display.pk, "Same as product id", str(self.box.pk)))
        note = merge_undoable(self.box, [later])
        CheckAnswer.objects.create(kind=CheckAnswer.Kind.MERGE, what="Merged the case", product=self.box, other=later,
                                   undo_note=note)
        merge = self.undo(upload).changes.get()
        self.assertEqual(merge.outcome, Outcome.UNDO_SKIPPED)
        self.assertEqual(merge.outcome_note, "A later merge built on this one: Merged the case. Undo that merge first, "
                                             "then this file.")
        self.assertFalse(Product.objects.get(pk=display.pk).is_active)
        # Done as it says, Undo this file is tapped again and undoes the merge.
        from . import autopilot

        self.assertTrue(autopilot.undo(CheckAnswer.objects.get(other=later)))
        self.client.force_login(self.owner)
        self.client.post(reverse("data_import", args=[upload.pk]), {"action": "undo"})
        self.assertEqual(DataImport.objects.get(pk=upload.pk).status, Upload.UNDO_QUEUED)
        call_command("data_files")
        upload.refresh_from_db()
        self.assertEqual((upload.status, upload.counts["undo"]), (Upload.UNDONE, {Outcome.UNDONE: 1}))
        self.assertTrue(Product.objects.get(pk=display.pk).is_active)


class CommandTests(Base):
    def test_a_second_runner_leaves_the_jobs_to_the_first(self):
        from django.conf import settings

        export = DataExport.objects.create()
        folder = Path(settings.RIPRAPTOR_CACHE_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / "data-files.lock", "w") as running:
            fcntl.flock(running, fcntl.LOCK_EX | fcntl.LOCK_NB)
            call_command("data_files")
            self.assertEqual(DataExport.objects.get(pk=export.pk).status, DataExport.Status.QUEUED)
        call_command("data_files")
        self.assertEqual(DataExport.objects.get(pk=export.pk).status, DataExport.Status.READY)

    def test_it_runs_below_the_site(self):
        from .management.commands import data_files

        with mock.patch.object(data_files.os, "nice") as nice, \
                mock.patch.object(data_files.Path, "write_text") as wrote:
            data_files.lower_priority()
        nice.assert_called_once_with(10)
        # Before the shop reader (OOMScoreAdjust=500), so a runner smaller than the reader still goes first.
        wrote.assert_called_once_with("1000")
        reader = (Path(__file__).resolve().parent.parent / "deploy" / "ripraptor-worker.service").read_text()
        self.assertIn("OOMScoreAdjust=500\n", reader)
        with mock.patch.object(data_files.os, "nice", side_effect=OSError), \
                mock.patch.object(data_files.Path, "write_text", side_effect=PermissionError):
            data_files.lower_priority()

    def test_start_now_never_starts_a_process_in_tests(self):
        with mock.patch("subprocess.Popen") as popen:
            self.assertFalse(data_jobs.start_now())
        popen.assert_not_called()
        with override_settings(TESTING=False), mock.patch("subprocess.Popen") as popen:
            self.assertTrue(data_jobs.start_now())
        self.assertEqual(popen.call_args.args[0][-1], "data_files")
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        # Its log goes where the site's goes, so a failure's traceback can be found.
        self.assertIsNone(popen.call_args.kwargs["stderr"])


class WordingTests(Base):
    def test_what_the_owner_reads_says_it_plainly(self):
        import ast
        import inspect

        from web.tests import CopyStyleTests

        texts = [data_jobs.STOPPED, data_jobs.CHANGED_SINCE, data_jobs.UNDO_CHANGED, data_jobs.NO_UPLOAD,
                 data_jobs.WAITING_FOR_READ]
        # Every sentence a change left as it was can carry, as the code writes it.
        for node in ast.walk(ast.parse(inspect.getsource(owner_edits))):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and \
                    getattr(node.exc.func, "id", "") == "Refused":
                texts.append(ast.unparse(node.exc.args[0]))
        self.assertGreater(len(texts), 15)
        for text in texts:
            self.assertNotIn("—", text)
            self.assertNotIn("!", text)
            for phrase in CopyStyleTests.BANNED:
                self.assertNotIn(phrase, text.lower())
