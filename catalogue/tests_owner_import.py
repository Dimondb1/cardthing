"""The owner's spreadsheet coming back: the file as Excel saves it, compared three ways (as downloaded, as
uploaded, as the site holds it now), the guards against a file mixed up in Excel, and what each change is
judged against before anything is made. Nothing here makes a change: that is the apply step."""

import json
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from . import data_jobs, workbook, workbook_plan
from .models import (
    CheckAnswer, DataChange, DataExport, DataImport, Listing, Product, ProductAlias, ProductSet, Retailer, ShopProduct,
)
from .testing import Pasted, excel_resave, make_game, make_listing, make_product, make_retailer, make_set

Kind = DataChange.Kind
State = DataChange.State
Status = ShopProduct.Status


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.game = make_game()
        self.magic = make_game("Magic: The Gathering", "magic-the-gathering", short_name="Magic")
        self.set = make_set(self.game, name="Surging Sparks", slug="surging-sparks", code="SV08",
                            release_date=date(2024, 11, 8))
        self.bolt = make_set(self.game, name="Black Bolt", slug="black-bolt", code="BLK", release_date=date(2025, 7, 18))
        self.foundations = make_set(self.magic, name="Foundations", slug="foundations", code="FDN")
        self.box = make_product(self.set, name="Surging Sparks Booster Box", slug="ssp-box", product_type="booster_box",
                                ean="0820650851247", release_date=date(2024, 11, 8))
        self.etb = make_product(self.set, name="Surging Sparks Elite Trainer Box", slug="ssp-etb",
                                release_date=date(2024, 11, 8))
        self.shop = make_retailer("Solent Cards")
        self.harbour = make_retailer("Harbour Games")
        self.ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        self.listing = make_listing(self.box, self.shop, url="https://solent.example/products/12345")

    def download(self, **kwargs):
        export = DataExport.objects.create(**kwargs)
        data = data_jobs.build_export(export)
        export.refresh_from_db()
        return export, data

    def check(self, data):
        parsed = workbook.read_upload(data)
        changes = workbook_plan.plan(workbook_plan.export_for(parsed.reference), parsed)
        for change in changes:
            # What the upload stores: every change must save as it is.
            json.dumps(change.data)
            json.dumps(change.fields)
            self.assertTrue(change.what)
            self.assertLessEqual(len(change.what), 300)
        return changes

    def edit(self, data, *edits, **options):
        return self.check(excel_resave(data, edits, **options))

    def only(self, changes, state=None, kind=None):
        found = [c for c in changes if (state is None or c.state == state) and (kind is None or c.kind == kind)]
        self.assertEqual(len(found), 1, [(c.kind, c.state, c.what, c.why) for c in changes])
        return found[0]

    def states(self, changes):
        return [(c.kind, c.state) for c in changes]


class RoundTripTests(Base):
    def test_a_file_saved_by_excel_with_no_edits_changes_nothing(self):
        from .models import ImportRun

        ShopProduct.objects.create(retailer=self.harbour, title="SSP ETB", url="https://harbour.example/p/ssp-etb",
                                   price=Decimal("44.99"), status=Status.REVIEW, suggested=self.etb, confidence=70)
        make_listing(self.etb, self.harbour, url="https://harbour.example/p/etb-other", is_active=False)
        ImportRun.objects.create(retailer=self.harbour, finished_at=timezone.now(), unmatched=(
            "Pokemon Surging Sparks Illustration Box [no barcode] https://harbour.example/products/illustration"))
        _export, data = self.download(left_out_retailer=self.harbour)
        self.assertIn("Left out", workbook.read_upload(data).sheets)
        self.assertEqual(self.check(data), [])
        moved_about = excel_resave(
            data, sort=("Products", "Name", True), delete_rows=[("Products", self.etb.pk)], reorder_columns=True,
            extra_column=True, drop_custom_props=True, drop_validations=True,
        )
        self.assertEqual(self.check(moved_about), [])
        self.assertEqual(self.check(excel_resave(data, sort=("Listings", "Shop"))), [])

    def test_typed_edits_make_exactly_those_changes(self):
        item = ShopProduct.objects.create(retailer=self.harbour, title="SSP ETB", url="https://harbour.example/p/9",
                                          price=Decimal("44.99"), status=Status.REVIEW, suggested=self.etb,
                                          confidence=70)
        _export, data = self.download()
        changes = self.edit(
            data,
            # A no-break space and a space at the end, as typed or pasted from a shop's page.
            ("Products", self.box.pk, "Name", "Surging Sparks Booster Box\xa0Display "),
            ("Products", self.etb.pk, "Barcode", "5053222123458"),
            ("Products", self.etb.pk, "Release date", "15/11/2026"),
            ("Listings", self.listing.pk, "Product id", str(self.etb.pk)),
            ("Shop items", item.pk, "Decision", "Link"),
            sort=("Products", "Name"),
        )
        self.assertTrue(all(c.state == State.READY for c in changes), [(c.what, c.why) for c in changes])
        by_field = {next(iter(c.fields), c.kind): c for c in changes}
        self.assertEqual(set(by_field), {"name", "barcode", "release_date", "product", Kind.LINK})
        self.assertEqual(by_field["name"].fields["name"],
                         ["Surging Sparks Booster Box", "Surging Sparks Booster Box Display",
                          "Surging Sparks Booster Box", "Surging Sparks Booster Box Display"])
        self.assertEqual(by_field["barcode"].fields["barcode"][3], "5053222123458")
        self.assertEqual(by_field["release_date"].fields["release_date"], ["2024-11-08", "2026-11-15", "2024-11-08",
                                                                           "2026-11-15"])
        move = by_field["product"]
        self.assertEqual((move.kind, move.object_id, move.target_id), (Kind.MOVE, self.listing.pk, self.etb.pk))
        link = by_field[Kind.LINK]
        self.assertEqual((link.object_id, link.target_id), (item.pk, self.etb.pk))
        # Made in order: product fields, then shop pages, then shop items.
        self.assertEqual([c.step for c in changes], sorted(c.step for c in changes))
        self.assertEqual([c.seq for c in changes], list(range(1, len(changes) + 1)))
        self.assertEqual(by_field["name"].what, f'Rename {self.box.pk} "Surging Sparks Booster Box" to '
                                                f'"Surging Sparks Booster Box Display" (Products row '
                                                f'{by_field["name"].row}).')
        self.assertTrue(link.what.startswith("Link Harbour Games' \"SSP ETB\" (£44.99, seen "))

    def test_planning_writes_nothing(self):
        _export, data = self.download()
        changed = excel_resave(data, [("Products", self.box.pk, "Name", "Surging Sparks Booster Box Display")])
        with CaptureQueriesContext(connection) as queries:
            changes = self.check(changed)
        self.assertEqual(len(changes), 1)
        self.assertTrue(all(q["sql"].lstrip().upper().startswith("SELECT") for q in queries.captured_queries),
                        [q["sql"] for q in queries.captured_queries if not q["sql"].upper().startswith("SELECT")])
        self.assertFalse(DataChange.objects.exists())
        self.assertEqual(Product.objects.get(pk=self.box.pk).name, "Surging Sparks Booster Box")


class ThreeWayTests(Base):
    def test_the_three_way_rule(self):
        self.assertEqual(workbook_plan.three_way("a", "a", "c"), "none")
        self.assertEqual(workbook_plan.three_way("a", "b", "a"), "apply")
        self.assertEqual(workbook_plan.three_way("a", "b", "b"), "same")
        self.assertEqual(workbook_plan.three_way("a", "b", "c"), "conflict")
        self.assertEqual(workbook_plan.three_way("0123", "123", "0123", workbook.same_barcode), "none")

    def test_a_cell_he_left_is_never_a_change_whatever_the_site_did(self):
        _export, data = self.download()
        Product.objects.filter(pk=self.etb.pk).update(name="Surging Sparks ETB", ean="5053222123458")
        self.assertEqual(self.check(excel_resave(data)), [])

    def test_his_change_is_ready_already_so_or_in_conflict(self):
        _export, data = self.download()
        renamed = excel_resave(data, [("Products", self.etb.pk, "Name", "Surging Sparks ETB")])
        self.assertEqual(self.states(self.check(renamed)), [(Kind.PRODUCT_FIELDS, State.READY)])
        Product.objects.filter(pk=self.etb.pk).update(name="Surging Sparks ETB")
        same = self.only(self.check(renamed))
        self.assertEqual((same.state, same.why), (State.SAME, "Already so."))
        Product.objects.filter(pk=self.etb.pk).update(name="Surging Sparks Elite Trainer Box (Pokemon Center)")
        conflict = self.only(self.check(renamed))
        self.assertEqual(conflict.state, State.CONFLICT)
        self.assertEqual(conflict.why, 'You changed Name from "Surging Sparks Elite Trainer Box" to "Surging Sparks '
                                       'ETB", but the site has "Surging Sparks Elite Trainer Box (Pokemon Center)" '
                                       'since your download. Left as "Surging Sparks Elite Trainer Box (Pokemon '
                                       'Center)".')
        self.assertEqual(conflict.fields["name"][1], "Surging Sparks ETB")

    def test_a_rename_goes_ahead_while_the_site_changed_another_cell_of_the_row(self):
        _export, data = self.download()
        Product.objects.filter(pk=self.etb.pk).update(ean="5053222123458")
        changes = self.edit(data, ("Products", self.etb.pk, "Name", "Surging Sparks ETB"))
        rename = self.only(changes)
        self.assertEqual((rename.state, list(rename.fields)), (State.READY, ["name"]))
        self.assertEqual(rename.fields["name"][2], "Surging Sparks Elite Trainer Box")

    def test_a_product_gone_since_the_download(self):
        gone = make_product(self.set, name="Surging Sparks Booster Display", slug="ssp-display")
        removed = make_product(self.set, name="Surging Sparks Mini Tin", slug="ssp-tin", product_type="tin")
        _export, data = self.download()
        Product.objects.filter(pk=gone.pk).update(is_active=False)
        ProductAlias.objects.create(slug=gone.slug, product=self.box)
        removed_pk = removed.pk
        removed.delete()
        changes = self.edit(data, ("Products", gone.pk, "Name", "Surging Sparks Booster Display Box"),
                            ("Products", removed_pk, "Barcode", "5053222123458"))
        reasons = sorted((c.state, c.why) for c in changes)
        self.assertEqual(reasons, [
            (State.CONFLICT, f"Merged into {self.box.pk} since your download."),
            (State.CONFLICT, "Removed by the hourly tidy-up since your download."),
        ])


