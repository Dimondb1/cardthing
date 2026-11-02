"""Draws the "Listed on RipRaptor" badges shops can put on their own sites (List your shop, #badge).

Needs: pip install playwright, and Chromium (Pillow is the site's own). Run: python scripts/make_badges.py [chromium].
Writes web/static/img/badge/listed-on-ripraptor.png (for a light background) and listed-on-ripraptor-dark.png (for a
dark one) at three times their shown size, with transparent corners. Run it again after the logo, the font or the
brand colours change; the addresses stay the same, so shops' badges follow.

The badge, the owner's brand in small (Nov 2026: the first ones were "misshapen and terrible"): an orange square
holding the raptor's head in white, beside "Listed on" and the wordmark in orange. Both shapes come from the site's
own artwork as masks: the raptor from raptor-lg.png, the letters from logo.png with its faint haze cleared and the
head left off, so they are crisp at any size and take any token colour.
"""
import base64
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "web" / "static"
OUT = STATIC / "img" / "badge"
# The size the badge is shown at on a shop's page, in CSS pixels (web/badge.py); the files are three times this.
WIDTH, HEIGHT = 180, 52
SCALE = 3
# The logo's letters end before this column; the raptor's head follows.
WORDMARK_END = 871
# Below this the logo's alpha is the faint haze round the artwork, not the artwork.
HAZE = 48

PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="{tokens}">
<style>
  html, body {{ margin: 0; background: transparent; }}
  .badge {{
    box-sizing: border-box; width: {width}px; height: {height}px; display: flex; overflow: hidden;
    border-radius: 12px; font-family: var(--font-sans);
  }}
  .badge--light {{ background: var(--color-paper); box-shadow: inset 0 0 0 1.5px var(--color-brand-line); }}
  .badge--dark {{ background: var(--color-ink); box-shadow: inset 0 0 0 1px var(--color-on-ink-soft); }}
  .badge__tile {{ flex: none; width: {height}px; display: grid; place-items: center; background: var(--color-brand); }}
  .badge__raptor {{
    width: 36px; height: 35px; background: var(--color-paper);
    -webkit-mask: url("{raptor}") center / contain no-repeat; mask: url("{raptor}") center / contain no-repeat;
  }}
  .badge__text {{ flex: 1; display: flex; flex-direction: column; justify-content: center; gap: 4px; padding: 0 12px; }}
  .badge__words {{ font-size: 8.5px; font-weight: 700; line-height: 1; letter-spacing: 0.16em; text-transform: uppercase; }}
  .badge--light .badge__words {{ color: var(--color-ink-muted); }}
  .badge--dark .badge__words {{ color: var(--color-on-ink-muted); }}
  .badge__wordmark {{
    display: block; width: {mark_width}px; height: {mark_height}px; background: var(--color-brand);
    -webkit-mask: url("{wordmark}") left center / contain no-repeat; mask: url("{wordmark}") left center / contain no-repeat;
  }}
</style></head>
<body><div class="badge badge--{theme}"><span class="badge__tile"><span class="badge__raptor"></span></span><span class="badge__text"><span class="badge__words">Listed on</span><span class="badge__wordmark"></span></span></div></body></html>
"""


def clean(image):
    """``image`` as a mask: its own alpha, the haze below HAZE cleared and the rest stretched back to full."""
    alpha = image.getchannel("A").point(lambda a: 0 if a < HAZE else min(255, round((a - HAZE) * 255 / (200 - HAZE))))
    mask = Image.new("RGBA", image.size, (255, 255, 255, 0))
    mask.putalpha(alpha)
    return mask.crop(alpha.getbbox())


def masks(folder):
    """The wordmark's letters and the raptor's head as clean masks in ``folder``: (wordmark, raptor, its aspect)."""
    logo = Image.open(STATIC / "img" / "logo.png").convert("RGBA")
    wordmark = clean(logo.crop((0, 0, WORDMARK_END, logo.height)))
    raptor = clean(Image.open(STATIC / "img" / "raptor-lg.png").convert("RGBA"))
    paths = folder / "wordmark.png", folder / "raptor.png"
    wordmark.save(paths[0])
    raptor.save(paths[1])
    return paths[0], paths[1], wordmark.width / wordmark.height


def data_uri(path):
    """A mask inside the page itself: Chromium never takes a CSS mask from a file on disk."""
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def page_html(theme, wordmark, raptor, aspect):
    """The page that draws one badge, its masks inside it."""
    # The letters fill the text column's width: 180 less the tile and the column's padding.
    mark_width = WIDTH - HEIGHT - 24
    return PAGE.format(tokens=(STATIC / "css" / "tokens.css").as_uri(), raptor=data_uri(raptor), wordmark=data_uri(wordmark),
                       width=WIDTH, height=HEIGHT, theme=theme, mark_width=mark_width,
                       mark_height=round(mark_width / aspect, 1))


THEMES = (("light", "listed-on-ripraptor.png"), ("dark", "listed-on-ripraptor-dark.png"))


def main():
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder, sync_playwright() as p:
        wordmark, raptor, aspect = masks(Path(folder))
        browser = p.chromium.launch(executable_path=sys.argv[1]) if len(sys.argv) > 1 else p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=SCALE)
        for theme, name in THEMES:
            # A file of its own, so the page may load the font from disk.
            html = Path(folder) / f"{theme}.html"
            html.write_text(page_html(theme, wordmark, raptor, aspect), encoding="utf-8")
            page.goto(html.as_uri())
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(200)
            page.locator(".badge").screenshot(path=str(OUT / name), omit_background=True)
            print(OUT / name)
        browser.close()


if __name__ == "__main__":
    main()
