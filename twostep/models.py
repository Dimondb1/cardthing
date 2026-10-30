import hashlib
import hmac
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from . import totp

BACKUP_CODES = 8
# No 0, o, 1, i or l: a backup code is read off paper.
BACKUP_LETTERS = "abcdefghjkmnpqrstuvwxyz23456789"


def tidy(code):
    """A typed code without spaces or dashes, in lower case."""
    return "".join(code.split()).replace("-", "").lower()


def hashed(code):
    return hashlib.sha256(tidy(code).encode()).hexdigest()


class TwoStep(models.Model):
    """One staff login's authenticator app. Signing in to Admin takes the password and the app's current code
    (or one of the backup codes, each good once) once ``secret`` is set."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="two_step")
    # Set once the first code from the app is right; blank until then.
    secret = models.CharField(max_length=64, blank=True)
    # A secret shown on the set-up page and not yet confirmed with a code: the first one, or a new phone's.
    # The one in ``secret`` keeps working until this one is confirmed.
    pending_secret = models.CharField(max_length=64, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # The 30-second step of the last code used, so a code seen over someone's shoulder cannot be used again.
    last_step = models.BigIntegerField(default=0)
    # SHA-256 of each unused backup code. The codes are shown once and never kept.
    backup_codes = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "two-step login"
        verbose_name_plural = "two-step logins"

    def __str__(self):
        return str(self.user)

    @property
    def is_on(self):
        return bool(self.secret)

    def check_code(self, code):
        """True when ``code`` is the app's current code or an unused backup code, which is then used up."""
        if not self.is_on or not code:
            return False
        step = totp.matching_step(self.secret, code, after=self.last_step)
        if step is not None:
            self.last_step = step
            self.save(update_fields=["last_step"])
            return True
        wanted = hashed(code)
        for stored in self.backup_codes:
            if hmac.compare_digest(stored, wanted):
                self.backup_codes = [other for other in self.backup_codes if other != stored]
                self.save(update_fields=["backup_codes"])
                return True
        return False

    def start(self):
        """The secret to show on the set-up page: the one already shown, so a reload keeps what the app has."""
        if not self.pending_secret:
            self.pending_secret = totp.new_secret()
            self.save()
        return self.pending_secret

    def confirm(self, code):
        """Switch to the pending secret when ``code`` is its current code. Returns the new backup codes, or None."""
        step = totp.matching_step(self.pending_secret, code) if self.pending_secret else None
        if step is None:
            return None
        self.secret, self.pending_secret = self.pending_secret, ""
        self.last_step, self.confirmed_at = step, timezone.now()
        return self.new_backup_codes()

    def new_backup_codes(self):
        """Fresh backup codes, saved hashed. The old ones stop working. Returns them to show once."""
        codes = []
        for _ in range(BACKUP_CODES):
            raw = "".join(secrets.choice(BACKUP_LETTERS) for _ in range(8))
            codes.append(f"{raw[:4]}-{raw[4:]}")
        self.backup_codes = [hashed(code) for code in codes]
        self.save()
        return codes