class ReuploadTests(Base):
    def test_a_file_already_made_is_already_so_and_its_new_rows_already_added(self):
        export, data = self.download()
        edited = excel_resave(data, [
            ("Products", self.etb.pk, "Name", "Surging Sparks ETB"),
            ("Listings", self.listing.pk, "Show on site", "No"),
            ("Products", self.etb.pk, "Not the same as", str(self.box.pk)),
        ], insert_rows=[("Products", {"Product id": "new1", "Game": "Pokémon", "Name": "Surging Sparks Mini Tin",
                                      "Type": "Tin", "Release date": "15/11/2026"})])
        changes = self.check(edited)
        self.assertEqual(sorted(c.state for c in changes), [State.READY] * 4)
        # What the apply step leaves behind, made here by hand.
        added = make_product(self.set, name="Surging Sparks Mini Tin", product_type="tin",
                             release_date=date(2026, 11, 15))
        Product.objects.filter(pk=self.etb.pk).update(name="Surging Sparks ETB")
        Listing.objects.filter(pk=self.listing.pk).update(is_active=False)
        CheckAnswer.objects.create(kind=CheckAnswer.Kind.APART, by_owner=True, what="Apart", product=self.etb,
                                   other=self.box)
        upload = DataImport.objects.create(export=export, file_name="a.xlsx", sha256="x", status="applied")
        DataChange.objects.create(data_import=upload, step=2, seq=1, sheet="Products", row=4, kind=Kind.NEW_PRODUCT,
                                  state=State.READY, ref="new1", object_id=added.pk, outcome="applied", what="Add")
        again = self.check(edited)
        self.assertEqual(sorted(c.state for c in again), [State.SAME] * 4, [(c.what, c.why) for c in again])
        new = self.only(again, kind=Kind.NEW_PRODUCT)
        self.assertEqual((new.why, new.object_id), (f"Already added (id {added.pk}).", added.pk))

    def test_links_to_a_row_added_before_name_the_product_it_became(self):
        item = ShopProduct.objects.create(retailer=self.harbour, title="Mini Tin", url="https://harbour.example/p/tin",
                                          price=Decimal("12"), status=Status.REVIEW)
        export, data = self.download()
        edited = excel_resave(data, [("Shop items", item.pk, "Link to product id", "new1")], insert_rows=[(
            "Products", {"Product id": "new1", "Game": "Pokémon", "Name": "Surging Sparks Mini Tin", "Type": "Tin"})])
        link = self.only(self.check(edited), kind=Kind.LINK)
        self.assertEqual((link.state, link.data["target_ref"]), (State.READY, "new1"))
        added = make_product(self.set, name="Surging Sparks Mini Tin", product_type="tin")
        upload = DataImport.objects.create(export=export, file_name="a.xlsx", sha256="x", status="applied")
        DataChange.objects.create(data_import=upload, step=2, seq=1, sheet="Products", row=9, kind=Kind.NEW_PRODUCT,
                                  state=State.READY, ref="new1", object_id=added.pk, outcome="applied", what="Add")
        link = self.only(self.check(edited), kind=Kind.LINK)
        self.assertEqual((link.state, link.target_id), (State.READY, added.pk))


    def test_a_new_row_number_used_again_for_another_product_or_set_is_refused(self):
        item = ShopProduct.objects.create(retailer=self.harbour, title="BLK Bundle", url="https://harbour.example/p/b",
                                          price=Decimal("30"), status=Status.REVIEW, last_seen=timezone.now())
        export, data = self.download()
        # An earlier upload of this download added new1 and a set new1.
        added = make_product(self.bolt, name="Black Bolt Elite Trainer Box", release_date=date(2025, 7, 18))
        rivals = make_set(self.game, name="Destined Rivals", slug="destined-rivals", code="DRI")
        upload = DataImport.objects.create(export=export, file_name="a.xlsx", sha256="x", status="applied")
        for seq, (sheet, kind, pk, row) in enumerate((
                ("Products", Kind.NEW_PRODUCT, added.pk, {"game": self.game.pk, "name": added.name,
                                                          "type": "elite_trainer_box"}),
                ("Sets", Kind.NEW_SET, rivals.pk, {"game": self.game.pk, "name": "Destined Rivals"}))):
            DataChange.objects.create(data_import=upload, step=2, seq=seq, sheet=sheet, row=9, kind=kind,
                                      state=State.READY, ref="new1", object_id=pk, outcome="applied", what="Add",
                                      data=row)
        # The original download opened again, and new1 given to another product and another set.
        changes = self.edit(data, ("Shop items", item.pk, "Link to product id", "new1"), insert_rows=[
            ("Products", {"Product id": "new1", "Game": "Pokémon", "Name": "Black Bolt Booster Bundle",
                          "Type": "Booster bundle", "Release date": "18/07/2025"}),
            ("Sets", {"Set id": "new1", "Game": "Pokémon", "Name": "Journey Together"})])
        new = self.only(changes, kind=Kind.NEW_PRODUCT)
        self.assertEqual((new.state, new.why), (State.PROBLEM, (
            f'new1 was used for "Black Bolt Elite Trainer Box" (id {added.pk}) in an earlier upload of this '
            "download. Number this row new2 instead, and the rows that name it.")))
        link = self.only(changes, kind=Kind.LINK)
        self.assertEqual((link.state, link.target_id), (State.PROBLEM, None))
        new_set = self.only(changes, kind=Kind.NEW_SET)
        self.assertEqual(new_set.state, State.PROBLEM)
        self.assertTrue(new_set.why.startswith(f'new1 was used for the set "Destined Rivals" (id {rivals.pk})'))
        # The same rows again are what was added before.
        again = self.edit(data, insert_rows=[
            ("Products", {"Product id": "new1", "Game": "Pokémon", "Name": "Black Bolt Elite Trainer Box",
                          "Type": "Elite Trainer Box", "Release date": "18/07/2025"}),
            ("Sets", {"Set id": "new1", "Game": "Pokémon", "Name": "Destined Rivals"})])
        self.assertEqual({c.state for c in again}, {State.SAME}, [(c.what, c.why) for c in again])


