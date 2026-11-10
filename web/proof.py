"""
Proof that the pages built for search engines and AI assistants say what they should (the owner, 9 Nov 2026: "Improve
RipRaptor's search and AI-assistant visibility, then prove the changes worked"). ``manage.py visibility_proof`` fetches
each changed page as each crawler would and runs these checks on the raw HTML, the HTML a crawler reads before any
script runs:

* a game's page: one heading that says what it compares, the cheapest of each kind in stock, five to seven questions,
  their FAQPage block matching the page word for word, an ItemList, and the same in-stock and shop counts in its
  description, its own line and llms.txt;
* a set's page: its sentence of live figures and an ItemList;
* the comparing guide: its seven questions, live examples and an Article block with both dates;
* every page: one h1, and every JSON-LD block parses;
* llms.txt and the sitemap name the guide.

The checks are plain functions over text, so the tests run them on the test site's pages (web/tests_answers.py), the
command runs them on the live one, and the Search visibility page in Admin (web/visibility.py) runs them on the
machine's own copy and shows the result, for an owner on a phone who cannot copy from a console.
"""

import html as html_text
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

# The crawlers a page is fetched as. Google and Bing are known to the site by their addresses (web/botcheck.py), so a
# fetch that only uses their name from another address abroad is shown the visitor check: VERIFIED says which.
CRAWLERS = {
    "Googlebot": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Bingbot": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "GPTBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2; +https://openai.com/gptbot)",
    "ClaudeBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
    "PerplexityBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; "
                     "+https://perplexity.ai/perplexitybot)",
}
VERIFIED = ("Googlebot", "Bingbot")
GUIDE = "/guides/compare-sealed-tcg-prices-uk/"
LD = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
FAQ = re.compile(r'<h3 class="hub-faq__q">(.*?)</h3><p class="hub-faq__a">(.*?)</p>', re.S)
STOCK = re.compile(r"([\d,]+) sealed .*?products? in stock at ([\d,]+) UK shops?")


def shown(markup):
    """What a reader sees of ``markup``: its tags gone, its entities read, its spaces single."""
    return " ".join(html_text.unescape(re.sub(r"<[^>]*>", "", markup)).split())


def is_check_page(html):
    """Whether ``html`` is the visitor check (web/botcheck.py), not the page asked for."""
    return 'class="botcheck-page"' in html


def blocks(html):
    """(every JSON-LD block parsed and flattened, how many failed to parse)."""
    found, broken = [], 0
    for text in LD.findall(html):
        try:
            data = json.loads(text)
        except ValueError:
            broken += 1
            continue
        found += data if isinstance(data, list) else [data]
    return found, broken


def of_type(found, kind):
    return [block for block in found if isinstance(block, dict) and block.get("@type") == kind]


def description(html):
    match = re.search(r'<meta name="description" content="([^"]*)"', html)
    return html_text.unescape(match.group(1)) if match else ""


def common(html):
    """The checks every page gets: [(name, passed, detail)]."""
    found, broken = blocks(html)
    headings = len(re.findall(r"<h1[\s>]", html))
    return [
        ("one h1", headings == 1, f"{headings} found"),
        ("every JSON-LD block parses", bool(found) and not broken, f"{len(found)} parsed, {broken} broken"),
    ]


def game(html, llms_line=""):
    """A game's page. ``llms_line`` is its line in llms.txt, to compare the counts."""
    found, _broken = blocks(html)
    heading = shown((re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S) or [None, ""])[1])
    faq = [(shown(question), shown(answer)) for question, answer in FAQ.findall(html)]
    marked = [(entry.get("name"), (entry.get("acceptedAnswer") or {}).get("text"))
              for block in of_type(found, "FAQPage") for entry in block.get("mainEntity", [])]
    lists = [block["mainEntity"] for block in of_type(found, "CollectionPage")
             if (block.get("mainEntity") or {}).get("@type") == "ItemList"]
    line = shown((re.search(r'<p class="cx-hero__lead">(.*?)</p>', html, re.S) or [None, ""])[1])
    counts = {"description": STOCK.search(description(html)), "page": STOCK.search(line)}
    counts = {name: match.groups() if match else None for name, match in counts.items()}
    said = re.search(r"([\d,]+) in stock now at ([\d,]+) UK shops?", llms_line)
    if llms_line:
        counts["llms.txt"] = said.groups() if said else None
    agreed = len(set(counts.values())) == 1 and None not in counts.values()
    rows = len(re.findall(r'<li class="hub-answer__row">', html))
    return common(html) + [
        ("heading says what the page compares", heading.endswith("sealed product prices in the UK"), heading),
        ("cheapest of each kind in stock", rows >= 1 and "delivered at" in html and "Checked " in html, f"{rows} kinds"),
        ("5 to 7 questions on the page", 5 <= len(faq) <= 7, f"{len(faq)} questions"),
        ("FAQPage matches the page word for word", bool(faq) and marked == faq, f"{len(marked)} in the block"),
        ("ItemList of the products named", bool(lists) and lists[0].get("numberOfItems", 0) >= 1,
         f"{lists[0].get('numberOfItems') if lists else 0} products"),
        ("counts agree: description, page" + (", llms.txt" if llms_line else ""), agreed,
         "; ".join(f"{name} {' in stock at '.join(value) if value else 'not found'}" for name, value in counts.items())),
    ]


