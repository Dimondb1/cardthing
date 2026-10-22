"""
Post new restocks, pre-orders opening, price drops and sets out today to the owner's Discord.

    python manage.py post_discord           # what cron runs every ten minutes
    python manage.py post_discord --test    # one hello to each channel set up, to check the webhooks

Each channel is set up in admin under Discord channels (or, until one is, RIPRAPTOR_DISCORD_WEBHOOK in .env;
see catalogue/discord.py). A channel's first run for an update posts nothing and notes what is already there.
"""

from django.core.management.base import BaseCommand

from catalogue import discord


class Command(BaseCommand):
    help = "Post new restocks, pre-orders, price drops and drop days to Discord."

    def add_arguments(self, parser):
        parser.add_argument("--test", action="store_true", help="Send one hello to each channel set up.")

    def handle(self, *args, test=False, **options):
        if not discord.channels():
            self.stdout.write("No Discord channel is set up (admin, Discord channels). Nothing posted.")
            return
        if test:
            try:
                count = discord.test()
            except discord.DiscordError as exc:
                self.stdout.write(f"Not posted: {exc}. Check the webhook address in .env.")
                return
            self.stdout.write(f"Hello posted to {count} Discord channel{'s' if count != 1 else ''}.")
            return
        sent = discord.run(stdout=self.stdout)
        self.stdout.write("; ".join(f"{label}: " + (", ".join(f"{kind} {count}" for kind, count in kinds.items()) or "nothing due")
                                    for label, kinds in sent.items()))
