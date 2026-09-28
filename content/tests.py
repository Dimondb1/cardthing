import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse

from . import service
from .admin import SiteContentForm
from .formatting import check
from .models import SiteContent
from .registry import ENTRIES, REGISTRY, SECTION_KEYS


def render(source, **context):
    return Template("{% load sitecopy %}" + source).render(Context(context))


class RegistryTests(TestCase):
    def test_every_default_passes_the_style_checks(self):
        for entry in ENTRIES:
            with self.subTest(key=entry.key):
                problems = check(
                    entry.default,
                    kind=entry.kind,
                    allowed=entry.placeholders,
                    optional=entry.optional,
                    legal=entry.legal,
                )
                self.assertEqual(problems, [])

    def test_defaults_have_no_exclamation_marks(self):
        for entry in ENTRIES:
            self.assertNotIn("!", entry.default, entry.key)

    def test_keys_start_with_a_known_section(self):
        for entry in ENTRIES:
            self.assertIn(entry.section, SECTION_KEYS, entry.key)

    def test_every_key_used_in_templates_exists(self):
        template_dirs = [Path(settings.BASE_DIR) / app / "templates" for app in ("web",)]
        tag = re.compile(r'{%\s*(copy|copy_count)\s+["\']([\w.]+)["\']')
        used = set()
        for directory in template_dirs:
            for path in directory.rglob("*.*"):
                for name, key in tag.findall(path.read_text()):
                    if name == "copy_count":
                        used.update({f"{key}.one", f"{key}.other"})
                    else:
                        used.add(key)
        self.assertTrue(used)
        self.assertEqual(sorted(used - set(REGISTRY)), [])


class SyncTests(TestCase):
    def test_migrate_creates_a_row_for_every_key(self):
        self.assertEqual(SiteContent.objects.count(), len(ENTRIES))

    def test_sync_keeps_edited_wording(self):
        row = SiteContent.objects.get(key="home.hero.title")
        row.content = "Sealed TCG prices from UK shops"
        row.save()
        service.sync_defaults()
        row.refresh_from_db()
        self.assertEqual(row.content, "Sealed TCG prices from UK shops")

    def test_unedited_wording_follows_a_new_default(self):
        SiteContent.objects.filter(key="home.hero.title").update(
            content="Old default", default_content="Old default"
        )
        service.sync_defaults()
        row = SiteContent.objects.get(key="home.hero.title")
        self.assertEqual(row.content, REGISTRY["home.hero.title"].default)

    def test_missing_rows_are_recreated(self):
        SiteContent.objects.filter(key="home.hero.title").delete()
        created, _updated, _removed = service.sync_defaults()
        self.assertEqual(created, 1)