def product_set(html):
    found, _broken = blocks(html)
    intro = shown((re.search(r'<p class="cx-setintro">(.*?)</p>', html, re.S) or [None, ""])[1])
    lists = [block["mainEntity"] for block in of_type(found, "CollectionPage")
             if (block.get("mainEntity") or {}).get("@type") == "ItemList"]
    return common(html) + [
        ("intro from live figures", bool(re.search(r"\d", intro)), intro[:110]),
        ("ItemList of its products", bool(lists) and lists[0].get("numberOfItems", 0) >= 1,
         f"{lists[0].get('numberOfItems') if lists else 0} products"),
    ]


def guide(html):
    found, _broken = blocks(html)
    articles = of_type(found, "Article")
    article = articles[0] if articles else {}
    sections = len(re.findall(r'<section class="guide-section"', html))
    live = len(re.findall(r'class="guide-box guide-live"', html))
    return common(html) + [
        ("seven questions", sections == 7, f"{sections} sections"),
        ("live examples from the database", live >= 1 and "£" in html, f"{live} examples"),
        ("Article with datePublished and dateModified",
         bool(article.get("datePublished")) and bool(article.get("dateModified")),
         f"published {article.get('datePublished')}, modified {article.get('dateModified')}"),
    ]


def home(html):
    return common(html)


def guides_index(html):
    return common(html) + [("links the comparing guide", f'href="{GUIDE}"' in html, GUIDE)]


def llms(text, site):
    return [("llms.txt names the guide", f"{site}{GUIDE}" in text, GUIDE),
            ("llms.txt labels its counts", "sealed products listed" in text and "in stock now" in text, "")]


def sitemap(text, site):
    return [("sitemap.xml lists the guide", f"<loc>{site}{GUIDE}</loc>" in text, GUIDE)]


def llms_line(text, path):
    """The line of llms.txt for the game at ``path``, or ""."""
    return next((line for line in text.splitlines() if line.startswith("- [") and f"{path})" in line), "")


# Running them all -------------------------------------------------------------------------------------------------

GAMES = ("/games/pokemon/", "/games/magic-the-gathering/")
TIMEOUT = 40


@dataclass
class Row:
    path: str
    crawler: str      # "" for llms.txt and the sitemap, which are read once
    name: str
    passed: bool
    detail: str = ""


def over_network(site):
    """A fetcher: (status, text) of a path of ``site`` asked for over the internet as a crawler."""
    def fetch(path, crawler):
        request = urllib.request.Request(site + path, headers={"User-Agent": CRAWLERS[crawler], "Accept": "text/html,*/*"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, ""
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return 0, exc.__class__.__name__

    return fetch


def inside(site):
    """A fetcher that asks this machine's own copy of the site, with no network: what the site serves a crawler it
    knows, whatever address asks. ``site`` gives the host and scheme the pages are built for."""
    from django.test import Client

    parts = urlsplit(site)

    def fetch(path, crawler):
        client = Client(HTTP_USER_AGENT=CRAWLERS[crawler], HTTP_HOST=parts.netloc)
        response = client.get(path, secure=parts.scheme == "https")
        body = b"".join(response.streaming_content) if getattr(response, "streaming", False) else response.content
        return response.status_code, body.decode("utf-8", "replace")

    return fetch


def a_set():
    """A Pokémon set with something in stock, from this machine's catalogue, or "" without one."""
    from catalogue.models import ProductSet

    from . import hubs

    stocked = hubs.in_stock_listings().values("product__product_set")
    found = (ProductSet.objects.filter(game__slug="pokemon", game__is_active=True, pk__in=stocked)
             .select_related("game").order_by("-release_date", "pk").first())
    return found.get_absolute_url() if found else ""


def run(fetch, site, set_path="", known_by_address=False):
    """Every check on every page as every crawler: [Row]. ``fetch`` is over_network(site) or inside(site).
    ``known_by_address``: the fetch is over the internet from an address that is not Google's or Bing's, so those two
    are shown the visitor check, as they should be; that row passes and says so."""
    rows = []
    llms_text = fetch("/llms.txt", "ClaudeBot")[1]
    rows += [Row("/llms.txt", "", *check) for check in llms(llms_text, site)]
    rows += [Row("/sitemap.xml", "", *check) for check in sitemap(fetch("/sitemap.xml", "ClaudeBot")[1], site)]
    pages = [("/", home)]
    pages += [(path, lambda html, path=path: game(html, llms_line(llms_text, path))) for path in GAMES]
    if set_path:
        pages.append((set_path, product_set))
    pages += [(GUIDE, guide), ("/guides/", guides_index)]
    for path, check in pages:
        for crawler in CRAWLERS:
            status, html = fetch(path, crawler)
            if status == 200 and is_check_page(html):
                expected = known_by_address and crawler in VERIFIED
                rows.append(Row(path, crawler, "visitor check shown", expected,
                                "expected: this address is not the crawler's own" if expected
                                else "the page was not served"))
                continue
            rows.append(Row(path, crawler, "answers 200", status == 200, f"status {status}, {len(html):,} bytes"))
            if status == 200:
                rows += [Row(path, crawler, *found) for found in check(html)]
    return rows


def by_page(rows):
    """[{"path", "checks", "failed": [(name, [crawlers], detail)]}] in the order run: one line a page for the Admin
    page, with each failing check once and the crawlers it failed for."""
    pages = {}
    for row in rows:
        page = pages.setdefault(row.path, {"path": row.path, "checks": 0, "failed": {}})
        page["checks"] += 1
        if not row.passed:
            found = page["failed"].setdefault(row.name, {"name": row.name, "crawlers": [], "detail": row.detail})
            if row.crawler:
                found["crawlers"].append(row.crawler)
    return [{**page, "failed": list(page["failed"].values())} for page in pages.values()]
