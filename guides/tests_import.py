"""Importing a guide from Word, Google Docs or text (guides/importer.py), the way marketing adds guides."""

import io
import zipfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from catalogue.testing import make_game, make_product, make_set

from . import importer, template
from .models import Guide


def word_file(body_xml, rels=""):
    """A small Word file with ``body_xml`` paragraphs, and hyperlink targets in ``rels``."""
    ns = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as package:
        package.writestr("[Content_Types].xml", template.CONTENT_TYPES)
        package.writestr("_rels/.rels", template.PACKAGE_RELS)
        package.writestr("word/_rels/document.xml.rels",
                         '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                         f'relationships">{rels}</Relationships>')
        package.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {ns}><w:body>{body_xml}</w:body></w:document>')
        package.writestr("word/styles.xml", template.STYLES)
        package.writestr("word/numbering.xml", template.NUMBERING.replace(
            '<w:num w:numId="1">', '<w:abstractNum w:abstractNumId="7"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/>'
            '</w:lvl></w:abstractNum><w:num w:numId="2"><w:abstractNumId w:val="7"/></w:num><w:num w:numId="1">'))
    return out.getvalue()


def para(text, style=""):
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{props}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


class ImportCase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.etb = make_product(make_set(make_game()), name="Prismatic Evolutions Elite Trainer Box",
                                slug="prismatic-evolutions-elite-trainer-box")


class TemplateTests(ImportCase):
    def test_the_word_and_text_templates_read_the_same_and_check_clean(self):
        word, text = importer.docx_lines(template.docx()), importer.text_lines(template.TEXT)
        self.assertEqual(word, text)
        checked = importer.check(word)
        self.assertEqual((checked.style, checked.publish, checked.read.notes), ([], [], []))
        self.assertTrue(checked.can_publish)

    def test_the_template_becomes_a_whole_guide(self):
        guide = importer.save(importer.check(importer.docx_lines(template.docx())))
        self.assertFalse(guide.is_published)
        self.assertEqual((guide.slug, guide.audience, guide.door), ("parents", "parents", "buying"))
        self.assertEqual(guide.door_line, "New to this? Read the parent's guide.")
        self.assertEqual(guide.search_title, "Pokémon cards for Christmas: a parent's guide")
        self.assertEqual(f"{guide.last_reviewed:%Y-%m-%d} {guide.next_review:%Y-%m-%d}", "2026-10-27 2027-01-20")
        first, fair, pay = guide.sections.all()
        self.assertTrue(first.short_answer.startswith("For a first present"))
        self.assertIn("- Buy English unless they asked for Japanese", first.body)
        self.assertEqual(list(first.products.all()), [self.etb])
        self.assertEqual((first.box_type, first.states_facts), ("elite_trainer_box", False))
        self.assertTrue(fair.show_checker)
        self.assertEqual(len(pay.green_flags.splitlines()), 2)
        self.assertEqual(len(pay.red_flags.splitlines()), 2)
        self.assertIn("This is general guidance, not legal advice.", pay.body)
        self.assertEqual(pay.sources.splitlines()[0],
                         "Citizens Advice: if you've changed your mind about something you've bought | "
                         "https://www.citizensadvice.org.uk/consumer/changed-your-mind/ | 27 Oct 2026")
        self.assertEqual(guide.checklist_title, "Before you buy: a parent's checklist")
        self.assertEqual(len(guide.checklist_items), 5)


class ReadingTests(ImportCase):
    def test_google_docs_markdown(self):
        markdown = (
            "# How to avoid scams and scalpers\n\n**Web address:** scams-and-scalpers\n\n"
            "**One-line answer:** Fakes, reseals and fake shops\\.\n\n**Linked from:** Sold out pages\n\n"
            "**Link words:** Sold out? Here is how to avoid scalpers\\.\n\n**Last checked:** 27 October 2026\n\n"
            "**Next review:** 2027-01-20\n\n## Is this box real?\n\nCheck the wrap, because a reseal "
            "shows\nloose corners.\n\n* Look at the corners\n* Look for glue\n\n**Warning signs:**\n\n"
            "* Baggy wrap, because the factory wrap is tight\n\n**Sources:**\n\n"
            "* [Citizens Advice: scams](https://www.citizensadvice.org.uk/consumer/scams/), read 27 Oct 2026\n")
        checked = importer.check(importer.text_lines(markdown))
        self.assertEqual(checked.style, [])
        section = checked.read.sections[0]
        self.assertEqual(section.short_answer, "Check the wrap, because a reseal shows loose corners.")
        self.assertEqual(section.body, ["- Look at the corners\n- Look for glue"])
        self.assertEqual(section.red, ["Baggy wrap, because the factory wrap is tight"])
        self.assertEqual(section.sources, ["Citizens Advice: scams | https://www.citizensadvice.org.uk/consumer/scams/ | 27 Oct 2026"])
        self.assertEqual((checked.guide.door, checked.guide.one_line_answer), ("sold_out", "Fakes, reseals and fake shops."))

    def test_a_guide_copied_from_a_chat_with_its_code_block_fences(self):
        pasted = "```text\n" + template.TEXT + "```\n"
        self.assertEqual(importer.text_lines(pasted), importer.text_lines(template.TEXT))

    def test_a_word_file_keeps_its_links_and_numbered_lists(self):
        body = (para("Scams guide", "Title") + para("Web address: scams") + para("One-line answer: How to spot one.")
                + para("Last checked: 27 Oct 2026") + para("Next review: 20 Jan 2027")
                + para("Is this shop real?", "Heading2") + para("Look for a UK address, because you can contact it.")
                + '<w:p><w:r><w:t xml:space="preserve">Read </w:t></w:r><w:hyperlink r:id="rId9"><w:r><w:t>Citizens Advice</w:t>'
                  '</w:r></w:hyperlink><w:r><w:t xml:space="preserve"> first.</w:t></w:r></w:p>'
                + '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr><w:r><w:t>Check the address</w:t></w:r></w:p>'
                + '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr><w:r><w:t>Call them</w:t></w:r></w:p>'
                + '<w:p><w:del><w:r><w:delText>deleted words</w:delText></w:r></w:del></w:p>'
                + para("Sources: none, this is advice"))
        rels = ('<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                'Target="https://www.citizensadvice.org.uk/" TargetMode="External"/>')
        lines = importer.docx_lines(word_file(body, rels))
        self.assertIn("Read [Citizens Advice](https://www.citizensadvice.org.uk/) first.", lines)
        self.assertIn("1. Check the address", lines)
        self.assertNotIn("deleted words", " ".join(lines))
        checked = importer.check(lines)
        self.assertEqual(checked.style, [])
        self.assertEqual(checked.read.sections[0].body,
                         ["Read [Citizens Advice](https://www.citizensadvice.org.uk/) first.", "1. Check the address\n1. Call them"])

    def test_each_problem_says_what_to_do(self):
        cases = [
            ("Web address: x\n\n## A question\n\nAn answer.", "Start with the guide's title"),
            ("# Title\n\nOne-line answer: Yes.", "Add at least one question"),
            ("# Title\n\nOne-line answer: Yes.\n\n## A question\n\nGood signs:\n\n- One", "has no short answer"),
            ("# Title\n\nOne-line answer: Yes.\n\n## Q\n\nAn answer.\n\nSources:\n\n- Which?, https://www.which.co.uk/", "add the date you read"),
            ("# Title\n\nOne-line answer: Yes.\n\n## Q\n\nAn answer.\n\nSources:\n\n- Old, http://old.example/, 27 Oct 2026", "Use its https:// address"),
            ("# Title\n\nOne-line answer: Yes.\n\n## Q\n\nAn answer.\n\nSources:\n\n- Which? the shop page", "has no web address"),
            ("# Title\n\nOne-line answer: Yes.\nLast checked: soon\n\n## Q\n\nAn answer.", "Last checked: write the date as 27 Oct 2026"),
            ("# Title — a guide\n\nOne-line answer: Yes.\n\n## Q\n\nAn answer.", "remove the em dash"),
            ("# Title\n\nOne-line answer: Yes!\n\n## Q\n\nAn answer.", "remove the exclamation mark"),
            ("# Title\n\nWeb address: fair-price\nOne-line answer: Yes.\n\n## Q\n\nAn answer.", "kept for another page"),
            ("# Title\n\n## Q\n\nAn answer.", "Add a One-line answer"),
        ]
        for text_, expected in cases:
            with self.subTest(expected):
                checked = importer.check(importer.text_lines(text_))
                self.assertFalse(checked.can_save)
                self.assertIn(expected, " ".join(checked.style))

    def test_what_it_saves_anyway_but_says(self):
        checked = importer.check(importer.text_lines(
            "# Title\n\nOne-line answer: Yes.\nStray words here\n\n## Q\n\nAn answer.\n\nPrices:\n"
            "https://ripraptor.com/products/no-such-box/\nhttps://ripraptor.com/products/prismatic-evolutions-elite-trainer-box/\n"
            "What is in the box: Mystery crate\nSources: none"))
        self.assertTrue(checked.can_save)
        notes = " ".join(checked.read.notes)
        self.assertIn("Stray words here", notes)
        self.assertIn("no product at https://ripraptor.com/products/no-such-box/", notes)
        self.assertIn("\"Mystery crate\" is not a product type", notes)
        self.assertEqual(checked.read.sections[0].products, [self.etb])
        self.assertIn("Fill in the last check and the next review.", checked.publish)
        self.assertFalse(checked.can_publish)

    def test_files_it_cannot_read(self):
        for name, data, expected in (("guide.doc", b"\xd0\xcf\x11\xe0 old word", "Save it as a Word file (.docx)"),
                                     ("guide.docx", b"PK\x03\x04 not really", "not a Word file the site can read"),
                                     ("big.txt", b"x" * (importer.MAX_BYTES + 1), "over 5 MB")):
            with self.subTest(name):
                with self.assertRaises(ValueError) as caught:
                    importer.lines_from_upload(name, data)
                self.assertIn(expected, str(caught.exception))


class SavingTests(ImportCase):
    def test_importing_the_same_address_again_replaces_the_guide(self):
        guide = importer.save(importer.check(importer.text_lines(template.TEXT)), publish=True)
        self.assertTrue(guide.is_published)
        published_at = guide.published_at
        shorter = template.TEXT.split("## Is this price fair?")[0]
        again = importer.save(importer.check(importer.text_lines(shorter)), publish=True)
        self.assertEqual(again.pk, guide.pk)
        self.assertEqual(again.sections.count(), 1)
        self.assertEqual(again.published_at, published_at)
        self.assertEqual(Guide.objects.count(), 1)

    def test_publish_is_refused_while_something_stops_it(self):
        text_ = template.TEXT.replace("Next review: 20 Jan 2027\n", "")
        guide = importer.save(importer.check(importer.text_lines(text_)), publish=True)
        self.assertFalse(guide.is_published)


class AdminImportTests(ImportCase):
    def setUp(self):
        super().setUp()
        user = get_user_model().objects.create_user("marketing", password="pw", is_staff=True)
        user.groups.add(Group.objects.get(name="Guide editors"))
        self.client.force_login(user)
        self.url = reverse("admin:guides_guide_import")

    def test_a_marketing_login_sees_guides_and_the_import_button(self):
        page = self.client.get(reverse("admin:guides_guide_changelist"))
        self.assertContains(page, f'href="{self.url}"')
        home = self.client.get(reverse("admin:index")).content.decode()
        self.assertIn("/admin/guides/guide/", home)
        self.assertNotIn("/admin/catalogue/retailer/", home)
        self.assertEqual(self.client.get(reverse("admin:catalogue_retailer_changelist")).status_code, 403)

    def test_the_templates_download(self):
        word = self.client.get(reverse("admin:guides_guide_template_docx"))
        self.assertIn("RipRaptor guide template.docx", word["Content-Disposition"])
        self.assertEqual(importer.docx_lines(word.content), importer.text_lines(template.TEXT))
        text_ = self.client.get(reverse("admin:guides_guide_template_txt"))
        self.assertEqual(text_.content.decode(), template.TEXT)

    def test_check_then_publish_from_a_word_file(self):
        upload = SimpleUploadedFile("Parents guide.docx", template.docx())
        page = self.client.post(self.url, {"action": "check", "file": upload})
        self.assertContains(page, "Web address: <strong>/guides/parents/</strong>.")
        self.assertContains(page, "A new guide.")
        self.assertContains(page, "Price card: Prismatic Evolutions Elite Trainer Box")
        self.assertContains(page, "Save and publish")
        self.assertFalse(Guide.objects.exists())   # nothing saved by checking
        checked_text = page.context["checked_text"]
        done = self.client.post(self.url, {"action": "publish_now", "checked_text": checked_text})
        guide = Guide.objects.get()
        self.assertRedirects(done, f"{self.url}?saved={guide.pk}")
        self.assertTrue(guide.is_published)
        page = self.client.get(f"{self.url}?saved={guide.pk}")
        self.assertContains(page, "published")
        self.assertContains(page, 'href="/guides/parents/"')

    def test_a_pasted_draft_then_publish_it(self):
        page = self.client.post(self.url, {"action": "check", "text": template.TEXT})
        self.client.post(self.url, {"action": "draft", "checked_text": page.context["checked_text"]})
        guide = Guide.objects.get()
        self.assertFalse(guide.is_published)
        self.assertContains(self.client.get(f"{self.url}?saved={guide.pk}"), "Publish it now")
        self.client.post(self.url, {"action": "publish", "guide": guide.pk})
        guide.refresh_from_db()
        self.assertTrue(guide.is_published)

    def test_problems_show_and_nothing_saves(self):
        page = self.client.post(self.url, {"action": "check", "text": "# A guide!\n\n## Q"})
        self.assertContains(page, "Fix these in your file, then check again")
        self.assertContains(page, "remove the exclamation mark")
        self.assertNotContains(page, "Save as a draft")
        self.client.post(self.url, {"action": "draft", "checked_text": "# A guide!\n\n## Q"})
        self.assertFalse(Guide.objects.exists())
        page = self.client.post(self.url, {"action": "check", "file": SimpleUploadedFile("g.pdf", b"%PDF-1.4")})
        self.assertContains(page, "The guide could not be read")

    def test_replacing_a_live_guide_warns_that_a_draft_takes_it_down(self):
        importer.save(importer.check(importer.text_lines(template.TEXT)), publish=True)
        page = self.client.post(self.url, {"action": "check", "text": template.TEXT})
        self.assertContains(page, "This replaces the guide already there, which is live.")
        self.assertContains(page, "takes it off the site until it is published")

    def test_the_owners_own_pages_stay_closed_to_a_marketing_login(self):
        for url in (reverse("web:dashboard"), reverse("insights"), reverse("checks"), reverse("crawl")):
            with self.subTest(url):
                self.assertEqual(self.client.get(url).status_code, 403)
        home = self.client.get(reverse("admin:index")).content.decode()
        for words in ("Dashboard", "Things to check", "Crawl health", "Insights", "new message"):
            with self.subTest(words):
                self.assertNotIn(words, home)
        self.assertIn("Guides: advice pages with live prices", home)

    def test_the_owner_still_opens_them(self):
        owner = get_user_model().objects.create_superuser("owner", "owner@example.com", "pw")
        owner.groups.add(Group.objects.get(name="Guide editors"))   # even in the group, the owner gets in
        self.client.force_login(owner)
        self.assertEqual(self.client.get(reverse("insights")).status_code, 200)
        self.assertContains(self.client.get(reverse("admin:index")), "Things to check")
