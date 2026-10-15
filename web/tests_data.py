"""Your data in Excel: the owner's pages for downloading the catalogue as a spreadsheet, uploading it once tidied in
Excel, reading what it would change, making the changes and undoing the file. Only the owner gets in, a file is
checked as a zip before it is kept, every button acts only from the state the page showed, and a web worker never
loads openpyxl."""

import fcntl
import io
import os
import re
import subprocess
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import Client, SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue import data_jobs, workbook
from catalogue.models import DataChange, DataExport, DataImport, Product, Retailer
from catalogue.testing import excel_resave, make_listing, make_product
from catalogue.tests_owner_apply import Base as ApplyBase

from . import views
from .tests import CopyStyleTests

Status = DataImport.Status
State = DataChange.State
REFRESH = 'http-equiv="refresh"'


def visible(html):
    """The words on a page, without its tags, scripts and styles."""
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"<[^>]*>", " ", html)


def rezip(data, add=(), rename=None):
    """A workbook's zip with parts added (name, bytes) and the custom properties rewritten by ``rename``."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for part in source.infolist():
            body = source.read(part.filename)
            if rename and part.filename == "docProps/custom.xml":
                body = rename(body)
            target.writestr(part.filename, body)
        for name, body in add:
            target.writestr(name, body)
    return out.getvalue()


class DataPageBase(ApplyBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        self.display = make_product(self.set, name="Surging Sparks Booster Display", slug="ssp-display",
                                    product_type="booster_box", release_date=date(2024, 11, 8))
        # Solent sells both pages, so a merge of the two waits for his say.
        make_listing(self.display, self.shop, url="https://solent.example/products/display")

    def page(self, pk=None, **params):
        return self.client.get(reverse("data_import", args=[pk]) if pk else reverse("data"), params)

    def make_download(self, **post):
        response = self.client.post(reverse("data"), {"action": "make", **post}, follow=True)
        self.assertContains(response, "Making the spreadsheet.")
        call_command("data_files")
        return DataExport.objects.latest("pk")

    def downloaded(self, export):
        response = self.client.get(reverse("data_download", args=[export.pk]))
        self.assertEqual(response.status_code, 200)
        return response.content

    def send(self, data, name="ripraptor-data.xlsx", client=None):
        """His Check this file button with ``data`` chosen, followed to the page it leads to."""
        upload = SimpleUploadedFile(name, data, content_type=views.XLSX)
        return (client or self.client).post(reverse("data"), {"action": "upload", "file": upload}, follow=True)

    def tap(self, upload, action, **post):
        return self.client.post(reverse("data_import", args=[upload.pk]), {"action": action, **post}, follow=True)

    def checked_upload(self):
        """A file with a rename (ready) and a merge (held), uploaded and checked."""
        export = self.make_download()
        edited = excel_resave(self.downloaded(export), [
            ("Products", self.box.pk, "Name", "Surging Sparks Booster Box (36 Packs)"),
            ("Products", self.display.pk, "Same as product id", str(self.box.pk)),
        ])
        self.send(edited)
        call_command("data_files")
        return DataImport.objects.get()


class AccessTests(DataPageBase):
    def urls(self):
        export = DataExport.objects.create()
        upload = DataImport.objects.create(file_name="data.xlsx", sha256="0" * 64)
        return [reverse("data"), reverse("data_download", args=[export.pk]), reverse("data_import", args=[upload.pk])]

    def test_only_the_owner_gets_in(self):
        urls = self.urls()
        self.client.logout()
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/admin/login/", response["Location"])
        staff = get_user_model().objects.create_user("helper", "helper@example.com", "pw", is_staff=True)
        self.client.force_login(staff)
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, {"action": "make"}).status_code, 403)
        self.assertEqual(DataExport.objects.count(), 1)
        self.assertNotContains(self.client.get(reverse("admin:index")), reverse("data"))

    def test_the_admin_home_links_to_the_page(self):
        home = self.client.get(reverse("admin:index"))
        self.assertContains(home, f'href="{reverse("data")}"')
        self.assertContains(home, "Your data in Excel")

    def test_buttons_need_the_page_token(self):
        guarded = Client(enforce_csrf_checks=True)
        guarded.force_login(self.owner)
        self.assertEqual(guarded.post(reverse("data"), {"action": "make"}).status_code, 403)
        self.assertFalse(DataExport.objects.exists())
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', guarded.get(reverse("data")).content.decode())
        response = guarded.post(reverse("data"), {"action": "make", "csrfmiddlewaretoken": token.group(1)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DataExport.objects.count(), 1)


class DownloadTests(DataPageBase):
    def test_make_run_and_download(self):
        Retailer.objects.filter(pk=self.harbour.pk).update(source_type=Retailer.Source.SHOPIFY)
        response = self.client.post(reverse("data"), {"action": "make", "left_out_shop": self.harbour.pk}, follow=True)
        self.assertRedirects(response, reverse("data"))
        self.assertContains(response, "Being made.")
        # Refreshes itself while the file is made, and not once it is ready.
        self.assertContains(response, REFRESH)
        export = DataExport.objects.get()
        self.assertEqual((export.created_by, export.left_out_retailer), (self.owner, self.harbour))
        # A second tap while it is made makes no second file.
        self.client.post(reverse("data"), {"action": "make"})
        self.assertEqual(DataExport.objects.count(), 1)
        call_command("data_files")
        page = self.page()
        self.assertNotContains(page, REFRESH)
        self.assertContains(page, "Download spreadsheet (")
        self.assertContains(page, "Products 3")
        response = self.client.get(reverse("data_download", args=[export.pk]))
        self.assertEqual(response["Content-Type"], views.XLSX)
        self.assertEqual(response["Content-Disposition"], f'attachment; filename="{export.filename}"')
        self.assertIn("no-store", response["Cache-Control"])
        self.assertTrue(response.content.startswith(b"PK\x03\x04"))
        self.assertIn("Left out", workbook.read_upload(response.content).sheets)
        export.refresh_from_db()
        self.assertIsNotNone(export.downloaded_at)
        self.assertContains(self.page(), "Downloaded ")

    def test_a_file_no_longer_kept_says_so(self):
        export = self.make_download()
        DataExport.objects.filter(pk=export.pk).update(workbook=None)
        response = self.client.get(reverse("data_download", args=[export.pk]), follow=True)
        self.assertRedirects(response, reverse("data"))
        self.assertContains(response, "That spreadsheet is no longer kept. Make a fresh one.")
        self.assertContains(response, "No longer kept")

    def test_the_page_starts_the_runner_for_a_job_nobody_took(self):
        now = timezone.now()
        export = DataExport.objects.create(created_at=now - timedelta(seconds=10))
        with mock.patch.object(data_jobs, "start_now") as started:
            self.page()
        started.assert_not_called()
        DataExport.objects.filter(pk=export.pk).update(created_at=now - timedelta(seconds=40))
        with mock.patch.object(data_jobs, "start_now") as started:
            self.page()
        started.assert_called_once()
        # One running with a heartbeat that stopped, and one beating.
        DataExport.objects.filter(pk=export.pk).update(status=DataExport.Status.BUILDING, heartbeat_at=now)
        with mock.patch.object(data_jobs, "start_now") as started:
            self.page()
        started.assert_not_called()
        DataExport.objects.filter(pk=export.pk).update(heartbeat_at=now - timedelta(minutes=6))
        with mock.patch.object(data_jobs, "start_now") as started:
            self.page()
        started.assert_called_once()
        # A runner going already (busy with another file) holds its lock: no copy is started to find it taken.
        data_jobs.lock_path().parent.mkdir(parents=True, exist_ok=True)
        with open(data_jobs.lock_path(), "w") as lock, mock.patch.object(data_jobs, "start_now") as started:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.page()
            self.page(DataImport.objects.create(file_name="data.xlsx", sha256="3" * 64,
                                                created_at=now - timedelta(minutes=1)).pk)
        started.assert_not_called()


class UploadRefusalTests(DataPageBase):
    def setUp(self):
        super().setUp()
        self.export = self.make_download()
        self.data = self.downloaded(self.export)

    def refused(self, data, message, name="ripraptor-data.xlsx"):
        response = self.send(data, name)
        self.assertRedirects(response, reverse("data"))
        self.assertContains(response, message.replace("'", "&#x27;"))
        self.assertFalse(DataImport.objects.exists())

    def test_files_that_are_not_a_workbook_from_here(self):
        self.refused(self.data, views.NOT_XLSX, name="data.xlsm")
        self.refused(b"Product id,Name\n1,Box\n", views.NOT_XLSX, name="data.csv")
        self.refused(b"Product id,Name\n1,Box\n", workbook.UNREADABLE)
        self.refused(b"PK\x03\x04 not really a zip", workbook.UNREADABLE)
        self.refused(b"PK\x03\x04" + b"0" * views.MAX_UPLOAD_BYTES, views.TOO_LARGE)
        self.refused(rezip(self.data, add=[("xl/vbaProject.bin", b"macro")]), views.MACROS)
        self.refused(rezip(self.data, add=[("../../evil.txt", b"x")]), workbook.UNREADABLE)
        no_parts = io.BytesIO()
        with zipfile.ZipFile(no_parts, "w") as archive:
            archive.writestr("hello.txt", "hello")
        self.refused(no_parts.getvalue(), workbook.UNREADABLE)

    def test_a_zip_that_unpacks_to_too_much_is_never_opened(self):
        bomb = io.BytesIO()
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("xl/workbook.xml", "<workbook/>")
            with archive.open("xl/worksheets/sheet1.xml", "w") as part:
                chunk = b"\0" * (1024 * 1024)
                for _ in range(101):
                    part.write(chunk)
        self.assertLess(len(bomb.getvalue()), views.MAX_UPLOAD_BYTES)
        self.refused(bomb.getvalue(), views.TOO_MUCH_INSIDE)
        many = rezip(self.data, add=[(f"xl/media/image{n}.png", b"x") for n in range(views.MAX_PARTS)])
        self.refused(many, views.TOO_MUCH_INSIDE)

    def test_a_lost_forged_or_expired_file_number(self):
        reference = self.export.reference
        forged = rezip(self.data, rename=lambda body: body.replace(reference.encode(), b"RR-%d-%s" % (
            self.export.pk, b"0" * 12)))
        self.refused(forged, workbook.LOST_NUMBER)
        unknown = rezip(self.data, rename=lambda body: body.replace(reference.encode(), b"RR-999-%s" % (
            self.export.token.encode())))
        self.refused(unknown, workbook.LOST_NUMBER)
        DataExport.objects.filter(pk=self.export.pk).update(base=None)
        self.refused(self.data, workbook.LOST_NUMBER)

    def test_a_file_that_lost_its_number_property_is_read_from_its_read_me(self):
        response = self.send(excel_resave(self.data, drop_custom_props=True))
        upload = DataImport.objects.get()
        self.assertRedirects(response, reverse("data_import", args=[upload.pk]))
        self.assertIsNone(upload.export_id)
        call_command("data_files")
        upload.refresh_from_db()
        self.assertEqual((upload.status, upload.export_id), (Status.CHECKED, self.export.pk))

    def test_the_same_file_twice_goes_to_where_it_got_to(self):
        first = self.send(self.data)
        upload = DataImport.objects.get()
        self.assertRedirects(first, reverse("data_import", args=[upload.pk]))
        self.assertEqual((upload.file_name, upload.export_id, upload.created_by), ("ripraptor-data.xlsx",
                                                                                   self.export.pk, self.owner))
        again = self.send(self.data, name="copy.xlsx")
        self.assertRedirects(again, reverse("data_import", args=[upload.pk]))
        self.assertContains(again, "You uploaded this file already")
        self.assertEqual(DataImport.objects.count(), 1)
        # Once discarded it may be uploaded again.
        DataImport.objects.filter(pk=upload.pk).update(status=Status.DISCARDED)
        self.send(self.data)
        self.assertEqual(DataImport.objects.count(), 2)


class PreviewTests(DataPageBase):
    def test_upload_check_make_and_undo(self):
        export = self.make_download()
        edited = excel_resave(self.downloaded(export), [
            ("Products", self.box.pk, "Name", "Surging Sparks Booster Box (36 Packs)"),
            ("Products", self.display.pk, "Same as product id", str(self.box.pk)),
            ("Products", self.etb.pk, "Barcode", "5053222123450"),   # a wrong check digit
        ])
        response = self.send(edited)
        upload = DataImport.objects.get()
        self.assertContains(response, "Checking the file against the site.")
        self.assertContains(response, REFRESH)
        call_command("data_files")
        page = self.page(upload.pk)
        self.assertNotContains(page, REFRESH)
        self.assertContains(page, "This file makes 1 change: 1 name. 1 is held for your say, 1 could not be made.")
        self.assertContains(page, f"Rename {self.box.pk} &quot;Surging Sparks Booster Box&quot; to &quot;Surging "
                                  "Sparks Booster Box (36 Packs)&quot;")
        self.assertContains(page, "Solent Cards sells both on separate pages")
        self.assertContains(page, "Ready (1)")
        self.assertContains(page, "Held for your say (1)")
        self.assertContains(page, "Not made (1)")
        self.assertContains(page, "Make 1 change<")
        self.assertContains(page, "Make 2 changes, including the 1 held for your say")
        self.assertContains(page, "Check again")
        self.assertContains(page, "Discard this file")
        upload.refresh_from_db()

        # An apply from an older page, or a page checked over a day ago, makes nothing.
        response = self.tap(upload, "apply", check_token="0" * 12)
        self.assertContains(response, views.RECHECKED)
        DataImport.objects.filter(pk=upload.pk).update(checked_at=timezone.now() - timedelta(hours=25))
        self.assertContains(self.page(upload.pk), "This list was checked over a day ago.")
        response = self.tap(upload, "apply", check_token=upload.check_token)
        self.assertContains(response, views.OLD_PREVIEW)
        self.assertEqual(DataImport.objects.get(pk=upload.pk).status, Status.CHECKED)
        DataImport.objects.filter(pk=upload.pk).update(checked_at=timezone.now())

        response = self.tap(upload, "apply_all", check_token=upload.check_token)
        self.assertContains(response, "Waiting to make the changes.")
        self.assertContains(response, REFRESH)
        self.assertTrue(DataImport.objects.get(pk=upload.pk).include_held)
        # A second tap does nothing more.
        self.assertContains(self.tap(upload, "apply_all", check_token=upload.check_token), "being made already")
        call_command("data_files")
        page = self.page(upload.pk)
        self.assertNotContains(page, REFRESH)
        self.assertContains(page, "Made 2 changes.")
        self.assertContains(page, "Things to check")
        self.assertContains(page, "Undo this file")
        self.assertEqual(Product.objects.get(pk=self.box.pk).name, "Surging Sparks Booster Box (36 Packs)")
        self.assertFalse(Product.objects.get(pk=self.display.pk).is_active)

        response = self.tap(upload, "undo")
        self.assertContains(response, "Waiting to undo the changes.")
        call_command("data_files")
        page = self.page(upload.pk)
        self.assertContains(page, "Undid 2 changes.")
        self.assertNotContains(page, "Undo this file")
        self.assertEqual(Product.objects.get(pk=self.box.pk).name, "Surging Sparks Booster Box")
        self.assertTrue(Product.objects.get(pk=self.display.pk).is_active)
        # Undone, the same file may be uploaded again.
        self.send(edited)
        self.assertEqual(DataImport.objects.count(), 2)

    def test_a_change_the_site_made_since_is_listed_as_left(self):
        upload = self.checked_upload()
        Product.objects.filter(pk=self.box.pk).update(name="Surging Sparks Booster Box 36")
        self.tap(upload, "apply", check_token=upload.check_token)
        call_command("data_files")
        page = self.page(upload.pk)
        self.assertContains(page, "Made 0 changes. 1 was left as it was. It is listed below with the reason.")
        self.assertContains(page, data_jobs.CHANGED_SINCE)
        self.assertNotContains(page, "Undo this file")

    def test_check_again_gives_a_new_token(self):
        upload = self.checked_upload()
        old = upload.check_token
        response = self.tap(upload, "check_again")
        self.assertContains(response, "Checking the file again")
        self.assertContains(response, REFRESH)
        self.assertContains(self.tap(upload, "apply", check_token=old), views.RECHECKED)
        call_command("data_files")
        upload.refresh_from_db()
        self.assertEqual(upload.status, Status.CHECKED)
        self.assertNotEqual(upload.check_token, old)
        self.assertContains(self.tap(upload, "apply", check_token=old), views.RECHECKED)
        self.assertEqual(DataImport.objects.get(pk=upload.pk).status, Status.CHECKED)

    def test_discard(self):
        upload = self.checked_upload()
        response = self.tap(upload, "discard")
        self.assertContains(response, "Discarded. Nothing in this file was made.")
        upload.refresh_from_db()
        self.assertEqual((upload.status, upload.upload), (Status.DISCARDED, None))
        self.assertContains(self.tap(upload, "apply", check_token=upload.check_token), views.RECHECKED)
        self.assertEqual(DataImport.objects.get(pk=upload.pk).status, Status.DISCARDED)
        self.assertEqual(Product.objects.get(pk=self.box.pk).name, "Surging Sparks Booster Box")

    def test_a_refused_file_says_why(self):
        upload = DataImport.objects.create(file_name="data.xlsx", sha256="1" * 64, upload=b"PK\x03\x04 broken")
        call_command("data_files")
        page = self.page(upload.pk)
        self.assertContains(page, workbook.UNREADABLE)
        self.assertNotContains(page, "Make ")

    def test_long_lists_are_paged(self):
        upload = DataImport.objects.create(file_name="data.xlsx", sha256="2" * 64, status=Status.CHECKED,
                                           checked_at=timezone.now(), counts={"state": {"same": 450}})
        DataChange.objects.bulk_create(
            DataChange(data_import=upload, step=3, seq=n, sheet="Products", row=n + 1, kind=DataChange.Kind.HIDE,
                       state=State.SAME, what=f"Hide product {n}.") for n in range(450))
        page = self.page(upload.pk)
        self.assertContains(page, "Hide product 199.")
        self.assertNotContains(page, "Hide product 200.")
        self.assertContains(page, "Lines 1 to 200 of 450")
        page = self.page(upload.pk, section="same", page="3")
        self.assertContains(page, "Hide product 449.")
        self.assertNotContains(page, "Hide product 399.")
        self.assertContains(page, "Previous 200")
        self.assertNotContains(page, "Next 200")


class QueryCountTests(DataPageBase):
    def uploads(self, count):
        for n in range(count):
            DataImport.objects.create(file_name=f"data{n}.xlsx", sha256=f"{n:064d}", status=Status.APPLIED,
                                      counts={"summary": "This file makes 1 change: 1 name."})
            DataExport.objects.create(status=DataExport.Status.READY, counts={"Products": 3}, size_bytes=2_000_000)

    def test_the_data_page_takes_the_same_queries_for_2_or_12_files(self):
        self.uploads(2)
        with CaptureQueriesContext(connection) as few:
            self.page()
        self.uploads(10)
        with self.assertNumQueries(len(few.captured_queries)):
            self.page()

    def changes(self, count):
        upload = DataImport.objects.create(file_name="data.xlsx", sha256=f"{count:064d}", status=Status.APPLIED,
                                           checked_at=timezone.now())
        states = [State.READY, State.HELD, State.PROBLEM, State.CONFLICT, State.SAME]
        DataChange.objects.bulk_create(
            DataChange(data_import=upload, step=3, seq=n, sheet="Products", row=n + 1, kind=DataChange.Kind.HIDE,
                       state=states[n % 5], what=f"Hide {n}.", outcome=DataChange.Outcome.SKIPPED,
                       outcome_note="Changed.") for n in range(count))
        counts = {"state": {state: count // 5 for state in states}, "kind": {"hide": count // 5},
                  "outcome": {"skipped": count}, "summary": "This file makes some changes."}
        DataImport.objects.filter(pk=upload.pk).update(counts=counts)
        return upload

    def test_the_preview_takes_the_same_queries_for_5_or_500_changes(self):
        few, many = self.changes(5), self.changes(500)
        with CaptureQueriesContext(connection) as queries:
            self.assertContains(self.page(few.pk), "Hide 4.")
        with self.assertNumQueries(len(queries.captured_queries)):
            self.assertContains(self.page(many.pk), "Hide 199.")


class CopyTests(DataPageBase):
    def test_the_pages_and_the_read_me_keep_the_house_style(self):
        upload = self.checked_upload()
        self.tap(upload, "apply_all", check_token=upload.check_token)
        call_command("data_files")
        texts = {
            "data page": visible(self.page().content.decode()),
            "upload page": visible(self.page(upload.pk).content.decode()),
            "read me": "\n".join(workbook.README_LINES),
            "messages": "\n".join(getattr(views, name) for name in (
                "NOT_XLSX", "TOO_LARGE", "TOO_MUCH_INSIDE", "MACROS", "BUSY", "RECHECKED", "OLD_PREVIEW")),
        }
        self.assertIn("Made 2 changes.", texts["upload page"])
        for where, text in texts.items():
            with self.subTest(where=where):
                self.assertNotIn("—", text)
                self.assertNotIn("!", text)
                for phrase in CopyStyleTests.BANNED:
                    self.assertNotIn(phrase, text.lower())


class NoOpenpyxlInWebWorkersTests(SimpleTestCase):
    def test_the_site_and_these_pages_never_load_openpyxl(self):
        code = ("import sys, django; django.setup(); "
                "import web.views, ripraptor.urls, catalogue.workbook, catalogue.workbook_plan, catalogue.data_jobs; "
                "print('openpyxl' in sys.modules)")
        env = {**os.environ, "DJANGO_SETTINGS_MODULE": "ripraptor.settings"}
        done = subprocess.run([sys.executable, "-c", code], cwd=Path(settings.BASE_DIR), env=env, capture_output=True,
                              text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "False")
