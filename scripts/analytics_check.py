"""Browser check of the Google Analytics consent card. Needs: pip install playwright, Chromium, and a dev
server on port 8000 started with RIPRAPTOR_GA_ID=G-TEST123 and demo data (manage.py seed_demo).
Run: python scripts/analytics_check.py [path-to-chromium] [screenshot-dir]
Google is never reached: its addresses are answered with an empty script. Prints each check and its result."""
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
GOOGLE = ("**/*googletagmanager.com/**", "**/*google-analytics.com/**")
failures = []


def check(name, ok, seen=None):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok or seen is None else f"  (saw {seen!r})"))
    if not ok:
        failures.append(name)


def context(browser, width=375, height=740, **kwargs):
    ctx = browser.new_context(viewport={"width": width, "height": height}, **kwargs)
    ctx.google = []

    def answer(route):
        ctx.google.append(route.request.url)
        route.fulfill(status=200, content_type="text/javascript", body="")
    for pattern in GOOGLE:
        ctx.route(pattern, answer)
    ctx.route("**/*dwin2.com/**", lambda route: route.fulfill(status=200, body=""))
    return ctx


def layer(page, kind):
    return page.evaluate(f"(window.dataLayer || []).map(a => Array.from(a)).filter(a => a[0] === '{kind}')")


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=sys.argv[1]) if len(sys.argv) > 1 else p.chromium.launch()
    shots = sys.argv[2] if len(sys.argv) > 2 else ""
    product = None

    ctx = context(browser)
    page = ctx.new_page()
    page.goto(BASE + "/")
    page.wait_for_selector("[data-consent].is-shown")
    product = page.eval_on_selector("a[href^='/products/']", "a => a.getAttribute('href')")
    check("first visit: card shown, nothing of Google's loaded", not ctx.google, ctx.google)
    check("first visit: the home screen card waits", page.evaluate("document.querySelector('[data-install]').hidden"))
    page.click("[data-consent-choice=denied]")
    check("No analytics moves focus to the footer button", page.evaluate("document.activeElement.matches('[data-consent-open]')"))
    page.goto(BASE + product)
    page.wait_for_timeout(300)
    check("after No analytics: no card, nothing loaded", not ctx.google and page.evaluate("document.querySelector('[data-consent]').hidden"), ctx.google)
    page.click("[data-consent-open]")
    check("footer button shows the card and focuses its first button", page.evaluate("document.activeElement.dataset.consentChoice === 'granted'"))
    page.click("[data-consent-choice=granted]")
    page.wait_for_timeout(300)
    check("Allow analytics loads the tag once", len(ctx.google) == 1 and "gtag/js?id=G-TEST123" in ctx.google[0], ctx.google)
    config = layer(page, "config")
    check("signals and ad personalisation off", config and config[0][2].get("allow_google_signals") is False, config)
    check("ad storage denied", layer(page, "consent")[0][2]["ad_storage"] == "denied")
    with ctx.expect_page() as shop:
        page.click("a[rel~='sponsored']")
    shop.value.close()
    events = layer(page, "event")
    check("a Buy press is sent as buy_click", events and events[0][1] == "buy_click", events)

    # A private thread: nothing loads there, and the next page reports only the site's address.
    page.goto(BASE + "/contact/")
    if page.locator("textarea[name=body]").count():
        page.fill("textarea[name=body]", "Analytics check")
        with page.expect_navigation():
            page.locator("textarea[name=body]").evaluate("t => t.form.requestSubmit()")
    thread = page.evaluate("location.pathname")
    if "/contact/c/" in thread:
        ctx.google.clear()
        page.goto(BASE + thread)
        check("private thread: no analytics script, no card", page.evaluate("!document.querySelector('script[data-ga-id], [data-consent]')"))
        check("private thread: no Google request", not ctx.google, ctx.google)
        page.click("a[href='/']")
        page.wait_for_load_state("load")
        check("next page: referrer is the site's address only", page.evaluate("document.referrer") == BASE + "/", page.evaluate("document.referrer"))
        page.goto(BASE + "/contact/c/mistyped/")
        check("mistyped private link: no analytics script", page.evaluate("!document.querySelector('script[data-ga-id]')"))
    else:
        check("could post a test message to reach a private thread", False, thread)

    # Refused in another tab: this tab's running tag is dropped.
    page.goto(BASE + product)
    page.wait_for_timeout(300)
    ctx.add_cookies([{"name": "_ga", "value": "GA1.1.1.1", "url": BASE}, {"name": "_ga_TEST123", "value": "GS1.1", "url": BASE}])
    other = ctx.new_page()
    other.goto(BASE + "/")
    with page.expect_navigation():
        other.click("[data-consent-open]")
        other.click("[data-consent-choice=denied]")
    page.wait_for_timeout(300)
    check("refused in another tab: this tab reloads without the tag", not page.evaluate("!!window.gtag"))
    check("refused: Google's cookies removed", not [c for c in ctx.cookies() if c["name"].startswith("_ga")])
    other.close()
    ctx.close()

    # Refused on a later page, then Back: the page kept for Back drops its tag too.
    ctx = context(browser)
    page = ctx.new_page()
    page.goto(BASE + product)
    page.click("[data-consent-choice=granted]")
    page.wait_for_timeout(300)
    page.goto(BASE + "/terms/")
    page.click("[data-consent-open]")
    page.click("[data-consent-choice=denied]")
    page.go_back()
    page.wait_for_timeout(600)
    check("refused, then Back: the restored page has no running tag", not page.evaluate("!!window.gtag"))
    ctx.close()

    # The swipe page on a phone: the card waits for the next page so Skip and Save stay free.
    ctx = context(browser)
    page = ctx.new_page()
    page.goto(BASE + "/swipe/")
    page.wait_for_timeout(800)
    check("swipe page: no card over the deck", page.evaluate("document.querySelector('[data-consent]').hidden"))
    if shots:
        page.screenshot(path=f"{shots}/swipe-375.png")
    ctx.close()

    for width, height in ((375, 740), (768, 1024), (1280, 900)):
        ctx = context(browser, width, height)
        page = ctx.new_page()
        page.goto(BASE + product)
        page.wait_for_selector("[data-consent].is-shown")
        page.wait_for_timeout(500)
        check(f"{width}px: no sideways scroll", page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        if shots:
            page.screenshot(path=f"{shots}/product-{width}.png")
        ctx.close()

    ctx = context(browser, java_script_enabled=False)
    page = ctx.new_page()
    page.goto(BASE + "/")
    check("JavaScript off: no card, no footer button", not page.is_visible("[data-consent]") and not page.is_visible("[data-consent-open]"))
    ctx.close()
    browser.close()

print(f"{len(failures)} failed")
sys.exit(1 if failures else 0)
