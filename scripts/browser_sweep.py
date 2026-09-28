"""Browser sweep of every public page. Needs: pip install playwright, a running dev server on port 8000, and Chromium.
Run: python scripts/browser_sweep.py [path-to-chromium]. Reports console errors, overflow, missing labels and tiny tap targets."""
import sys
from playwright.sync_api import sync_playwright
BASE = "http://127.0.0.1:8000"
pages = ["/", "/search/", "/search/?q=booster", "/search/?q=zzz", "/games/", "/games/pokemon/", "/games/pokemon/prismatic-evolutions/",
         "/products/prismatic-evolutions-elite-trainer-box/", "/products/prismatic-evolutions-super-premium-collection/", "/products/archazias-island-gift-set/",
         "/swipe/", "/about/", "/terms/", "/nope/"]
problems = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=sys.argv[1]) if len(sys.argv) > 1 else p.chromium.launch()
    for w in (375, 768, 1280):
        ctx = b.new_context(viewport={"width": w, "height": 800})
        page = ctx.new_page()
        page.on("console", lambda m: problems.append(("console", m.type, m.text)) if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: problems.append(("pageerror", str(e))))
        page.on("response", lambda r: problems.append(("http", r.status, r.url)) if r.status >= 400 and "/nope/" not in r.url else None)
        for path in pages:
            page.goto(BASE + path, wait_until="networkidle")
            ov = page.evaluate("document.documentElement.scrollWidth - innerWidth")
            if ov > 0: problems.append(("overflow", w, path, ov))
            missing_alt = page.evaluate("[...document.images].filter(i => !i.hasAttribute('alt')).length")
            if missing_alt: problems.append(("alt", path, missing_alt))
            unlabeled = page.evaluate("[...document.querySelectorAll('input,select,button')].filter(e => !(e.labels && e.labels.length) && !e.getAttribute('aria-label') && !e.textContent.trim() && e.type!=='hidden').length")
            if unlabeled: problems.append(("label", path, unlabeled))
            small_targets = page.evaluate("[...document.querySelectorAll('a,button')].filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.height < 24; }).map(e => e.className || e.textContent.trim().slice(0,20))")
            if w == 375 and small_targets: problems.append(("small-target", path, small_targets[:6]))
        ctx.close()
    ctx = b.new_context(java_script_enabled=False, viewport={"width": 375, "height": 800}); page = ctx.new_page()
    page.goto(BASE + "/search/?q=booster"); page.select_option("#id_type", "bundle"); page.click("text=Show results"); page.wait_for_load_state()
    print("no-js filter url:", page.url, "results:", page.locator(".card").count())
    page.goto(BASE + "/"); page.fill("#home-q", "etb"); page.press("#home-q", "Enter"); page.wait_for_load_state(); print("no-js search:", page.url)
    page.goto(BASE + "/swipe/"); print("no-js swipe fallback link present:", page.locator(".deck__nojs a").count())
    ctx.close(); b.close()
for pr in problems: print(pr)
print("problems:", len(problems))
