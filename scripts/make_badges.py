"""Draws the "Listed on RipRaptor" badges shops can put on their own sites (List your shop, #badge).

Needs: pip install playwright, and Chromium. Run: python scripts/make_badges.py [path-to-chromium].
Writes web/static/img/badge/listed-on-ripraptor.png and listed-on-ripraptor-dark.png at twice their shown size,
from the logo and the site's own font and colours (web/static/css/tokens.css), with transparent corners. Run it
again after the logo or the brand colours change; the addresses stay the same, so shops' badges follow.
"""
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "web" / "static"
OUT = STATIC / "img" / "badge"
# The size the badge is shown at on a shop's page, in CSS pixels; the files are twice this.
WIDTH, HEIGHT = 180, 52
PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="{tokens}">
<style>
  html, body {{ margin: 0; background: transparent; }}
  .badge {{
    box-sizing: border-box; width: {width}px; height: {height}px; padding: 0 14px;
    display: flex; flex-direction: column; justify-content: center; gap: 3px;
    border-radius: 12px; font-family: var(--font-sans);
  }}
  .badge--light {{ background: var(--color-paper); border: 1.5px solid var(--color-brand-line); }}
  .badge--dark {{ background: var(--color-ink); border: 1.5px solid var(--color-ink); }}
  .badge__words {{ font-size: 11px; font-weight: 600; line-height: 1; letter-spacing: 0.02em; }}
  .badge--light .badge__words {{ color: var(--color-ink-muted); }}
  .badge--dark .badge__words {{ color: var(--color-paper); }}
  .badge__logo {{ display: block; height: 26px; width: auto; }}
</style></head>
<body><div class="badge badge--{theme}"><span class="badge__words">Listed on</span><img class="badge__logo" src="{logo}" alt=""></div></body></html>
"""


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=sys.argv[1]) if len(sys.argv) > 1 else p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=2)
        for theme, name in (("light", "listed-on-ripraptor.png"), ("dark", "listed-on-ripraptor-dark.png")):
            # A file of its own, so the page may load the font and logo from disk.
            with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as html:
                html.write(PAGE.format(tokens=(STATIC / "css" / "tokens.css").as_uri(),
                                       logo=(STATIC / "img" / "logo.png").as_uri(), width=WIDTH, height=HEIGHT, theme=theme))
            page.goto(Path(html.name).as_uri())
            Path(html.name).unlink()
            page.evaluate("document.fonts.ready")
            page.wait_for_function("[...document.images].every(i => i.complete && i.naturalWidth)")
            page.locator(".badge").screenshot(path=str(OUT / name), omit_background=True)
            print(OUT / name)
        browser.close()


if __name__ == "__main__":
    main()
