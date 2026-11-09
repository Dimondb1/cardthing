"""The visitor check (web/botcheck.py). The owner, 9 Nov 2026: "I thought we could prevent these bots ourselves".

A first visit from outside the UK and Ireland works out a small sum in the browser before a page shows, and keeps a
pass for its address for a week. Search engines proven by their addresses, AI assistants and link previews by name,
staff, the files pages load and the files crawlers read are never checked."""

import json
import re
import time
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core import signing
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from catalogue.models import VisitorCheck
from catalogue.testing import make_game
from web import botcheck

COUNTRIES = {"81.2.69.160": "GB", "87.32.0.1": "IE", "203.0.113.5": "US", "203.0.113.6": "US",
             "198.51.100.7": "BR", "66.249.66.1": "US", "157.55.39.1": "US", "2001:4860:4801:10::1": "US"}
US, OTHER_US = "203.0.113.5", "203.0.113.6"
CHROME = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36"


def country_of(ip):
    if ip.startswith("2001:db8:"):
        return "DE"
    return COUNTRIES.get(ip, "")


def solve(task, zeros=botcheck.ZEROS):
    n = 0
    while not botcheck.solves(task, str(n), zeros):
        n += 1
    return str(n)


class CheckTests(TestCase):
    def setUp(self):
        cache.clear()
        make_game()
        patcher = mock.patch("catalogue.geo.country_of", side_effect=country_of)
        patcher.start()
        self.addCleanup(patcher.stop)

    def visit(self, path="/", ip=US, agent=CHROME, **extra):
        return self.client.get(path, REMOTE_ADDR=ip, HTTP_USER_AGENT=agent, **extra)

    def checked(self, response):
        return response.status_code == 200 and b"data-check " in response.content

    def task_of(self, response):
        return re.search(r'data-task="([^"]+)"', response.content.decode()).group(1)

    def answer(self, task, answer_value, target="/", ip=US):
        return self.client.get(botcheck.check_url(task, answer_value, target), REMOTE_ADDR=ip, HTTP_USER_AGENT=CHROME)

    def test_visitors_from_the_uk_and_ireland_and_from_nowhere_known_are_never_checked(self):
        for ip in ("81.2.69.160", "87.32.0.1", "127.0.0.1", "10.0.0.4"):
            with self.subTest(ip=ip):
                response = self.visit(ip=ip)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(self.checked(response))

    def test_a_first_visit_from_abroad_gets_the_check_never_kept_or_indexed(self):
        response = self.visit("/games/pokemon/?type=booster_box")
        self.assertTrue(self.checked(response))
        self.assertEqual(response["Cache-Control"], "no-store, private")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        html = response.content.decode()
        # One line, the owner's (9 Nov 2026): "just say checking to make sure you are a human 😊. That's all."
        self.assertIn('<h1 class="botcheck__title">Checking to make sure you are a human 😊</h1>', html)
        self.assertNotIn("botcheck__body", html)
        self.assertIn('data-next="/games/pokemon/?type=booster_box"', html)
        self.assertIn(f'data-zeros="{botcheck.ZEROS}"', html)
        self.assertRegex(html, r'<script nonce="[^"]+" src="[^"]*botcheck\.js"')
        self.assertIn("Turn on JavaScript to continue to RipRaptor.", html)
        self.assertIn("data-check-status hidden>One moment", html)   # shown by the script, so not with it off
        self.assertIn('data-cookies="Turn on cookies', html)
        self.assertTrue(self.checked(self.visit(ip="198.51.100.7")))
        self.assertTrue(botcheck.needs_check(self.request("/", method="head")))

    def test_a_data_feed_from_abroad_with_no_pass_is_refused(self):
        response = self.visit(reverse("web:search_api") + "?q=etb")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(response.content), {"error": "check"})
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(self.visit(reverse("web:search_api") + "?q=etb", ip="81.2.69.160").status_code, 200)

    def test_files_pages_load_and_files_crawlers_read_are_never_checked(self):
        for path in ("/robots.txt", "/sitemap.xml", "/llms.txt", "/static/css/site.css", "/media/x.png",
                     "/feeds/deals/", "/abc123.txt", "/admin/login/", "/check/", "/favicon.ico"):
            with self.subTest(path=path):
                self.assertFalse(botcheck.needs_check(self.request(path)))
                self.assertFalse(self.checked(self.visit(path)))
        # A .txt file deeper in is an ordinary page and checked like one.
        self.assertTrue(botcheck.needs_check(self.request("/games/x.txt")))

    def test_only_reading_is_checked(self):
        self.assertFalse(botcheck.needs_check(self.request("/", method="post")))

    def request(self, path, ip=US, agent=CHROME, method="get"):
        request = getattr(RequestFactory(), method)(path, REMOTE_ADDR=ip, HTTP_USER_AGENT=agent)
        request.user = AnonymousUser()
        return request

    def test_a_right_answer_earns_a_pass_for_a_week_and_goes_on_to_the_page_asked_for(self):
        first = self.visit("/games/pokemon/")
        task = self.task_of(first)
        response = self.answer(task, solve(task), "/games/pokemon/")
        self.assertEqual((response.status_code, response["Location"]), (302, "/games/pokemon/"))
        cookie = response.cookies[botcheck.COOKIE]
        self.assertEqual(cookie["max-age"], 7 * 24 * 3600)
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertTrue(cookie["secure"])
        self.assertIn("no-cache", response["Cache-Control"])
        # The pass opens every page, and the data feeds, for that address.
        self.assertFalse(self.checked(self.visit("/games/pokemon/")))
        self.assertFalse(self.checked(self.visit("/")))
        self.assertEqual(self.visit(reverse("web:search_api") + "?q=etb").status_code, 200)
        # The same pass from another address is no pass: that address does the work too.
        self.assertTrue(self.checked(self.visit(ip=OTHER_US)))

    def test_a_pass_is_bound_to_the_network_of_an_ipv6_address(self):
        here, same_network, elsewhere = "2001:db8:1:2::5", "2001:db8:1:2:aaaa::99", "2001:db8:1:3::5"
        task = self.task_of(self.visit(ip=here))
        self.answer(task, solve(task), ip=here)
        self.assertFalse(self.checked(self.visit(ip=same_network)))
        self.assertTrue(self.checked(self.visit(ip=elsewhere)))
        self.assertEqual(botcheck.address_key("2001:DB8:1:2::5"), "2001:db8:1:2::")
        self.assertEqual(botcheck.address_key("203.0.113.5"), "203.0.113.5")

    def test_a_wrong_answer_a_late_one_or_one_from_another_address_earns_nothing(self):
        task = self.task_of(self.visit())
        right = solve(task)
        wrong = next(str(n) for n in range(10**6) if not botcheck.solves(task, str(n)))
        late_task = self.late_task()
        cases = {
            "wrong": (task, wrong, US),
            "not a number": (task, right + "x", US),
            "another address": (task, right, OTHER_US),
            "late": (late_task, solve(late_task), US),
            "made up": ("made-up-task", "1", US),
        }
        for name, (given_task, given, ip) in cases.items():
            with self.subTest(name):
                response = self.answer(given_task, given, "/games/pokemon/", ip=ip)
                self.assertEqual((response.status_code, response["Location"]), (302, "/games/pokemon/"))
                self.assertNotIn(botcheck.COOKIE, response.cookies)
        self.assertTrue(self.checked(self.visit()))

    def late_task(self):
        with mock.patch("django.core.signing.time.time", return_value=time.time() - botcheck.TASK_SECONDS - 60):
            return botcheck.task_for(US)

    def test_a_browser_driven_by_a_script_earns_no_pass(self):
        task = self.task_of(self.visit())
        for agent in ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/130.0 "
                      "Safari/537.36", "Mozilla/5.0 PhantomJS/2.1.1", "Mozilla/5.0 Playwright/1.48"):
            with self.subTest(agent=agent):
                response = self.client.get(botcheck.check_url(task, solve(task), "/"), REMOTE_ADDR=US,
                                           HTTP_USER_AGENT=agent)
                self.assertEqual(response["Location"], "/")
                self.assertNotIn(botcheck.COOKIE, response.cookies)
        # The page itself declines to work the sum for one (web/static/js/botcheck.js).
        from django.contrib.staticfiles import finders

        script = open(finders.find("js/botcheck.js"), encoding="utf-8").read()
        self.assertIn("navigator.webdriver === true", script)
        self.assertIn("HeadlessChrome", script)

    def test_the_checks_shown_and_passed_today_are_counted_for_who_is_online(self):
        self.assertEqual(botcheck.today_counts(), {"shown": 0, "passed": 0})
        task = self.task_of(self.visit())
        self.visit(ip=OTHER_US)
        self.visit(reverse("web:search_api") + "?q=etb")      # a data feed refused is not a check shown
        self.visit(ip="81.2.69.160")
        self.answer(task, solve(task))
        self.answer(task, "x")                                 # a wrong answer is not a pass
        self.assertEqual(botcheck.today_counts(), {"shown": 2, "passed": 1})

        owner = get_user_model().objects.create_superuser("boss", password="x")
        self.client.force_login(owner)
        with override_settings(RIPRAPTOR_TWO_STEP_REQUIRED=False):
            page = self.client.get(reverse("online"))
            self.assertContains(page, "Visitor check today (work it out, then tap Continue): shown 2 times to first "
                                      "visits from abroad, passed 1 time.")
            row = VisitorCheck.load()
            row.on = False
            row.save()
            self.assertContains(self.client.get(reverse("online")), "The visitor check is off")

    def test_a_made_up_pass_is_no_pass(self):
        self.client.cookies[botcheck.COOKIE] = signing.dumps(botcheck.address_key(US), salt="another.salt")
        self.assertTrue(self.checked(self.visit()))
        self.client.cookies[botcheck.COOKIE] = "203.0.113.5"
        self.assertTrue(self.checked(self.visit()))

    def test_the_answer_only_ever_goes_on_to_this_site(self):
        task = self.task_of(self.visit())
        right = solve(task)
        for target in ("//evil.example/", "https://evil.example/", "/\\evil.example/", "\\\\evil.example",
                       "/\t/evil.example/", "javascript:alert(1)", "evil.example", ""):
            with self.subTest(target=target):
                self.assertEqual(self.answer(task, right, target)["Location"], "/")
        self.assertEqual(self.answer(task, right, "/search/?q=etb&page=2")["Location"], "/search/?q=etb&page=2")

    def test_search_engines_pass_only_when_their_addresses_prove_who_they_are(self):
        agent = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
        names = {"66.249.66.1": "crawl-66-249-66-1.googlebot.com.", US: "home-1.example-isp.net",
                 "157.55.39.1": "msnbot-157-55-39-1.search.msn.com",
                 "2001:4860:4801:10::1": "crawl-2001-4860-4801-10--1.googlebot.com"}
        forward = {"crawl-66-249-66-1.googlebot.com": ["66.249.66.1"], "home-1.example-isp.net": [US],
                   "msnbot-157-55-39-1.search.msn.com": ["157.55.39.1"],
                   "crawl-2001-4860-4801-10--1.googlebot.com": ["2001:4860:4801:0010:0:0:0:1"]}

        def addresses(host, port):
            return [(None, None, None, "", (found, 0)) for found in forward[host]]

        with mock.patch("socket.gethostbyaddr", side_effect=lambda ip: (names[ip], [], [ip])) as lookup, \
                mock.patch("socket.getaddrinfo", side_effect=addresses):
            self.assertFalse(self.checked(self.visit(ip="66.249.66.1", agent=agent)))
            self.assertFalse(self.checked(self.visit(ip="66.249.66.1", agent=agent)))
            self.assertEqual(lookup.call_count, 1)   # remembered a day
            # Calling itself Googlebot from a home connection gets the check like anyone else.
            self.assertTrue(self.checked(self.visit(ip=US, agent=agent)))
            # Bing's name is not Google's.
            self.assertTrue(self.checked(self.visit(ip="157.55.39.1", agent=agent)))
            bing = "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"
            self.assertFalse(self.checked(self.visit(ip="157.55.39.1", agent=bing)))
            # Googlebot over IPv6: its name looks up to the same address, written out longhand.
            self.assertFalse(self.checked(self.visit(ip="2001:4860:4801:10::1", agent=agent)))

    def test_a_rented_google_cloud_address_does_not_prove_googlebot(self):
        rented = (lambda ip: ("1.2.3.4.bc.googleusercontent.com", [], [ip]), lambda host: ["203.0.113.12"])
        self.assertFalse(botcheck.verified("203.0.113.12", botcheck.VERIFIED_CRAWLERS["googlebot"], *rented))
        self.assertTrue(botcheck.verified("203.0.113.12", botcheck.VERIFIED_CRAWLERS["google-site-verification"],
                                          *rented))

    def test_a_name_that_leads_somewhere_else_proves_nothing(self):
        self.assertFalse(botcheck.verified("203.0.113.9", (".googlebot.com",),
                                           lookup=lambda ip: ("crawl.googlebot.com", [], [ip]),
                                           forward=lambda host: ["66.249.66.9"]))
        self.assertFalse(botcheck.verified("203.0.113.10", (".googlebot.com",),
                                           lookup=mock.Mock(side_effect=OSError("no name"))))

    def test_a_lookup_that_hangs_is_given_up_on_and_asked_again_later(self):
        def slow(ip):
            time.sleep(0.5)
            return ("crawl.googlebot.com", [], [ip])

        with mock.patch.object(botcheck, "LOOKUP_SECONDS", 0.05), \
                mock.patch.object(botcheck.cache, "set", wraps=botcheck.cache.set) as kept:
            started = time.monotonic()
            self.assertFalse(botcheck.verified("203.0.113.11", (".googlebot.com",), lookup=slow,
                                               forward=lambda host: ["203.0.113.11"]))
            self.assertLess(time.monotonic() - started, 0.4)
        self.assertEqual(kept.call_args.args[2], botcheck.UNANSWERED_SECONDS)

    def test_ai_assistants_and_link_previews_pass_by_name(self):
        for agent in ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.2; "
                      "+https://openai.com/gptbot",
                      "Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
                      "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
                      "WhatsApp/2.23.20.0", "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"):
            with self.subTest(agent=agent):
                self.assertFalse(self.checked(self.visit(agent=agent)))

    @override_settings(RIPRAPTOR_TWO_STEP_REQUIRED=False)
    def test_staff_are_never_checked(self):
        staff = get_user_model().objects.create_user("owner", password="x", is_staff=True)
        self.client.force_login(staff)
        self.assertFalse(self.checked(self.visit()))

    def test_the_owner_can_switch_it_off_or_name_more_countries_and_it_applies_at_once(self):
        self.assertEqual(botcheck.current(), {"on": True, "countries": ["GB", "IE"], "mode": "tap"})
        row = VisitorCheck.load()
        row.countries = "gb; ie, US"
        row.save()
        self.assertFalse(self.checked(self.visit()))
        self.assertTrue(self.checked(self.visit(ip="198.51.100.7")))
        row.countries, row.on = "GB, IE", False
        row.save()
        self.assertFalse(self.checked(self.visit(ip="198.51.100.7")))
        self.assertEqual(self.visit(reverse("web:search_api") + "?q=etb").status_code, 200)

    def set_mode(self, mode):
        row = VisitorCheck.load()
        row.mode = mode
        row.save()

    def test_a_first_visit_from_abroad_taps_continue_after_the_sum_unless_the_owner_says_otherwise(self):
        html = self.visit().content.decode()
        self.assertIn('data-check-go hidden>Continue to RipRaptor</button>', html)   # shown by the script
        from django.contrib.staticfiles import finders

        script = open(finders.find("js/botcheck.js"), encoding="utf-8").read()
        self.assertIn("event.isTrusted", script)
        self.set_mode("work")
        html = self.visit().content.decode()
        self.assertTrue(self.checked(self.visit()))
        self.assertNotIn("data-check-go", html)
        self.assertNotIn("Continue to RipRaptor", html)

    def test_turned_away_a_visit_from_abroad_sees_why_and_no_way_on(self):
        task = self.task_of(self.visit())
        self.answer(task, solve(task))                       # a pass earned before the owner turned them away
        self.set_mode("away")
        response = self.visit("/games/pokemon/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["Cache-Control"], "no-store, private")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        html = response.content.decode()
        self.assertIn("RipRaptor is for UK shoppers", html)
        self.assertIn("In the UK on a VPN? Turn it off to continue.", html)
        for absent in ("data-check", "botcheck.js", "data-task", "Continue to RipRaptor"):
            self.assertNotIn(absent, html)
        # A right answer earns nothing while they are turned away, and the data feeds stay shut.
        fresh = botcheck.task_for(US)
        self.assertNotIn(botcheck.COOKIE, self.answer(fresh, solve(fresh), ip=US).cookies)
        self.assertEqual(self.visit(reverse("web:search_api") + "?q=etb").status_code, 403)
        # The UK, staff's countries and the search engines and link previews still come in.
        self.assertEqual(self.visit(ip="81.2.69.160").status_code, 200)
        self.assertEqual(self.visit(agent="facebookexternalhit/1.1").status_code, 200)
        self.assertEqual(botcheck.today_counts()["passed"], 1)

        owner = get_user_model().objects.create_superuser("boss", password="x")
        self.client.force_login(owner)
        with override_settings(RIPRAPTOR_TWO_STEP_REQUIRED=False):
            self.assertContains(self.client.get(reverse("online")), "visits from abroad turned away")

    def test_the_check_costs_no_query_once_its_settings_are_kept(self):
        VisitorCheck.load()
        botcheck.remember()
        with self.assertNumQueries(0):
            self.assertTrue(botcheck.needs_check(self.request("/")))
            self.assertFalse(botcheck.needs_check(self.request("/", ip="81.2.69.160")))
        cache.clear()
        with self.assertNumQueries(1):
            botcheck.needs_check(self.request("/"))

    def test_the_admin_opens_the_one_row(self):
        owner = get_user_model().objects.create_superuser("boss", password="x")
        self.client.force_login(owner)
        with override_settings(RIPRAPTOR_TWO_STEP_REQUIRED=False):
            response = self.client.get(reverse("admin:catalogue_visitorcheck_changelist"))
            row = VisitorCheck.objects.get()
            self.assertRedirects(response, reverse("admin:catalogue_visitorcheck_change", args=[row.pk]))
            page = self.client.get(response["Location"])
        self.assertContains(page, "Countries never checked")
        self.assertEqual(VisitorCheck(countries="gb, ie;fr ,").country_codes(), {"GB", "IE", "FR"})
