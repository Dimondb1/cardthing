from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError

from . import lockout
from .models import TwoStep

# One message for a wrong password and a wrong code, so a guess never learns which part was right.
WRONG = ("Enter the right username and password for a staff account, and the 6-digit code from your "
         "authenticator app once two-step login is set up. The password is case-sensitive.")
LOCKED = "Too many wrong tries from this address. Try again in 15 minutes."


def code_field(label, help_text):
    return forms.CharField(label=label, required=False, max_length=12, strip=True, help_text=help_text,
                           widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric",
                                                         "autocapitalize": "none", "spellcheck": "false"}))


class LoginForm(AdminAuthenticationForm):
    """Admin's sign-in with the authenticator app's code, and a block on an address that keeps getting it wrong."""

    error_messages = {**AdminAuthenticationForm.error_messages, "invalid_login": WRONG}
    code = code_field("Code", "The 6-digit code from your authenticator app, or a backup code. "
                              "Leave it empty until two-step login is set up.")

    def clean(self):
        if lockout.locked(self.request):
            raise ValidationError(LOCKED, code="locked")
        try:
            cleaned = super().clean()
        except ValidationError:
            lockout.failed(self.request)
            raise
        if self.user_cache is None:
            return cleaned   # a field was left empty: that is not a guess
        two_step = TwoStep.objects.filter(user=self.user_cache).first()
        if two_step is not None and two_step.is_on:
            if not two_step.check_code(cleaned.get("code", "")):
                lockout.failed(self.request)
                raise ValidationError(WRONG, code="invalid_login")
            # Read by views.mark_passed once Django has signed the user in.
            self.request.two_step_passed = self.user_cache.pk
        lockout.cleared(self.request)
        return cleaned


class ConfirmForm(forms.Form):
    code = code_field("Code from the app", "The 6 digits the app shows for RipRaptor now.")
