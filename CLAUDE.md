# RipRaptor project rules

These rules apply to every change, whoever makes it. The owner can override
any of them, but should say so in the request.

## What RipRaptor is

A UK price comparison site for sealed trading card game products. The product
page is the money page: cheapest delivered price, stock, retailer, comparison,
last checked, buy link. Everything else supports that.

## Governance

- Act as the technical lead. Explain trade-offs in plain English, recommend
  one option, and say what you did not do and why.
- Never invent data. No fake retailers, prices, reviews, statistics, awards or
  retailer relationships on any page the public can see. Demo data lives only
  in `seed_demo` and is labelled as fictional.
- Ask before: adding a paid service, storing personal data, changing the URL
  structure, or fetching data from a retailer that has not been added by the
  owner.
- Keep the site legal: outbound retailer links use `rel="sponsored nofollow"`,
  every page's footer carries the one-line commission note with a link to
  the Terms page (which holds the full disclosures), and prices always carry
  when they were last checked.

## Engineering

- Django, server rendered, progressive enhancement. Every page and form works
  with JavaScript off. JavaScript only speeds things up.
- Keep dependencies few. Add one only when it removes real work.
- Every change comes with tests. `python manage.py test` must pass before a
  commit. Run `python -m pyflakes ripraptor catalogue content web` too.
- One query per list. Use `select_related`, `prefetch_related` and the
  `for_lists()` queryset. Check query counts in tests for new pages.
- Migrations are generated, never hand edited. Do not rewrite a migration that
  has been pushed.
- Settings that differ between machines come from environment variables and
  are listed in the README.
- Small commits with messages that say why, not just what.

## Design

- Tokens only. Every colour, size, radius, spacing and font value comes from
  `web/static/css/tokens.css`. No new hard-coded values in `site.css`.
- Mobile first. Check every change at 375px, 768px and 1280px before calling
  it done. No horizontal scrolling at any width.
- Brand: orange (`--color-brand`) for the logo, buy buttons and links; green
  (`--color-price`) for every cheapest price and saving; red only for dearer
  prices. Nothing else is coloured.
- Rounded cards with the soft orange border are the unit of layout for
  products. Lists of plain rows are fine for everything else.
- Icons only where they carry meaning (bag on buy, arrow on a dearer price,
  the section marks). No decorative shapes.
- Motion has a reason: hierarchy, feedback, continuity or state. Use the
  motion tokens (`--motion-fast/normal/slow`, `--ease-out/--ease-standard`),
  animate only transform and opacity, nothing over 400ms, and honour
  prefers-reduced-motion. If removing an animation makes the page clearer,
  remove it.
- The logo is `web/static/img/logo.png`; the search and bag icons are the
  supplied artwork in `web/static/img/`, recoloured through CSS masks.
- Buttons say what they do. "Buy now" and "Search products" are fine; never
  "Get started" or "Learn more".
- The swipe page (`/swipe/`) must feel instant: cards are prefetched, only
  transforms change during a drag, no layout work on pointermove.

## Copy

- Short, direct British English. Every sentence has a job.
- No em dashes anywhere. No exclamation marks. No marketing filler
  ("unlock", "seamless", "ultimate", "all in one place").
- Public wording lives in `content/registry.py` and is edited in Django
  Admin. Do not hard-code sentences in templates. Short data labels (stock
  states, product types) stay in code.
- The tests render every public page and fail on em dashes, exclamation
  marks and stock phrases. Keep them passing.

## Definition of done

1. Tests pass and pyflakes is clean.
2. Screenshots checked at three widths.
3. Every new public sentence is in the registry.
4. README updated if setup, settings or commands changed.
5. Committed and pushed to the working branch. Pull requests only when asked.
