"""
Make the home screen icons from the logo mark.

    python manage.py make_icons

Writes web/static/img/icon-192.png, icon-512.png, icon-maskable-512.png and
apple-touch-icon.png from web/static/img/mark.png: the mark on white, with
the padding Android's maskable icons need. Run it again after changing the
mark, then commit the files.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

IMG = Path(settings.BASE_DIR) / "web" / "static" / "img"
# Name, size, and how much of the width the mark takes: maskable icons keep the mark inside the
# middle 80%, so a circle or squircle crop never clips it.
ICONS = [("icon-192.png", 192, 0.8), ("icon-512.png", 512, 0.8), ("icon-maskable-512.png", 512, 0.6), ("apple-touch-icon.png", 180, 0.8)]


class Command(BaseCommand):
    help = "Write the home screen icons from web/static/img/mark.png."

    def handle(self, *args, **options):
        from PIL import Image

        mark = Image.open(IMG / "mark.png").convert("RGBA")
        mark = mark.crop(mark.getbbox())
        for name, size, share in ICONS:
            canvas = Image.new("RGBA", (size, size), "#ffffff")
            box = int(size * share)
            scale = min(box / mark.width, box / mark.height)
            fitted = mark.resize((max(1, round(mark.width * scale)), max(1, round(mark.height * scale))), Image.LANCZOS)
            canvas.alpha_composite(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2))
            canvas.convert("RGB").save(IMG / name, optimize=True)
            self.stdout.write(f"{name}: {size}x{size}")