class IntegrityTests(Base):
    def products(self, n):
        return [make_product(self.set, name=f"Surging Sparks Collection {i + 1:02d}", product_type="collection_box",
                             release_date=date(2024, 11, 8)) for i in range(n)]

    def test_ids_sorted_on_their_own_no_longer_match_their_addresses(self):
        a, b, c = self.products(3)
        _export, data = self.download()
        shuffled = excel_resave(data, [("Products", a.pk, "Product id", str(b.pk)),
                                       ("Products", b.pk, "Product id", str(a.pk))])
        changes = self.check(shuffled)
        self.assertEqual(sorted(c.object_id for c in changes), sorted([a.pk, b.pk]))
        self.assertTrue(all(c.state == State.PROBLEM and c.why == workbook_plan.MISMATCH for c in changes))

    def test_addresses_changed_by_find_and_replace_are_ignored(self):
        # Replace All over the sheet changes the grey Address text too, which is ignored on upload.
        made = self.products(24)
        _export, data = self.download()
        edits = [("Products", p.pk, "Address", f"https://ripraptor.example/products/pokémon-{p.slug}") for p in made]
        edits.append(("Products", self.box.pk, "Address", "https://ripraptor.example/products/elite-trainer-box"))
        edits.append(("Products", self.etb.pk, "Name", "Surging Sparks Elite Trainer Box Pokémon Center"))
        rename = self.only(self.edit(data, *edits))
        self.assertEqual((rename.object_id, rename.state), (self.etb.pk, State.READY))

    def test_more_than_twenty_mismatched_ids_leave_the_whole_sheet(self):
        made = self.products(24)
        _export, data = self.download()
        ids = [p.pk for p in made]
        edits = [("Products", pk, "Product id", str(ids[(i + 1) % len(ids)])) for i, pk in enumerate(ids)]
        edits.append(("Products", self.box.pk, "Name", "Surging Sparks Booster Box Display"))
        changes = self.edit(data, *edits)
        problem = self.only(changes)
        self.assertEqual(problem.what, "Nothing on Products was changed.")
        self.assertIn("24 rows' ids do not match their addresses", problem.why)

    def test_names_sorted_on_their_own_are_refused_as_a_column(self):
        made = self.products(12)
        _export, data = self.download()
        names = [p.name for p in made]
        edits = [("Products", p.pk, "Name", names[(i + 1) % len(names)]) for i, p in enumerate(made)]
        problem = self.only(self.edit(data, *edits))
        self.assertEqual(problem.why, "Names look sorted on their own, so nothing in this column was changed.")
        # Ten real renames are not a sort.
        edits = [("Products", p.pk, "Name", f"{p.name} Deluxe") for p in made[:10]]
        self.assertEqual({c.state for c in self.edit(data, *edits)}, {State.READY})

    def test_a_column_changed_on_a_quarter_of_its_rows_is_held(self):
        made = self.products(55)
        _export, data = self.download()
        edits = [("Products", p.pk, "Release date", f"{1 + i % 28:02d}/12/2026") for i, p in enumerate(made[:50])]
        changes = self.edit(data, *edits)
        self.assertEqual({(c.state, c.why) for c in changes}, {(State.HELD, workbook_plan.MASS)})
        self.assertEqual(len(changes), 50)

    def test_one_id_twice_an_unknown_id_and_a_row_with_no_id(self):
        _export, data = self.download()
        row = {"Product id": self.etb.pk, "Game": "Pokémon", "Name": "Surging Sparks Elite Trainer Box",
               "Type": "Elite Trainer Box", "Set": "Surging Sparks", "Language": "English",
               "Release date": "08/11/2024", "Show on site": "Yes", "Address": "https://ripraptor.com/products/ssp-etb/"}
        # The same row twice, as a copy and paste leaves it: read once.
        self.assertEqual(self.check(excel_resave(data, insert_rows=[("Products", row)])), [])
        differs = excel_resave(data, insert_rows=[("Products", {**row, "Name": "Surging Sparks ETB"})])
        changes = self.check(differs)
        self.assertEqual({(c.object_id, c.state) for c in changes}, {(self.etb.pk, State.PROBLEM)})
        self.assertEqual(len(changes), 2)
        self.assertIn("have the same id and differ, so neither was read", changes[0].why)
        unknown = self.only(self.check(excel_resave(data, insert_rows=[("Products", {"Product id": 999999,
                                                                                      "Name": "Mystery Box"})])))
        self.assertEqual(unknown.why, "Not in the file you downloaded.")
        no_id = self.only(self.check(excel_resave(data, insert_rows=[("Products", {"Name": "Mystery Box"})])))
        self.assertEqual(no_id.why, "Use new1, new2 and so on to add a row.")
        listing = self.only(self.check(excel_resave(data, insert_rows=[("Listings", {"Product id": self.box.pk})])))
        self.assertEqual(listing.why, "Not in the file you downloaded.")

    def test_a_formula_is_not_read(self):
        _export, data = self.download()
        # Pasted into Name; typed into Release date, which is not a text cell, so Excel makes it a formula.
        changes = self.edit(data, ("Products", self.box.pk, "Name", Pasted('=PROPER("x")')),
                            ("Products", self.etb.pk, "Release date", "=TODAY()"))
        self.assertEqual({c.state for c in changes}, {State.PROBLEM})
        self.assertEqual(sorted((c.what, c.why) for c in changes), [
            (f"Name on Products row {changes[0].row} was not read.",
             f"Row {changes[0].row}, Name is a formula. Copy the column, paste it back as values, save and upload "
             "again."),
            (f"Release date on Products row {changes[1].row} was not read.",
             f"Row {changes[1].row}, Release date is a formula. Copy the column, paste it back as values, save and "
             "upload again."),
        ])

    def test_an_array_formula_is_not_read_either(self):
        from openpyxl.worksheet.formula import ArrayFormula

        _export, data = self.download()
        changes = self.edit(data, ("Products", self.box.pk, "Name", Pasted(ArrayFormula("D2", "=TRIM(X2:X5)"))),
                            ("Products", self.etb.pk, "Same as product id", Pasted(ArrayFormula("L3", "=X3"))),
                            ("Sets", self.set.pk, "Name", Pasted(ArrayFormula("D2", "=PROPER(D2)"))))
        self.assertEqual({c.state for c in changes}, {State.PROBLEM})
        self.assertEqual(len(changes), 3)
        self.assertTrue(all(" is a formula. Copy the column" in c.why for c in changes), [c.why for c in changes])

    def test_a_file_with_no_number_a_forged_one_or_an_old_one_is_refused(self):
        export, data = self.download()
        with self.assertRaisesMessage(workbook.FileRefused, "lost its file number or is over 90 days old"):
            workbook_plan.export_for(None)
        with self.assertRaisesMessage(workbook.FileRefused, "lost its file number"):
            workbook_plan.export_for((export.pk, "0" * 12))
        with self.assertRaisesMessage(workbook.FileRefused, "lost its file number"):
            workbook_plan.export_for((export.pk + 100, export.token))
        DataExport.objects.filter(pk=export.pk).update(base=None)
        with self.assertRaisesMessage(workbook.FileRefused, "lost its file number"):
            workbook_plan.export_for((export.pk, export.token))

    def test_too_many_changes_in_one_file(self):
        _export, data = self.download()
        edits = [("Products", self.box.pk, "Name", "Surging Sparks Booster Box Display"),
                 ("Products", self.etb.pk, "Name", "Surging Sparks ETB")]
        with mock.patch.object(workbook_plan, "MAX_CHANGES", 1), \
                self.assertRaisesMessage(workbook.FileRefused, "more than 1 changes. Split your changes over several"):
            self.edit(data, *edits)


