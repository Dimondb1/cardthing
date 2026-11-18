"""The admin home (web/templates/admin/ripraptor_index.html): what is waiting first, then the pages in labelled groups,
then every table folded away. The owner, 18 Nov 2026: "The admin area has grown legs and requires tidying and
categorisation"."""

import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from inbox import service


class AdminHomeTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser("owner", "owner@example.com", "pw")
        self.client.force_login(self.owner)

    def home(self):
        return self.client.get(reverse("admin:index")).content.decode()

    def groups(self, html):
        return re.findall(r'<h2 id="rr-[a-z]+">([^<]+)</h2>', html)

    def test_needs_you_then_six_groups_then_the_tables_folded_away(self):
        html = self.home()
        self.assertEqual(self.groups(html),
                         ["Needs you", "Numbers", "Catalogue", "Shops", "Site", "Settings", "Guides", "Admin app"])
        self.assertIn('<details class="rr-box rr-tables">\n    <summary>Every table</summary>', html)
        self.assertLess(html.index("Every table"), html.index('id="content-main"'))   # Django's table list is inside
        self.assertNotIn("Recent actions", html)
        # Each page once, under its group: the timeline under Numbers, Crawl health under Shops.
        numbers = html[html.index('id="rr-numbers"'):html.index('id="rr-catalogue"')]
        self.assertIn(f'href="{reverse("click_timeline")}" class="section">Shop clicks timeline</a>', numbers)
        shops = html[html.index('id="rr-shops"'):html.index('id="rr-site"')]
        self.assertIn(f'href="{reverse("crawl")}" class="section">Crawl health</a>', shops)
        self.assertEqual(html.count(f'href="{reverse("crawl")}" class="section"'), 1)

    def test_needs_you_says_nothing_is_waiting_or_lists_only_what_is(self):
        self.assertIn('<li class="rr-calm">Nothing waiting.</li>', self.home())
        with self.settings(RIPRAPTOR_INBOX_NOTIFY_EMAIL="", RIPRAPTOR_NTFY_TOPIC=""):
            service.start("Can you add Lorcana tins?", name="Sam", email="sam@example.com")
        html = self.home()
        self.assertNotIn("Nothing waiting.", html)
        self.assertIn("<strong>1 new message</strong>", html)
        self.assertNotIn("No new messages", html)

    def test_a_guides_editor_sees_only_the_guides(self):
        from guides.apps import EDITORS

        editor = get_user_model().objects.create_user("editor", password="pw", is_staff=True)
        editor.groups.add(Group.objects.get_or_create(name=EDITORS)[0])
        self.client.force_login(editor)
        self.assertEqual(self.groups(self.home()), ["Guides", "Admin app"])
