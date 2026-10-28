"""
The "Listed on RipRaptor" badge a listed shop can put on its own site (List your shop, #badge), and the code it
pastes. The owner asked for it on 28 Oct 2026, so shops' sites link to RipRaptor: links from other sites are how
search engines judge a new site.

The code is one plain link to the home page around one image. The image's address has no content hash
(web/static/img/badge/, drawn by scripts/make_badges.py), so a badge redrawn later reaches every shop that pasted
the code. Taking it is the shop's choice and never changes its listing: a link given in exchange for a listing
is one search engines discount.
"""

from html import escape

from django.conf import settings
from django.templatetags.static import static
from django.urls import reverse

# The size the badge shows at, in CSS pixels; the files are twice this, for sharp screens.
WIDTH, HEIGHT = 180, 52
# (theme, file, the wording key of its label on the page).
BADGES = (
    ("light", "listed-on-ripraptor.png", "listshop.badge.light"),
    ("dark", "listed-on-ripraptor-dark.png", "listshop.badge.dark"),
)


def image_url(name):
    """The image's lasting address: the file's own name under the static folder, not its hashed copy."""
    return f"{settings.RIPRAPTOR_SITE_URL}/{settings.STATIC_URL.strip('/')}/img/badge/{name}"


def code(name, alt):
    """The HTML a shop pastes for the badge in file ``name``, ``alt`` being what a screen reader says for it."""
    return (f'<a href="{escape(settings.RIPRAPTOR_SITE_URL)}/"><img src="{escape(image_url(name))}" '
            f'alt="{escape(alt)}" width="{WIDTH}" height="{HEIGHT}"></a>')


def badges(alt):
    """Each badge for the page: its theme, label key, the image our page shows, and the code to paste."""
    return [{"theme": theme, "label": label, "src": static(f"img/badge/{name}"), "code": code(name, alt),
             "width": WIDTH, "height": HEIGHT} for theme, name, label in BADGES]


def page_url():
    """The badge part of the List your shop page, as the email offering it links to it."""
    return f"{settings.RIPRAPTOR_SITE_URL}{reverse('web:list_shop')}#badge"
