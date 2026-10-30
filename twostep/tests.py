import base64
import json
import os
import subprocess
import sys
import tempfile
from io import StringIO
from unittest import mock

from django.conf import settings
from django.contrib.auth import SESSION_KEY as AUTH_SESSION_KEY
from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.core.management import call_command
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import lockout, totp
from .forms import LOCKED, WRONG
from .middleware import TwoStepMiddleware
from .models import TwoStep, hashed
from .views import SESSION_KEY

LOGIN = reverse("admin:login")
SETUP = reverse("twostep_setup")
ADMIN = reverse("admin:index")
PASSWORD = "a long staff password"


def now_code(secret):
    return totp.code_at(secret, totp.current_step())


class TotpTests(TestCase):
    # RFC 6238, appendix B: the SHA-1 secret, and the last six digits of its eight-digit codes.
    SECRET = base64.b32encode(b"12345678901234567890").decode()

    def test_codes_match_the_standard(self):
        for moment, code in ((59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037")):
            self.assertEqual(totp.code_at(self.SECRET, totp.current_step(moment)), code)

    def test_a_code_counts_from_the_step_either_side_but_no_further(self):
        step = totp.current_step(1234567890)
        for offset in (-1, 0, 1):
            code = totp.code_at(self.SECRET, step + offset)
            self.assertEqual(totp.matching_step(self.SECRET, code, now=1234567890), step + offset)
        self.assertIsNone(totp.matching_step(self.SECRET, totp.code_at(self.SECRET, step - 2), now=1234567890))

    def test_a_code_works_once(self):
        step = totp.current_step(1234567890)
        code = totp.code_at(self.SECRET, step)
        self.assertIsNone(totp.matching_step(self.SECRET, code, after=step, now=1234567890))

    def test_spaces_are_ignored_and_anything_but_six_digits_is_refused(self):
        code = totp.code_at(self.SECRET, totp.current_step(59))
        self.assertEqual(totp.matching_step(self.SECRET, f"{code[:3]} {code[3:]}", now=59), 1)
        for typed in ("", "12345", "1234567", "abcdef"):
            self.assertIsNone(totp.matching_step(self.SECRET, typed, now=59))

    def test_new_secrets_are_long_random_and_readable(self):
        first, second = totp.new_secret(), totp.new_secret()
        self.assertNotEqual(first, second)
        self.assertEqual(len(base64.b32decode(first)), 20)
        self.assertEqual(totp.grouped("ABCDEFGHIJ"), "ABCD EFGH IJ")

    def test_the_app_link_carries_the_secret_and_the_site(self):
        link = totp.app_link("ABCD", "ben", "RipRaptor")
        self.assertTrue(link.startswith("otpauth://totp/RipRaptor%3Aben?"))
        self.assertIn("secret=ABCD", link)
        self.assertIn("issuer=RipRaptor", link)


# A quick hasher: these tests sign in wrong many times.
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class StaffCase(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = get_user_model().objects.create_user("owner", password=PASSWORD, is_staff=True,
                                                          is_superuser=True)

    def sign_in(self, password=PASSWORD, code="", **extra):
        return self.client.post(LOGIN, {"username": "owner", "password": password, "code": code, "next": ADMIN},
                                **extra)

    def signed_in(self):
        return self.client.session.get(AUTH_SESSION_KEY) == str(self.staff.pk)

    def turn_on(self):
        two_step = TwoStep.objects.create(user=self.staff, pending_secret=totp.new_secret())
        self.codes = two_step.confirm(now_code(two_step.pending_secret))
        # The code used to confirm cannot be used again; the tests sign in with the next step's.
        two_step.last_step = 0
        two_step.save()
        return two_step


class LockoutTests(StaffCase):
    def test_ten_wrong_tries_turn_the_address_away_even_with_the_right_password(self):
        for _ in range(lockout.LIMIT):
            self.assertContains(self.sign_in("wrong"), WRONG)
        response = self.sign_in()
        self.assertContains(response, LOCKED)
        self.assertNotContains(response, WRONG)
        self.assertFalse(self.signed_in())

    def test_the_block_ends_after_fifteen_minutes(self):
        for _ in range(lockout.LIMIT):
            self.sign_in("wrong")
        later = lockout.time.time() + lockout.WINDOW + 1
        with mock.patch.object(lockout.time, "time", return_value=later):
            self.assertEqual(self.sign_in().status_code, 302)
        self.assertTrue(self.signed_in())

    def test_the_password_is_not_checked_while_blocked(self):
        for _ in range(lockout.LIMIT):
            self.sign_in("wrong")
        with mock.patch("django.contrib.auth.forms.authenticate") as checked:
            self.sign_in()
        checked.assert_not_called()

    def test_a_right_sign_in_clears_the_count(self):
        for _ in range(lockout.LIMIT - 1):
            self.sign_in("wrong")
        self.assertEqual(self.sign_in().status_code, 302)
        self.client.logout()
        for _ in range(lockout.LIMIT - 1):
            self.sign_in("wrong")
        self.assertEqual(self.sign_in().status_code, 302)

    def test_another_address_is_not_blocked(self):
        for _ in range(lockout.LIMIT):
            self.sign_in("wrong")
        self.assertEqual(self.sign_in(REMOTE_ADDR="10.0.0.2").status_code, 302)

    def test_the_address_is_the_one_caddy_adds_last(self):
        for _ in range(lockout.LIMIT):
            self.sign_in("wrong", HTTP_X_FORWARDED_FOR="1.2.3.4, 5.6.7.8")
        self.assertContains(self.sign_in(HTTP_X_FORWARDED_FOR="9.9.9.9, 5.6.7.8"), LOCKED)
        self.assertEqual(self.sign_in(HTTP_X_FORWARDED_FOR="1.2.3.4, 5.6.7.9").status_code, 302)

    def test_an_empty_form_is_not_a_wrong_try(self):
        for _ in range(lockout.LIMIT + 2):
            self.client.post(LOGIN, {"username": "", "password": ""})
        self.assertEqual(self.sign_in().status_code, 302)

    def test_a_wrong_code_counts(self):
        self.turn_on()
        for _ in range(lockout.LIMIT):
            self.assertContains(self.sign_in(code="000000"), WRONG)
        self.assertContains(self.sign_in(code=now_code(self.staff.two_step.secret)), LOCKED)


@override_settings(RIPRAPTOR_TWO_STEP_REQUIRED=True)
class SetupTests(StaffCase):
    def test_a_staff_sign_in_without_an_app_goes_to_set_one_up(self):
        response = self.sign_in()
        self.assertRedirects(response, ADMIN, fetch_redirect_response=False)
        response = self.client.get(ADMIN)
        self.assertRedirects(response, f"{SETUP}?next=/admin/")
        page = self.client.get(response["Location"])
        secret = TwoStep.objects.get(user=self.staff).pending_secret
        self.assertContains(page, totp.grouped(secret))
        self.assertContains(page, "otpauth://totp/RipRaptor%3Aowner?")
        self.assertContains(page, 'value="/admin/"')
        # A reload shows the same key, so the one already in the app still matches.
        self.assertContains(self.client.get(SETUP), totp.grouped(secret))

    def test_the_dashboard_waits_for_it_too(self):
        self.client.force_login(self.staff)
        self.assertRedirects(self.client.get(reverse("web:dashboard")), f"{SETUP}?next=/dashboard/",
                             fetch_redirect_response=False)

    def test_a_wrong_first_code_keeps_it_off(self):
        self.client.force_login(self.staff)
        self.client.get(SETUP)
        response = self.client.post(SETUP, {"action": "confirm", "code": "000000"})
        self.assertContains(response, "That code is not the one the app shows now")
        self.assertFalse(TwoStep.objects.get(user=self.staff).is_on)

    def test_the_first_right_code_turns_it_on_and_shows_backup_codes_once(self):
        self.client.force_login(self.staff)
        self.client.get(SETUP)
        secret = TwoStep.objects.get(user=self.staff).pending_secret
        response = self.client.post(SETUP, {"action": "confirm", "code": now_code(secret), "next": "/admin/checks/"})
        two_step = TwoStep.objects.get(user=self.staff)
        self.assertEqual((two_step.secret, two_step.pending_secret), (secret, ""))
        self.assertIsNotNone(two_step.confirmed_at)
        self.assertEqual(len(response.context["codes"]), 8)
        self.assertEqual(two_step.backup_codes, [hashed(code) for code in response.context["codes"]])
        self.assertContains(response, 'href="/admin/checks/"')
        # This sign-in passed: Admin opens, and the set-up page now says it is on, without the codes.
        self.assertEqual(self.client.get(ADMIN).status_code, 200)
        page = self.client.get(SETUP)
        self.assertContains(page, "Two-step login is on")
        self.assertNotContains(page, response.context["codes"][0])

    def test_next_never_leaves_the_site(self):
        self.client.force_login(self.staff)
        self.client.get(SETUP)
        secret = TwoStep.objects.get(user=self.staff).pending_secret
        response = self.client.post(SETUP, {"action": "confirm", "code": now_code(secret),
                                            "next": "https://elsewhere.example/"})
        self.assertNotContains(response, "elsewhere.example")

    def test_the_setup_page_reads_little(self):
        self.client.force_login(self.staff)
        self.client.get(SETUP)
        # The session and user, whether two-step is on, the row, then the session saved (with its savepoint).
        with self.assertNumQueries(7):
            self.client.get(SETUP)


@override_settings(RIPRAPTOR_TWO_STEP_REQUIRED=True)
class SignInTests(StaffCase):
    def setUp(self):
        super().setUp()
        self.two_step = self.turn_on()

    def test_the_password_alone_is_not_enough(self):
        response = self.sign_in()
        self.assertContains(response, WRONG)
        self.assertFalse(self.signed_in())

    def test_the_password_and_the_code_get_in(self):
        self.assertRedirects(self.sign_in(code=now_code(self.two_step.secret)), ADMIN, fetch_redirect_response=False)
        self.assertEqual(self.client.session[SESSION_KEY], self.staff.pk)
        self.assertEqual(self.client.get(ADMIN).status_code, 200)
        self.assertEqual(self.client.get(reverse("web:dashboard")).status_code, 200)

    def test_a_code_works_once(self):
        code = now_code(self.two_step.secret)
        self.sign_in(code=code)
        self.client.logout()
        self.assertContains(self.sign_in(code=code), WRONG)

    def test_the_wrong_password_with_the_right_code_says_the_same_as_any_mistake(self):
        self.assertContains(self.sign_in("wrong", code=now_code(self.two_step.secret)), WRONG)

    def test_a_backup_code_gets_in_once(self):
        code = self.codes[0]
        self.assertEqual(self.sign_in(code=code.upper().replace("-", " ")).status_code, 302)
        self.client.logout()
        self.assertContains(self.sign_in(code=code), WRONG)
        self.assertEqual(len(TwoStep.objects.get(pk=self.two_step.pk).backup_codes), 7)

    def test_a_session_from_before_it_was_on_signs_in_again(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("checks"))
        self.assertRedirects(response, f"{LOGIN}?next=/admin/checks/", fetch_redirect_response=False)
        self.assertFalse(self.signed_in())

    def test_the_login_page_asks_for_the_code(self):
        page = self.client.get(LOGIN)
        self.assertContains(page, 'name="code"')
        self.assertContains(page, 'autocomplete="one-time-code"')

    def test_new_backup_codes_replace_the_old(self):
        self.sign_in(code=now_code(self.two_step.secret))
        response = self.client.post(SETUP, {"action": "codes"})
        new = response.context["codes"]
        self.assertEqual(TwoStep.objects.get(pk=self.two_step.pk).backup_codes, [hashed(code) for code in new])
        self.assertNotIn(hashed(self.codes[0]), TwoStep.objects.get(pk=self.two_step.pk).backup_codes)

    def test_a_new_phone_takes_over_only_once_it_gives_a_code(self):
        self.sign_in(code=now_code(self.two_step.secret))
        self.client.post(SETUP, {"action": "new"})
        pending = TwoStep.objects.get(pk=self.two_step.pk).pending_secret
        self.assertNotEqual(pending, self.two_step.secret)
        # Until then the old phone still signs in.
        self.client.logout()
        self.two_step.refresh_from_db()
        self.two_step.last_step = 0
        self.two_step.save()
        self.assertEqual(self.sign_in(code=now_code(self.two_step.secret)).status_code, 302)
        self.client.post(SETUP, {"action": "confirm", "code": now_code(pending)})
        self.assertEqual(TwoStep.objects.get(pk=self.two_step.pk).secret, pending)

    def test_keeping_the_old_phone_drops_the_new_key(self):
        self.sign_in(code=now_code(self.two_step.secret))
        self.client.post(SETUP, {"action": "new"})
        self.client.post(SETUP, {"action": "cancel"})
        two_step = TwoStep.objects.get(pk=self.two_step.pk)
        self.assertEqual((two_step.secret, two_step.pending_secret), (self.two_step.secret, ""))

    def test_the_reset_command_turns_it_off_for_a_lost_phone(self):
        out = StringIO()
        call_command("reset_two_step", "owner", stdout=out)
        self.assertIn("reset for owner", out.getvalue())
        self.assertFalse(TwoStep.objects.filter(user=self.staff).exists())
        self.assertEqual(self.sign_in().status_code, 302)


class MiddlewareTests(StaffCase):
    def run_through(self, request):
        seen = {}

        def view(request):
            seen["user"] = request.user
            return HttpResponse()

        return TwoStepMiddleware(view)(request), seen

    def request(self, path, cookie=True):
        request = RequestFactory().get(path)
        if cookie:
            request.COOKIES["sessionid"] = "x"
        SessionMiddleware(lambda r: None).process_request(request)
        request.user = self.staff
        return request

    @override_settings(RIPRAPTOR_TWO_STEP_REQUIRED=True)
    def test_staff_who_have_not_passed_are_visitors_on_public_pages(self):
        _, seen = self.run_through(self.request("/guides/"))
        self.assertFalse(seen["user"].is_authenticated)

    @override_settings(RIPRAPTOR_TWO_STEP_REQUIRED=True)
    def test_a_passed_sign_in_keeps_its_staff_powers(self):
        request = self.request("/guides/")
        request.session[SESSION_KEY] = self.staff.pk
        _, seen = self.run_through(request)
        self.assertEqual(seen["user"], self.staff)

    @override_settings(RIPRAPTOR_TWO_STEP_REQUIRED=True)
    def test_a_visitor_without_a_session_cookie_is_not_looked_at(self):
        request = self.request("/", cookie=False)
        self.run_through(request)
        self.assertFalse(request.session.accessed)

    def test_off_on_a_development_machine(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(ADMIN).status_code, 200)


class ServerSettingsTests(SimpleTestCase):
    """What the server runs with (DJANGO_DEBUG=0): two-step required, and browsers told to keep to https."""

    def server_settings(self, **extra):
        code = ("import json, django; django.setup(); from django.conf import settings as s; "
                "print(json.dumps([s.RIPRAPTOR_TWO_STEP_REQUIRED, s.SECURE_HSTS_SECONDS, "
                "s.SECURE_HSTS_INCLUDE_SUBDOMAINS, s.SECURE_HSTS_PRELOAD]))")
        env = {key: value for key, value in os.environ.items() if not key.startswith(("DJANGO_", "RIPRAPTOR_"))}
        with tempfile.TemporaryDirectory() as folder:
            env.update(DJANGO_SETTINGS_MODULE="ripraptor.settings", DJANGO_DEBUG="0", DJANGO_SECRET_KEY="x" * 50,
                       RIPRAPTOR_CACHE_DIR=folder, **extra)
            done = subprocess.run([sys.executable, "-c", code], cwd=settings.BASE_DIR, env=env, capture_output=True,
                                  text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_the_server_requires_two_step_and_sends_hsts_for_a_year_on_this_address_only(self):
        self.assertEqual(self.server_settings(), [True, 365 * 24 * 3600, False, False])

    def test_two_step_can_be_switched_off_from_the_environment(self):
        self.assertFalse(self.server_settings(RIPRAPTOR_TWO_STEP="0")[0])
