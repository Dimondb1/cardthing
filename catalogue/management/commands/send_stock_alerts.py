"""
Email everyone waiting for a product that a shop now has in stock.

    python manage.py send_stock_alerts
    python manage.py send_stock_alerts --test you@example.com

Each address gets one email per product and is then deleted. Unconfirmed
requests go after a week, confirmed ones after six months. Meant for a cron
entry every ten minutes, after watch_stock. --test sends one message to
check the ZeptoMail settings.
"""

from django.core.management.base import BaseCommand, CommandError

from catalogue import alerts, mail


class Command(BaseCommand):
    help = "Send back-in-stock emails that are due."

    def add_arguments(self, parser):
        parser.add_argument("--test", metavar="EMAIL", help="Send one test message to this address and stop.")

    def handle(self, *args, test=None, **options):
        if not mail.enabled():
            self.stdout.write("Email is not set up (RIPRAPTOR_ZEPTOMAIL_TOKEN, RIPRAPTOR_MAIL_FROM). Nothing sent.")
            return
        if test:
            try:
                text, html = alerts.render_mail("test", {"subject": "RipRaptor test email"})
                # Sent with the unsubscribe header too, so the test proves ZeptoMail accepts it.
                mail.send(test, "RipRaptor test email", text, html, unsubscribe=alerts.settings.RIPRAPTOR_SITE_URL + "/terms/")
            except mail.MailError as exc:
                raise CommandError(str(exc))
            self.stdout.write(f"Sent a test email to {test}.")
            return
        sent, failed = alerts.send_due(stdout=self.stdout)
        self.stdout.write(f"{sent} back-in-stock emails sent, {failed} failed.")