class LookupTests(TestCase):
    def test_edited_wording_is_used(self):
        SiteContent.objects.filter(key="home.popular.title").update(content="Most clicked")
        self.assertEqual(service.get("home.popular.title"), "Most clicked")

    def test_missing_row_falls_back_to_default(self):
        SiteContent.objects.filter(key="home.popular.title").delete()
        self.assertEqual(service.get("home.popular.title"), "Popular this week")

    def test_switched_off_row_uses_default(self):
        SiteContent.objects.filter(key="home.popular.title").update(
            content="Most clicked", active=False
        )
        self.assertEqual(service.get("home.popular.title"), "Popular this week")

    def test_blank_required_text_falls_back(self):
        SiteContent.objects.filter(key="home.popular.title").update(content="")
        self.assertEqual(service.get("home.popular.title"), "Popular this week")

    def test_blank_optional_text_is_hidden(self):
        SiteContent.objects.filter(key="home.hero.subtitle").update(content="")
        self.assertEqual(service.get("home.hero.subtitle"), "")

    def test_placeholders_are_filled(self):
        text = service.get("home.savings.save", amount="£4.99")
        self.assertEqual(text, "Save £4.99")

    def test_tag_escapes_values(self):
        html = render('{% copy "browse.results.title" query=q %}', q="<script>")
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_tag_escapes_stored_text(self):
        SiteContent.objects.filter(key="home.popular.title").update(content="<b>Hi</b>")
        self.assertIn("&lt;b&gt;", render('{% copy "home.popular.title" %}'))

    def test_paragraph_text_becomes_paragraphs(self):
        SiteContent.objects.filter(key="about.introduction").update(content="One.\n\nTwo.")
        self.assertEqual(
            render('{% copy "about.introduction" %}'), "<p>One.</p>\n\n<p>Two.</p>"
        )

    def test_as_variable(self):
        html = render('{% copy "home.popular.title" as t %}[{{ t }}]')
        self.assertEqual(html, "[Popular this week]")

    def test_one_query_per_request(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as queries:
            self.client.get(reverse("web:about"))
        lookups = [q for q in queries.captured_queries if "content_sitecontent" in q["sql"]]
        self.assertEqual(len(lookups), 1)


class AdminFormTests(TestCase):
    def form(self, key, content, **extra):
        row = SiteContent.objects.get(key=key)
        data = {"content": content, "active": "on", **extra}
        return SiteContentForm(data=data, instance=row)

    def test_accepts_plain_wording(self):
        self.assertTrue(self.form("home.popular.title", "Most clicked this week").is_valid())

    def test_rejects_em_dash(self):
        form = self.form("home.popular.title", "Popular — this week")
        self.assertFalse(form.is_valid())
        self.assertIn("em dash", str(form.errors))

    def test_rejects_unknown_placeholder(self):
        form = self.form("product.buy.button", "Buy for {cost}")
        self.assertFalse(form.is_valid())
        self.assertIn("{price}", str(form.errors))

    def test_rejects_broken_braces(self):
        self.assertFalse(self.form("product.buy.button", "Buy for {price").is_valid())

    def test_rejects_line_breaks_in_short_text(self):
        self.assertFalse(self.form("home.popular.title", "Popular\nthis week").is_valid())

    def test_rejects_html(self):
        self.assertFalse(self.form("home.popular.title", "<b>Popular</b>").is_valid())

    def test_legal_text_cannot_be_empty(self):
        self.assertFalse(self.form("footer.affiliate_disclosure", "").is_valid())

    def test_legal_text_stays_active(self):
        form = self.form("footer.affiliate_disclosure", "We may earn a commission.", active="")
        self.assertTrue(form.is_valid())
        row = form.save()
        self.assertTrue(row.active)

    def test_restore_default(self):
        SiteContent.objects.filter(key="home.popular.title").update(content="Edited")
        form = self.form("home.popular.title", "Edited again", restore_default="on")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().content, "Popular this week")


class AdminViewTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw")
        self.client.force_login(user)

    def test_lists_load_and_split_legal_text(self):
        site_text = self.client.get(reverse("admin:content_sitecontent_changelist"))
        legal = self.client.get(reverse("admin:content_legaltext_changelist"))
        self.assertContains(site_text, "home.hero.title")
        self.assertNotContains(site_text, "footer.affiliate_disclosure")
        self.assertContains(legal, "footer.affiliate_disclosure")

    def test_search_and_filter(self):
        url = reverse("admin:content_sitecontent_changelist")
        response = self.client.get(url, {"q": "price_drops", "section": "home"})
        self.assertContains(response, "home.price_drops.title")
        self.assertNotContains(response, "product.cheapest.label")

    def test_change_page_shows_default_and_placeholders(self):
        row = SiteContent.objects.get(key="product.buy.button")
        response = self.client.get(reverse("admin:content_sitecontent_change", args=[row.pk]))
        self.assertContains(response, "Default wording")
        self.assertContains(response, "{price}")

    def test_edit_shows_on_the_site_straight_away(self):
        row = SiteContent.objects.get(key="home.hero.title")
        self.client.post(
            reverse("admin:content_sitecontent_change", args=[row.pk]),
            {"content": "Sealed TCG prices, delivered", "active": "on"},
        )
        self.assertContains(self.client.get("/"), "Sealed TCG prices, delivered")

    def test_restore_action(self):
        SiteContent.objects.filter(key="home.hero.title").update(content="Edited")
        row = SiteContent.objects.get(key="home.hero.title")
        self.client.post(
            reverse("admin:content_sitecontent_changelist"),
            {"action": "restore_defaults", "_selected_action": [row.pk]},
        )
        row.refresh_from_db()
        self.assertEqual(row.content, row.default_content)