class ProductFieldTests(Base):
    def test_a_rename_is_tidied_and_a_collision_with_the_hourly_merge_is_refused(self):
        twin = make_product(self.set, name="Surging Sparks Booster Bundle", slug="ssp-bundle", product_type="bundle")
        _export, data = self.download()
        rename = self.only(self.edit(data, ("Products", self.etb.pk, "Name", "Surging Sparks ETB!")))
        self.assertEqual(rename.fields["name"][3], "Surging Sparks ETB")
        # "Surging Sparks: Booster Bundle" has the bundle's strict key, in its game, type and language.
        Product.objects.filter(pk=self.etb.pk).update(product_type="bundle")
        _export, data = self.download()
        collision = self.only(self.edit(data, ("Products", self.etb.pk, "Name", "Surging Sparks: Booster Bundle")))
        self.assertEqual(collision.state, State.PROBLEM)
        self.assertEqual(collision.why, f'The hourly tidy would join this with {twin.pk} "Surging Sparks Booster '
                                        f'Bundle". If they are one product, put {twin.pk} in Same as product id.')
        exact = self.only(self.edit(data, ("Products", self.box.pk, "Name", "Surging Sparks Booster Bundle")))
        self.assertEqual(exact.state, State.PROBLEM)
        self.assertIn(f"join this with {twin.pk}", exact.why)
        # Not when the other is renamed, or merged into this one, in the same file.
        changes = self.edit(data, ("Products", self.etb.pk, "Name", "Surging Sparks: Booster Bundle"),
                            ("Products", twin.pk, "Name", "Surging Sparks Booster Bundle Pokemon Center"))
        self.assertEqual({c.state for c in changes}, {State.READY})
        changes = self.edit(data, ("Products", self.etb.pk, "Name", "Surging Sparks: Booster Bundle"),
                            ("Products", twin.pk, "Same as product id", str(self.etb.pk)))
        self.assertEqual({c.state for c in changes}, {State.READY}, [(c.what, c.why) for c in changes])

    def test_a_rename_its_own_shop_pages_contradict_is_held(self):
        # Filed under a Japanese set it stays Japanese without the word, but its shop's address says Japanese,
        # and the hourly tidy deletes a listing whose address has a language word its product's name lacks.
        festival = make_set(self.game, name="Terastal Festival ex", slug="terastal-festival", code="SV8a")
        box = make_product(festival, name="Terastal Festival ex Booster Box Japanese", slug="sv8a-box",
                           product_type="booster_box", release_date=date(2024, 12, 6))
        url = "https://solent.example/products/terastal-festival-ex-booster-box-japanese"
        make_listing(box, self.shop, url=url)
        _export, data = self.download()
        rename = self.only(self.edit(data, ("Products", box.pk, "Name", "Terastal Festival ex Booster Box")))
        self.assertEqual((rename.state, rename.why),
                         (State.HELD, f"{workbook_plan.FITS} The new name contradicts Solent Cards' page {url}."))
        ready = self.only(self.edit(data, ("Products", box.pk, "Name", "Terastal Festival ex Booster Box (Japanese)")))
        self.assertEqual(ready.state, State.READY)

    def test_showing_a_hidden_twin_the_hourly_merge_would_join_is_refused(self):
        twin = make_product(self.set, name="Pokemon TCG: Surging Sparks Booster Box", slug="ssp-box-tcg",
                            product_type="booster_box", release_date=date(2024, 11, 8), is_active=False)
        _export, data = self.download()
        shown = self.only(self.edit(data, ("Products", twin.pk, "Show on site", "Yes")))
        self.assertEqual((shown.state, shown.why), (State.PROBLEM, (
            f'The hourly tidy would join this with {self.box.pk} "Surging Sparks Booster Box". If they are one '
            f"product, put {self.box.pk} in Same as product id.")))

    def test_names_the_hourly_tidy_would_remove_are_refused(self):
        _export, data = self.download()
        generic = self.only(self.edit(data, ("Products", self.etb.pk, "Name", "Elite Trainer Box")))
        self.assertEqual(generic.state, State.PROBLEM)
        self.assertIn("says only what kind of product it is", generic.why)

    def test_a_type_its_game_does_not_list_is_kept_while_untouched(self):
        tin = make_product(self.foundations, name="Foundations Krenko Tin", product_type="tin")
        _export, data = self.download()
        self.assertEqual(self.check(excel_resave(data)), [])
        refused = self.only(self.edit(data, ("Products", tin.pk, "Type", "Elite Trainer Box")))
        self.assertEqual((refused.state, refused.why),
                         (State.PROBLEM, "Elite Trainer Box is not a type this game uses. Pick one from the list."))
        unknown = self.only(self.edit(data, ("Products", tin.pk, "Type", "Mystery thing")))
        self.assertEqual(unknown.what, f'Change Type of {tin.pk} "Foundations Krenko Tin" to "Mystery thing" '
                                       f'(Products row {unknown.row}).')

    def test_a_type(self):
        _export, data = self.download()
        retype = self.only(self.edit(data, ("Products", self.etb.pk, "Type", "Booster bundle")))
        self.assertEqual((retype.state, retype.fields["type"][3]), (State.READY, "bundle"))
        self.assertIn("from Elite Trainer Box to Booster bundle", retype.what)
        self.assertIn("The hourly retype leaves a type you set.", retype.what)
        wrong = self.only(self.edit(data, ("Products", self.etb.pk, "Type", "Commander deck")))
        self.assertEqual(wrong.state, State.PROBLEM)

    def test_a_set(self):
        plain = make_product(self.set, name="Pokemon Mystery Collection", product_type="collection_box",
                             release_date=date(2025, 1, 1))
        _export, data = self.download()
        moved = self.only(self.edit(data, ("Products", self.etb.pk, "Set", "black bolt")))
        self.assertEqual((moved.state, moved.fields["set"][3]), (State.READY, self.bolt.pk))
        self.assertIn("under the set Black Bolt", moved.what)
        other_game = self.only(self.edit(data, ("Products", self.etb.pk, "Set", "Foundations")))
        self.assertEqual((other_game.state, other_game.why), (State.PROBLEM, "Foundations is a set of another game."))
        # Its name names Surging Sparks, so the hourly filing would put it back.
        cleared = self.only(self.edit(data, ("Products", self.etb.pk, "Set", "")))
        self.assertEqual(cleared.state, State.PROBLEM)
        self.assertIn("The name names Surging Sparks, so the hourly filing puts it back", cleared.why)
        cleared = self.only(self.edit(data, ("Products", plain.pk, "Set", "")))
        self.assertEqual((cleared.state, cleared.fields["set"][3]), (State.READY, None))

    def test_a_set_code_that_changes_its_products_language(self):
        mew = make_set(self.game, name="151", slug="151", code="MEW", release_date=date(2023, 9, 22))
        bundle = make_product(mew, name="151 Booster Bundle", slug="151-bundle", product_type="bundle",
                              release_date=date(2023, 9, 22))
        _export, data = self.download()
        change = self.only(self.edit(data, ("Sets", mew.pk, "Code", "SV2a")))
        self.assertEqual((change.state, change.what), (State.READY, (
            f'Change the code of the set {mew.pk} "151" to SV2a (was MEW) (Sets row {change.row}). 1 product '
            "becomes Japanese.")))
        # With a Japanese twin, the hourly merge would join the two and delete one.
        jset = make_set(self.game, name="Pokemon Card 151", slug="pokemon-card-151", code="sv2a",
                        release_date=date(2023, 6, 16))
        twin = make_product(jset, name="Pokemon 151 Booster Bundle", slug="151-bundle-jp", product_type="bundle",
                            release_date=date(2023, 6, 16))
        _export, data = self.download()
        refused = self.only(self.edit(data, ("Sets", mew.pk, "Code", "SV2a")))
        self.assertEqual((refused.state, refused.why), (State.PROBLEM, (
            f'{bundle.pk} "151 Booster Bundle" becomes Japanese, and the hourly tidy would join it with {twin.pk} '
            f'"Pokemon 151 Booster Bundle". If they are one product, put {twin.pk} in its Same as product id.')))
        # A release date says nothing about language.
        self.assertEqual(self.only(self.edit(data, ("Sets", mew.pk, "Release date", "23/09/2023"))).state,
                         State.READY)

    def test_a_set_name_or_code_holding_markup_is_refused(self):
        _export, data = self.download()
        for heading, value in (("Name", "Surging Sparks <img src=x>"), ("Code", "SV08>")):
            refused = self.only(self.edit(data, ("Sets", self.set.pk, heading, value)))
            self.assertEqual((refused.state, refused.why), (State.PROBLEM, "A set's name or code cannot hold < or >."))
        added = self.only(self.edit(data, insert_rows=[("Sets", {"Set id": "new1", "Game": "Pokémon",
                                                                 "Name": "Destined </script> Rivals"})]))
        self.assertEqual(added.state, State.PROBLEM)

    def test_a_set_added_in_the_same_file(self):
        _export, data = self.download()
        changes = self.edit(
            data, ("Products", self.etb.pk, "Set", "Destined Rivals"),
            insert_rows=[("Sets", {"Set id": "new1", "Game": "Pokémon", "Name": "Destined Rivals", "Code": "DRI",
                                   "Release date": "30/05/2025"})])
        self.assertEqual(self.states(changes), [(Kind.NEW_SET, State.READY), (Kind.PRODUCT_FIELDS, State.READY)])
        self.assertEqual(changes[1].fields["set"][3], "new1")
        self.assertEqual(changes[0].what, 'Add the set "Destined Rivals" (DRI), out 30 May 2025 (Sets row 5).')
        again = self.edit(data, insert_rows=[("Sets", {"Set id": "new1", "Game": "Pokémon", "Name": "Black Bolt"})])
        self.assertEqual((again[0].state, again[0].why), (State.PROBLEM, f"Pokémon already has this set: id {self.bolt.pk}."))

    def test_a_language_is_added_to_the_name(self):
        _export, data = self.download()
        japanese = self.only(self.edit(data, ("Products", self.etb.pk, "Language", "Japanese")))
        self.assertEqual(japanese.state, State.READY)
        self.assertEqual(japanese.fields["name"][3], "Surging Sparks Elite Trainer Box (Japanese)")
        self.assertEqual(japanese.fields["language"][3], "ja")
        self.assertIn("Make", japanese.what)
        both = self.only(self.edit(data, ("Products", self.etb.pk, "Language", "Korean"),
                                   ("Products", self.etb.pk, "Name", "Surging Sparks ETB")))
        self.assertEqual(both.fields["name"][3], "Surging Sparks ETB (Korean)")
        sv2a = make_product(self.set, name="Pokemon 151 sv2a Booster Box", product_type="booster_box",
                            release_date=date(2023, 6, 16))
        self.assertEqual(sv2a.language, "ja")
        _export, data = self.download()
        english = self.only(self.edit(data, ("Products", sv2a.pk, "Language", "English")))
        self.assertEqual((english.state, english.why),
                         (State.PROBLEM, "The name or set says Japanese. Change the name or set."))

    def test_a_language_the_name_already_gives_is_already_so(self):
        gem = make_product(self.set, name="Gem Pack Vol 6 Booster Box", product_type="booster_box",
                           release_date=date(2025, 1, 1))
        self.assertEqual(gem.language, "zh-hans")
        _export, data = self.download()
        chinese = self.only(self.edit(data, ("Products", gem.pk, "Language", "Chinese")))
        self.assertEqual((chinese.state, chinese.why), (State.SAME, "Already so: the site reads it as Simplified Chinese."))

    def test_a_long_name_is_shown_whole_where_it_changes(self):
        long = "SWSH Evolving Skies Elite Trainer Box Leafeon, Umbreon, Jolteon, Flareon POKEMON CENTER"
        Product.objects.filter(pk=self.etb.pk).update(name=long)
        _export, data = self.download()
        new = long.replace("POKEMON CENTER", "Pokemon Center")
        rename = self.only(self.edit(data, ("Products", self.etb.pk, "Name", new)))
        self.assertEqual(rename.what, f'Rename {self.etb.pk} "SWSH Evolving Skies Elite Trainer Box..." to "{new}" '
                                      f"(Products row {rename.row}).")
        japanese = self.only(self.edit(data, ("Products", self.etb.pk, "Language", "Japanese")))
        self.assertIn(f'its name becomes "{long} (Japanese)"', japanese.what)
        # Changed on the site too: the names are cut in the middle, so where they differ shows.
        Product.objects.filter(pk=self.etb.pk).update(name=long.replace("POKEMON CENTER", "Pokemon Centre"))
        conflict = self.only(self.edit(data, ("Products", self.etb.pk, "Name", new)))
        self.assertEqual(conflict.state, State.CONFLICT)
        self.assertIn("Flareon Pokemon Center\" but the site has", conflict.why.replace(",", ""))
        self.assertIn("Flareon Pokemon Centre\" since your download", conflict.why)
        self.assertLessEqual(len(conflict.why), 300)

    def test_a_rename_says_when_it_changes_the_language(self):
        _export, data = self.download()
        rename = self.only(self.edit(data, ("Products", self.etb.pk, "Name", "Surging Sparks Elite Trainer Box JPN")))
        self.assertTrue(rename.what.endswith("Its language becomes Japanese."), rename.what)

    def test_a_barcode(self):
        _export, data = self.download()
        given = self.only(self.edit(data, ("Products", self.etb.pk, "Barcode", "5053222123458")))
        self.assertEqual((given.state, given.fields["barcode"][3]), (State.READY, "5053222123458"))
        taken = self.only(self.edit(data, ("Products", self.etb.pk, "Barcode", "820650851247")))
        self.assertEqual(taken.state, State.PROBLEM)
        self.assertTrue(taken.why.startswith(f'Already on {self.box.pk} "Surging Sparks Booster Box". Two products'))
        bad = self.only(self.edit(data, ("Products", self.etb.pk, "Barcode", "5053222123456")))
        self.assertEqual((bad.state, bad.why),
                         (State.PROBLEM, "5053222123456 is not a barcode: its last digit does not check out."))
        damaged = self.only(self.edit(data, ("Products", self.etb.pk, "Barcode", "5.05322E+12")))
        self.assertEqual((damaged.state, damaged.why), (State.PROBLEM, workbook.EXCEL_NUMBER))
        # Excel dropped the leading zero of the box's barcode: no change.
        self.assertEqual(self.edit(data, ("Products", self.box.pk, "Barcode", Pasted(820650851247))), [])
        # Moving a barcode from one product to another in one file is not a collision.
        changes = self.edit(data, ("Products", self.box.pk, "Barcode", ""),
                            ("Products", self.etb.pk, "Barcode", "0820650851247"))
        self.assertEqual({c.state for c in changes}, {State.READY})

    def test_clearing_a_date_when_only_a_link_with_no_price_would_keep_it_is_held(self):
        item = ShopProduct.objects.create(retailer=self.harbour, title="SSP ETB", url="https://harbour.example/p/9",
                                          status=Status.REVIEW, suggested=self.etb, last_seen=timezone.now())
        _export, data = self.download()
        changes = self.edit(data, ("Products", self.etb.pk, "Release date", ""),
                            ("Shop items", item.pk, "Decision", "Link"))
        cleared = self.only(changes, kind=Kind.PRODUCT_FIELDS)
        self.assertEqual((cleared.state, cleared.why),
                         (State.HELD, f"{workbook_plan.TIDY_REMOVES} Clearing its date removes it."))

    def test_hiding_a_product_the_hourly_tidy_would_then_remove_is_held(self):
        lonely = make_product(self.set, name="Surging Sparks Mini Tin", product_type="tin")
        _export, data = self.download()
        hide = self.only(self.edit(data, ("Products", lonely.pk, "Show on site", "No")))
        self.assertEqual(hide.state, State.HELD)
        self.assertIn("Hiding it removes it for good.", hide.why)
        self.assertEqual(self.only(self.edit(data, ("Products", self.box.pk, "Show on site", "No"))).state,
                         State.READY)
        self.assertEqual(self.edit(data, ("Products", self.box.pk, "Show on site", "")), [])


class NewProductTests(Base):
    def new_row(self, **values):
        return ("Products", {"Product id": "new1", "Game": "Pokémon", "Name": "Surging Sparks Mini Tin",
                             "Type": "Tin", **values})

    def test_a_new_product_needs_a_game_name_type_and_a_release_date_or_a_priced_shop(self):
        item = ShopProduct.objects.create(retailer=self.harbour, title="SSP Tin", url="https://harbour.example/p/tin",
                                          price=Decimal("12.99"), status=Status.REVIEW)
        _export, data = self.download()
        dated = self.only(self.edit(data, insert_rows=[self.new_row(**{"Release date": "15/11/2026"})]))
        self.assertEqual((dated.state, dated.kind, dated.ref), (State.READY, Kind.NEW_PRODUCT, "new1"))
        self.assertEqual(dated.data["type"], "tin")
        self.assertTrue(dated.data["show"])
        self.assertEqual(dated.what, f'Add new1 "Surging Sparks Mini Tin": Tin, out 15 Nov 2026 (Products row '
                                     f'{dated.row}).')
        undated = self.only(self.edit(data, insert_rows=[self.new_row()]))
        self.assertEqual(undated.state, State.PROBLEM)
        self.assertIn("removes a product no shop lists that has no release date", undated.why)
        linked = self.edit(data, ("Shop items", item.pk, "Link to product id", "new1"), insert_rows=[self.new_row()])
        self.assertEqual({c.state for c in linked}, {State.READY})
        for values, problem in (({"Game": "Yu-Gi-Oh"}, "The site has no game called Yu-Gi-Oh."),
                                ({"Name": ""}, "A product needs a name."),
                                ({"Type": "Collector booster box"}, "is not a type this game uses."),
                                ({"Type": "Commander deck"}, "Commander deck is not a type.")):
            refused = self.only(self.edit(data, insert_rows=[self.new_row(**{"Release date": "15/11/2026", **values})]))
            self.assertEqual(refused.state, State.PROBLEM)
            self.assertIn(problem, refused.why)

    def test_other_sheets_can_name_a_new_row(self):
        item = ShopProduct.objects.create(retailer=self.harbour, title="SSP Tin", url="https://harbour.example/p/tin",
                                          price=Decimal("12.99"), status=Status.REVIEW)
        tin = make_product(self.set, name="Surging Sparks Tin Pokemon Centre", product_type="tin",
                           release_date=date(2024, 11, 8))
        wrong = make_listing(tin, self.shop, url="https://solent.example/products/777")
        _export, data = self.download()
        changes = self.edit(
            data, ("Shop items", item.pk, "Decision", "Link"), ("Shop items", item.pk, "Link to product id", "new1"),
            ("Listings", wrong.pk, "Product id", "new1"), ("Products", tin.pk, "Same as product id", "new1"),
            insert_rows=[self.new_row()])
        self.assertEqual({c.state for c in changes}, {State.READY}, [(c.what, c.why) for c in changes])
        by_kind = {c.kind: c for c in changes}
        for kind in (Kind.LINK, Kind.MOVE, Kind.MERGE):
            self.assertEqual(by_kind[kind].data["target_ref"], "new1", kind)
            self.assertIsNone(by_kind[kind].target_id)
        self.assertEqual([c.kind for c in changes], [Kind.NEW_PRODUCT, Kind.MOVE, Kind.MERGE, Kind.LINK])
        missing = self.edit(data, ("Listings", wrong.pk, "Product id", "new2"))
        self.assertEqual((missing[0].state, missing[0].why), (State.PROBLEM, "There is no new2 row on Products."))

    def test_a_language_on_a_new_row_goes_in_its_name(self):
        _export, data = self.download()
        added = self.only(self.edit(data, insert_rows=[self.new_row(**{"Language": "Japanese",
                                                                         "Release date": "15/11/2026"})]))
        self.assertEqual((added.data["name"], added.data["language"]), ("Surging Sparks Mini Tin (Japanese)", "ja"))


class MergeTests(Base):
    def setUp(self):
        super().setUp()
        self.display = make_product(self.set, name="Surging Sparks Booster Display", slug="ssp-display",
                                    product_type="booster_box", release_date=date(2024, 11, 8))
        make_listing(self.display, self.harbour, url="https://harbour.example/p/display")

    def same_as(self, data, *pairs, **options):
        return self.edit(data, *[("Products", other.pk, "Same as product id", str(keep.pk)) for other, keep in pairs],
                         **options)

    def test_a_merge(self):
        _export, data = self.download()
        merge = self.only(self.same_as(data, (self.display, self.box)))
        self.assertEqual((merge.state, merge.object_id, merge.target_id), (State.READY, self.display.pk, self.box.pk))
        self.assertEqual(merge.what, f'Merge {self.display.pk} "Surging Sparks Booster Display" into {self.box.pk} '
                                     f'"Surging Sparks Booster Box" (Products row {merge.row}). Its 1 shop moves '
                                     'across and its address redirects. Can be undone.')
        self.assertEqual(merge.step, 5)

    def test_other_edits_on_a_row_merged_away_are_not_made(self):
        _export, data = self.download()
        changes = self.edit(data, ("Products", self.display.pk, "Same as product id", str(self.box.pk)),
                            ("Products", self.display.pk, "Name", "Surging Sparks Display"))
        self.assertEqual(sorted(self.states(changes)), sorted([(Kind.PRODUCT_FIELDS, State.PROBLEM),
                                                                (Kind.MERGE, State.READY)]))
        self.assertEqual(self.only(changes, kind=Kind.PRODUCT_FIELDS).why,
                         "Merged away, so its other changes are not made.")

    def test_chains_resolve_and_circles_are_refused(self):
        third = make_product(self.set, name="Surging Sparks Booster Box 36", slug="ssp-36", product_type="booster_box")
        _export, data = self.download()
        changes = self.same_as(data, (third, self.display), (self.display, self.box))
        self.assertEqual({(c.object_id, c.target_id, c.state) for c in changes},
                         {(third.pk, self.box.pk, State.READY), (self.display.pk, self.box.pk, State.READY)})
        circle = self.same_as(data, (self.box, self.display), (self.display, self.box))
        self.assertEqual({(c.state, c.why) for c in circle},
                         {(State.PROBLEM, "These products name each other in Same as product id, so none is merged.")})
        itself = self.only(self.same_as(data, (self.box, self.box)))
        self.assertEqual(itself.why, "A product cannot be the same as itself.")
        unknown = self.only(self.edit(data, ("Products", self.box.pk, "Same as product id", "999999")))
        self.assertEqual(unknown.why, "No product has id 999999.")

    def test_held_when_a_shop_sells_both_but_not_for_ebay(self):
        make_listing(self.display, self.ebay, url="https://www.ebay.co.uk/itm/1")
        make_listing(self.box, self.ebay, url="https://www.ebay.co.uk/itm/2")
        _export, data = self.download()
        self.assertEqual(self.only(self.same_as(data, (self.display, self.box))).state, State.READY)
        make_listing(self.display, self.shop, url="https://solent.example/products/display")
        _export, data = self.download()
        held = self.only(self.same_as(data, (self.display, self.box)))
        self.assertEqual(held.state, State.HELD)
        self.assertTrue(held.why.startswith("Solent Cards sells both on separate pages."))

    def test_merges_into_one_product_are_judged_in_the_order_they_are_made(self):
        # The box has no Harbour page; the display and a third product each have one. The first merge made brings
        # its page across, so the second would leave its own on the switched-off product.
        third = make_product(self.set, name="Surging Sparks Booster Box 36 Packs", slug="ssp-36",
                             product_type="booster_box", release_date=date(2024, 11, 8))
        make_listing(third, self.harbour, url="https://harbour.example/p/36")
        _export, data = self.download()
        first, second = sorted(self.same_as(data, (self.display, self.box), (third, self.box)), key=lambda c: c.row)
        self.assertEqual((first.object_id, first.state, first.data["shops"]), (third.pk, State.READY, 1))
        self.assertIn("Its 1 shop moves across", first.what)
        self.assertEqual((second.object_id, second.state, second.data["shops"]), (self.display.pk, State.HELD, 0))
        self.assertTrue(second.why.startswith("Harbour Games sells both on separate pages."))
        self.assertNotIn("moves across", second.what)

    def test_held_for_differing_barcodes_and_for_a_pair_kept_apart(self):
        Product.objects.filter(pk=self.display.pk).update(ean="5053222123458")
        _export, data = self.download()
        held = self.only(self.same_as(data, (self.display, self.box)))
        self.assertEqual(held.state, State.HELD)
        self.assertIn("different barcodes, 5053222123458 and 0820650851247", held.why)
        Product.objects.filter(pk=self.display.pk).update(ean="")
        CheckAnswer.objects.create(kind=CheckAnswer.Kind.APART, by_owner=True, what="Apart", product=self.box,
                                   other=self.display)
        _export, data = self.download()
        held = self.only(self.same_as(data, (self.display, self.box)))
        self.assertEqual((held.state, held.why), (State.HELD, "You said before that these are not the same product."))

    def test_a_ready_change_that_needs_a_held_one_is_held_with_it(self):
        # The move is held (its address says Japanese, the ETB's name does not). Judged with the move made, the
        # merge would be ready, but made without it Solent's second page would sit on the switched-off display.
        page = make_listing(self.display, self.shop, url="https://solent.example/products/ssp-display-japanese")
        _export, data = self.download()
        changes = self.edit(data, ("Listings", page.pk, "Product id", str(self.etb.pk)),
                            ("Products", self.display.pk, "Same as product id", str(self.box.pk)))
        move, merge = self.only(changes, kind=Kind.MOVE), self.only(changes, kind=Kind.MERGE)
        self.assertEqual(move.state, State.HELD)
        self.assertEqual((merge.state, merge.why), (State.HELD, workbook_plan.DEPENDS))

    def test_held_when_a_page_it_brings_contradicts_the_product_kept(self):
        case = make_product(self.set, name="Surging Sparks Booster Box Case", slug="ssp-case",
                            product_type="booster_box", release_date=date(2024, 11, 8))
        url = "https://kingdom.example/products/surging-sparks-booster-box-case"
        make_listing(case, make_retailer("Kingdom Cards"), url=url)
        _export, data = self.download()
        held = self.only(self.same_as(data, (case, self.box)))
        self.assertEqual((held.state, held.why), (State.HELD, (
            f"{workbook_plan.FITS} Kingdom Cards' page moves across, and its address contradicts {self.box.pk} "
            f'"Surging Sparks Booster Box": {url}.')))

    def test_refused_across_languages_or_games_or_into_a_hidden_product(self):
        japanese = make_product(self.set, name="Surging Sparks Booster Box (Japanese)", slug="ssp-jp",
                                product_type="booster_box")
        magic = make_product(self.foundations, name="Foundations Play Booster Box", product_type="booster_box")
        hidden = make_product(self.set, name="Surging Sparks Booster Box Pokemon Center", is_active=False,
                              product_type="booster_box")
        _export, data = self.download()
        self.assertEqual(self.only(self.same_as(data, (japanese, self.box))).why,
                         f"This is Japanese and {self.box.pk} is English, so they are not one product.")
        self.assertEqual(self.only(self.same_as(data, (magic, self.box))).why,
                         f"{self.box.pk} is a product of another game.")
        self.assertIn("is hidden from the site", self.only(self.same_as(data, (self.display, hidden))).why)

    def test_a_type_difference_is_only_noted(self):
        bundle = make_product(self.set, name="Surging Sparks Booster Box Bundle", product_type="bundle")
        _export, data = self.download()
        merge = self.only(self.same_as(data, (bundle, self.box)))
        self.assertEqual(merge.state, State.READY)
        self.assertEqual(merge.warning, f"Their types differ: {self.box.pk} keeps its type, Booster box.")

    def test_already_merged(self):
        _export, data = self.download()
        merging = excel_resave(data, [("Products", self.display.pk, "Same as product id", str(self.box.pk))])
        Product.objects.filter(pk=self.display.pk).update(is_active=False)
        ProductAlias.objects.create(slug=self.display.slug, product=self.box)
        merge = self.only(self.check(merging))
        self.assertEqual((merge.state, merge.why), (State.SAME, f"Already merged into {self.box.pk}."))


class ApartTests(Base):
    def test_not_the_same_as(self):
        _export, data = self.download()
        apart = self.only(self.edit(data, ("Products", self.box.pk, "Not the same as", str(self.etb.pk))))
        self.assertEqual((apart.state, apart.object_id, apart.target_id), (State.READY, self.box.pk, self.etb.pk))
        both = self.edit(data, ("Products", self.box.pk, "Not the same as", str(self.etb.pk)),
                         ("Products", self.etb.pk, "Not the same as", str(self.box.pk)))
        self.assertEqual(len(both), 1)
        merging = self.edit(data, ("Products", self.box.pk, "Not the same as", str(self.etb.pk)),
                            ("Products", self.etb.pk, "Same as product id", str(self.box.pk)))
        self.assertEqual(self.only(merging, kind=Kind.APART).why, "The same pair merges in this file.")
        CheckAnswer.objects.create(kind=CheckAnswer.Kind.APART, by_owner=True, what="Apart", product=self.etb,
                                   other=self.box)
        self.assertEqual(self.only(self.edit(data, ("Products", self.box.pk, "Not the same as",
                                                    f"{self.etb.pk}"))).state, State.SAME)


class ListingTests(Base):
    def test_a_move(self):
        _export, data = self.download()
        move = self.only(self.edit(data, ("Listings", self.listing.pk, "Product id", str(self.etb.pk))))
        self.assertEqual((move.kind, move.state, move.target_id), (Kind.MOVE, State.READY, self.etb.pk))
        self.assertEqual(move.fields["product"], [self.box.pk, self.etb.pk, self.box.pk, self.etb.pk])
        self.assertEqual(move.what, f'Move Solent Cards\' "{self.listing.title or self.listing.url}" from '
                                    f'{self.box.pk} "Surging Sparks Booster Box" to {self.etb.pk} "Surging Sparks '
                                    f'Elite Trainer Box" (Listings row {move.row}).')

    def test_moves_that_would_not_stick_are_refused(self):
        on_ebay = make_listing(self.box, self.ebay, url="https://www.ebay.co.uk/itm/1")
        make_listing(self.etb, self.harbour, url="https://harbour.example/p/etb")
        on_harbour = make_listing(self.box, self.harbour, url="https://harbour.example/p/box")
        _export, data = self.download()
        ebay = self.only(self.edit(data, ("Listings", on_ebay.pk, "Product id", str(self.etb.pk))))
        self.assertEqual(ebay.why, "Its prices come from searching for the product. Use Show on site No.")
        taken = self.only(self.edit(data, ("Listings", on_harbour.pk, "Product id", str(self.etb.pk))))
        self.assertEqual(taken.why, f"Harbour Games already has a page on {self.etb.pk}: https://harbour.example/p/etb. "
                                    "Either that page or this one is a different product.")
        etb_page = Listing.objects.get(product=self.etb, retailer=self.harbour)
        swap = self.edit(data, ("Listings", on_harbour.pk, "Product id", str(self.etb.pk)),
                         ("Listings", etb_page.pk, "Product id", str(self.box.pk)))
        self.assertEqual({c.state for c in swap}, {State.PROBLEM})
        self.assertIn("moves too. Move one page at a time", swap[0].why)

    def test_a_move_against_the_shops_barcode_is_refused(self):
        Listing.objects.filter(pk=self.listing.pk).update(shop_ean="0820650851247")
        _export, data = self.download()
        move = self.only(self.edit(data, ("Listings", self.listing.pk, "Product id", str(self.etb.pk))))
        self.assertEqual(move.state, State.PROBLEM)
        self.assertIn("The shop gives the barcode 0820650851247, which is on", move.why)

    def test_a_move_the_hourly_tidy_would_undo_is_held(self):
        magic = make_listing(self.etb, self.harbour, url="https://harbour.example/p/mtg-foundations-play-booster-box")
        _export, data = self.download()
        move = self.only(self.edit(data, ("Listings", magic.pk, "Product id", str(self.box.pk))))
        self.assertEqual(move.state, State.HELD)
        self.assertTrue(move.why.startswith(workbook_plan.FITS))

    def test_not_this_product(self):
        on_ebay = make_listing(self.box, self.ebay, url="https://www.ebay.co.uk/itm/1")
        _export, data = self.download()
        off = self.only(self.edit(data, ("Listings", self.listing.pk, "Not this product", "Yes")))
        self.assertEqual((off.kind, off.state, off.step), (Kind.TAKE_OFF, State.READY, 4))
        ebay = self.only(self.edit(data, ("Listings", on_ebay.pk, "Not this product", "Yes")))
        self.assertEqual(ebay.state, State.PROBLEM)
        both = self.edit(data, ("Listings", self.listing.pk, "Not this product", "Yes"),
                         ("Listings", self.listing.pk, "Product id", str(self.etb.pk)))
        self.assertEqual({(c.kind, c.state) for c in both}, {(Kind.TAKE_OFF, State.PROBLEM), (Kind.MOVE, State.PROBLEM)})
        Listing.objects.filter(pk=self.listing.pk).update(shop_ean="0820650851247")
        _export, data = self.download()
        relinked = self.only(self.edit(data, ("Listings", self.listing.pk, "Not this product", "Yes")))
        self.assertEqual(relinked.state, State.PROBLEM)
        self.assertIn("so the shop's next read links it again", relinked.why)

    def test_taking_off_or_moving_a_products_last_page_is_held(self):
        # Made from a shop's page, with no release date: the hourly tidy-up removes it once it has no listing.
        bundle = make_product(self.bolt, name="Black Bolt Booster Bundle", slug="blk-bundle", product_type="bundle")
        page = make_listing(bundle, self.shop, url="https://solent.example/products/blk-bundle")
        item = ShopProduct.objects.create(retailer=self.harbour, title="BLK Bundle", url="https://harbour.example/p/b",
                                          status=Status.REVIEW, suggested=bundle, last_seen=timezone.now())
        _export, data = self.download()
        off = self.only(self.edit(data, ("Listings", page.pk, "Not this product", "Yes")))
        self.assertEqual((off.state, off.why), (State.HELD, (
            f"{workbook_plan.TIDY_REMOVES} Taking its last page off removes it, with your other changes to it.")))
        move = self.only(self.edit(data, ("Listings", page.pk, "Product id", str(self.etb.pk))))
        self.assertEqual((move.state, move.why), (State.HELD, (
            f'{workbook_plan.TIDY_REMOVES} Moving its last page removes {bundle.pk} "Black Bolt Booster Bundle", '
            "with your other changes to it.")))
        # A link with no price makes no listing until the shop's next read, so it does not keep the product.
        changes = self.edit(data, ("Listings", page.pk, "Not this product", "Yes"),
                            ("Shop items", item.pk, "Decision", "Link"))
        self.assertEqual(self.only(changes, kind=Kind.TAKE_OFF).state, State.HELD)
        ShopProduct.objects.filter(pk=item.pk).update(price=Decimal("30.00"))
        _export, data = self.download()
        changes = self.edit(data, ("Listings", page.pk, "Not this product", "Yes"),
                            ("Shop items", item.pk, "Decision", "Link"))
        self.assertEqual({c.state for c in changes}, {State.READY})

    def test_hide_and_show(self):
        hidden = make_listing(self.etb, self.harbour, is_active=False)
        _export, data = self.download()
        hide = self.only(self.edit(data, ("Listings", self.listing.pk, "Show on site", "no")))
        self.assertEqual((hide.kind, hide.state), (Kind.HIDE, State.READY))
        show = self.only(self.edit(data, ("Listings", hidden.pk, "Show on site", "Yes")))
        self.assertEqual((show.kind, show.state), (Kind.SHOW, State.READY))
        Retailer.objects.filter(pk=self.harbour.pk).update(is_active=False)
        refused = self.only(self.edit(data, ("Listings", hidden.pk, "Show on site", "Yes")))
        self.assertEqual(refused.why, "Harbour Games is switched off, so its prices are not shown.")

    def test_a_listings_column_changed_on_a_quarter_of_its_rows_is_held(self):
        others = [make_listing(self.etb, shop, url=f"https://{shop.slug}.example/p/etb") for shop in (self.harbour,
                                                                                                    self.ebay)]
        _export, data = self.download()
        with mock.patch.object(workbook_plan, "MASS_EDITS", 2):
            changes = self.edit(data, *[("Listings", listing.pk, "Show on site", "No")
                                        for listing in (self.listing, *others)])
        self.assertEqual({(c.kind, c.state, c.why) for c in changes}, {(Kind.HIDE, State.HELD, workbook_plan.MASS)})
        self.assertEqual(len(changes), 3)

    def test_a_page_removed_since_the_download(self):
        _export, data = self.download()
        pk = self.listing.pk
        self.listing.delete()
        gone = self.only(self.edit(data, ("Listings", pk, "Show on site", "No")))
        self.assertEqual((gone.state, gone.why), (State.CONFLICT, "This shop's page was removed since your download."))


class ShopItemTests(Base):
    def setUp(self):
        super().setUp()
        self.item = ShopProduct.objects.create(
            retailer=self.harbour, title="SSP ETB", url="https://harbour.example/p/etb", price=Decimal("44.99"),
            status=Status.REVIEW, suggested=self.etb, confidence=70, last_seen=timezone.now())

    def decide(self, data, decision, product=None):
        edits = [("Shop items", self.item.pk, "Decision", decision)]
        if product is not None:
            edits.append(("Shop items", self.item.pk, "Link to product id", str(product)))
        return self.edit(data, *edits)

    def test_link_not_ours_and_look_again(self):
        _export, data = self.download()
        link = self.only(self.decide(data, "Link"))
        self.assertEqual((link.kind, link.state, link.target_id, link.warning), (Kind.LINK, State.READY, self.etb.pk, ""))
        other = self.only(self.decide(data, None, product=self.box.pk))
        self.assertEqual((other.kind, other.target_id), (Kind.LINK, self.box.pk))
        self.assertEqual(self.only(self.decide(data, "Not ours")).kind, Kind.IGNORE)
        both = self.only(self.decide(data, "Not ours", product=self.box.pk))
        self.assertEqual((both.state, both.why),
                         (State.PROBLEM, "Link to product id and Not ours on one row. Do one or the other."))
        ShopProduct.objects.filter(pk=self.item.pk).update(status=Status.IGNORED)
        self.assertEqual(self.only(self.decide(data, "Not ours")).state, State.SAME)
        self.assertEqual(self.only(self.decide(data, "Look again")).state, State.CONFLICT)

    def test_a_link_to_a_product_with_another_page_at_the_shop_is_refused(self):
        make_listing(self.etb, self.harbour, url="https://harbour.example/p/etb-two")
        _export, data = self.download()
        link = self.only(self.decide(data, "Link"))
        self.assertEqual(link.state, State.PROBLEM)
        self.assertEqual(link.why, 'Harbour Games already shows "Surging Sparks Elite Trainer Box" on '
                                   'https://harbour.example/p/etb-two. One product has one page per shop. If this page '
                                   'is another product, add it as a new row on Products and link it to that.')

    def test_already_linked_changed_since_and_old_prices(self):
        _export, data = self.download()
        make_listing(self.etb, self.harbour, url="https://harbour.example/p/etb/")
        ShopProduct.objects.filter(pk=self.item.pk).update(status=Status.LINKED)
        self.assertEqual(self.only(self.decide(data, "Link")).state, State.SAME)
        Listing.objects.filter(product=self.etb, retailer=self.harbour).delete()
        ShopProduct.objects.filter(pk=self.item.pk).update(status=Status.REVIEW, suggested=self.box)
        self.assertEqual(self.only(self.decide(data, "Link")).state, State.CONFLICT)
        ShopProduct.objects.filter(pk=self.item.pk).update(suggested=self.etb,
                                                          last_seen=timezone.now() - timedelta(days=5))
        old = self.only(self.decide(data, "Link"))
        self.assertEqual((old.state, old.warning), (State.READY, "The price shows once the shop's next read checks it."))


    def test_a_link_its_address_contradicts_is_held(self):
        ShopProduct.objects.filter(pk=self.item.pk).update(
            url="https://harbour.example/products/surging-sparks-elite-trainer-box-german")
        _export, data = self.download()
        link = self.only(self.decide(data, "Link"))
        self.assertEqual((link.state, link.why), (State.HELD, (
            f'{workbook_plan.FITS} Check its address names {self.etb.pk} "Surging Sparks Elite Trainer Box".')))


class LeftOutTests(Base):
    def setUp(self):
        super().setUp()
        from .models import ImportRun

        ImportRun.objects.create(retailer=self.harbour, finished_at=timezone.now(), unmatched="\n".join([
            "Pokemon Surging Sparks Illustration Box [no barcode] https://harbour.example/products/illustration",
            "Pokemon Surging Sparks Mega Bundle [0820650851230] https://harbour.example/products/mega",
        ]))

    def test_a_left_out_link_and_not_ours(self):
        export, data = self.download(left_out_retailer=self.harbour)
        key = "https://harbour.example/products/illustration"
        link = self.only(self.edit(data, ("Left out", key, "Link to product id", str(self.etb.pk))))
        self.assertEqual((link.kind, link.state, link.target_id), (Kind.LEFT_LINK, State.READY, self.etb.pk))
        self.assertEqual(link.ref, f"{self.harbour.pk}|harbour.example/products/illustration")
        self.assertEqual(link.data["url"], key)
        self.assertEqual(self.only(self.edit(data, ("Left out", key, "Decision", "Not ours"))).kind, Kind.LEFT_IGNORE)
        needs = self.only(self.edit(data, ("Left out", key, "Decision", "Link")))
        self.assertEqual(needs.why, "Type the product id in Link to product id.")
        make_listing(self.etb, self.harbour, url="https://harbour.example/p/etb")
        refused = self.only(self.edit(data, ("Left out", key, "Link to product id", str(self.etb.pk))))
        self.assertEqual(refused.state, State.PROBLEM)
        self.assertIn("Harbour Games already has a page on", refused.why)

    def test_a_left_out_link_its_address_contradicts_is_held(self):
        from .models import ImportRun

        ImportRun.objects.update(unmatched=(
            "Pokemon Surging Sparks Booster Box German [no barcode] https://harbour.example/products/ssp-box-german"))
        _export, data = self.download(left_out_retailer=self.harbour)
        held = self.only(self.edit(data, ("Left out", "https://harbour.example/products/ssp-box-german",
                                          "Link to product id", str(self.box.pk))))
        self.assertEqual((held.state, held.why), (State.HELD, (
            f'{workbook_plan.FITS} Check its address names {self.box.pk} "Surging Sparks Booster Box".')))


class OrderTests(Base):
    def test_one_file_judged_as_a_whole(self):
        """A new product, a rename and a barcode on the product kept, a take-off of a shop both products share,
        a merge, and a link to the product merged away: the barcode is the keeper's, the merge is not held,
        and the link lands on the keeper."""
        other = make_product(self.set, name="Surging Sparks Booster Display", slug="ssp-display",
                             product_type="booster_box", ean="5053222123458", release_date=date(2024, 11, 8))
        shared = make_listing(other, self.shop, url="https://solent.example/products/display")
        Product.objects.filter(pk=self.box.pk).update(ean="")
        item = ShopProduct.objects.create(retailer=self.harbour, title="SSP Display", url="https://harbour.example/p/d",
                                          price=Decimal("90"), status=Status.REVIEW, suggested=other)
        _export, data = self.download()
        changes = self.edit(
            data,
            ("Products", self.box.pk, "Name", "Surging Sparks Booster Box 36 Packs"),
            ("Products", self.box.pk, "Barcode", "5053222123458"),
            ("Listings", shared.pk, "Not this product", "Yes"),
            ("Products", other.pk, "Same as product id", str(self.box.pk)),
            ("Shop items", item.pk, "Link to product id", str(other.pk)),
            insert_rows=[("Products", {"Product id": "new1", "Game": "Pokémon", "Name": "Surging Sparks Mini Tin",
                                       "Type": "Tin", "Release date": "15/11/2026"})],
        )
        self.assertEqual({c.state for c in changes}, {State.READY}, [(c.what, c.why) for c in changes])
        self.assertEqual([c.kind for c in changes], [Kind.NEW_PRODUCT, Kind.PRODUCT_FIELDS, Kind.PRODUCT_FIELDS,
                                                     Kind.TAKE_OFF, Kind.MERGE, Kind.LINK])
        self.assertEqual(self.only(changes, kind=Kind.LINK).target_id, self.box.pk)


class SummaryTests(Base):
    def test_the_summary_line(self):
        changes = [DataChange(kind=Kind.PRODUCT_FIELDS, state=State.READY, fields={"name": [1, 2, 3, 4]}),
                   DataChange(kind=Kind.PRODUCT_FIELDS, state=State.READY, fields={"name": [1, 2, 3, 4]}),
                   DataChange(kind=Kind.MERGE, state=State.READY), DataChange(kind=Kind.LINK, state=State.HELD),
                   DataChange(kind=Kind.LINK, state=State.PROBLEM), DataChange(kind=Kind.LINK, state=State.SAME),
                   DataChange(kind=Kind.LINK, state=State.SAME)]
        self.assertEqual(workbook_plan.summary(changes), "This file makes 3 changes: 2 names, 1 merge. 1 is held for "
                                                         "your say, 1 could not be made, 2 are already so.")
        self.assertEqual(workbook_plan.summary([]), "This file makes no changes.")

    def test_every_sentence_says_it_plainly(self):
        from web.tests import CopyStyleTests

        _export, data = self.download()
        changes = self.edit(data, ("Products", self.etb.pk, "Name", "Surging Sparks ETB"),
                            ("Products", self.box.pk, "Same as product id", "999999"),
                            ("Listings", self.listing.pk, "Show on site", "No"))
        for change in changes:
            for text in (change.what, change.why, change.warning):
                self.assertNotIn("—", text)
                self.assertNotIn("!", text)
                for phrase in CopyStyleTests.BANNED:
                    self.assertNotIn(phrase, text.lower())


class CheckCommandTests(Base):
    def test_check_prints_the_plan_and_changes_nothing(self):
        import tempfile
        from pathlib import Path

        export, data = self.download()
        edited = excel_resave(data, [("Products", self.etb.pk, "Name", "Surging Sparks ETB"),
                                     ("Products", self.box.pk, "Same as product id", "999999")], extra_column=True)
        before = (Product.objects.count(), DataChange.objects.count(), DataImport.objects.count(),
                  list(Product.objects.values_list("name", flat=True)))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "edited.xlsx"
            path.write_bytes(edited)
            out = StringIO()
            # With the system checks a real run makes, which a method called check() would break.
            call_command("data_files", "--check", str(path), stdout=out, skip_checks=False)
        text = out.getvalue()
        self.assertIn(f"File {export.reference}, downloaded ", text)
        self.assertIn("This file makes 1 change: 1 name. 1 could not be made.", text)
        self.assertIn("Nothing was changed: this was a check.", text)
        self.assertIn(f'Rename {self.etb.pk} "Surging Sparks Elite Trainer Box" to "Surging Sparks ETB"', text)
        self.assertIn("Not made (1)", text)
        self.assertIn("No product has id 999999.", text)
        self.assertIn("Products, the column My notes.", text)
        self.assertEqual(before, (Product.objects.count(), DataChange.objects.count(), DataImport.objects.count(),
                                  list(Product.objects.values_list("name", flat=True))))

    def test_check_refuses_a_file_it_cannot_read_or_place(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "notes.xlsx"
            path.write_bytes(b"not a workbook")
            with self.assertRaisesMessage(CommandError, "Save it as Excel Workbook (.xlsx)"):
                call_command("data_files", "--check", str(path))
            _export, data = self.download()
            DataExport.objects.update(base=None)
            path.write_bytes(data)
            with self.assertRaisesMessage(CommandError, "lost its file number"):
                call_command("data_files", "--check", str(path))
        with self.assertRaises(CommandError):
            call_command("data_files", "--check", "x.xlsx", "--export", "y.xlsx")
        self.assertFalse(ProductSet.objects.filter(name="x").exists())
