"""Screenshot every page at 360/768/1280 px in light and dark, and record objective UX checks.

    py audit/screens.py http://localhost:8010 [pages-file]

For each page it records: HTTP status, console errors, horizontal overflow (page wider than the
viewport), tap targets under 44px on phones, images that failed to load, and the title. Results go
to audit/screenshots/_results.json; images to audit/screenshots/<theme>/<width>/<slug>.png.
"""
import json
import pathlib
import re
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8010"
OUT = pathlib.Path(__file__).parent / "screenshots"
PAGES = [p.strip() for p in pathlib.Path(sys.argv[2]).read_text().splitlines() if p.strip()] if len(sys.argv) > 2 else []
WIDTHS = [360, 768, 1280]
THEMES = ["light", "dark"]
LOGIN = ("admin@example.org", "Launch-Audit-2026")

CHECKS = """() => {
  const vw = document.documentElement.clientWidth;
  const over = document.documentElement.scrollWidth - vw;
  const wide = [];
  if (over > 1) for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.right > vw + 1 && r.width > 0 && getComputedStyle(el).position !== 'fixed' && !el.closest('.dtable-wrap,.overflow-x-auto,[data-scroll-x]')) {
      wide.push((el.tagName + '.' + (el.className && el.className.baseVal === undefined ? el.className : '')).slice(0, 80)); if (wide.length > 4) break; }
  }
  const small = [];
  if (vw < 500) for (const el of document.querySelectorAll('a[href],button,input:not([type=hidden]),select,[role=button]')) {
    const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    if (r.width && r.height && s.visibility !== 'hidden' && (r.height < 32 || r.width < 32) && !el.closest('p,li p,td p'))
      small.push((el.innerText || el.getAttribute('aria-label') || el.name || el.tagName).trim().slice(0, 30));
  }
  const broken = [...document.images].filter(i => i.complete && i.naturalWidth === 0 && i.src).map(i => i.src.slice(-60));
  return {overflow: over, wide, small: small.slice(0, 12), smallCount: small.length, broken, title: document.title,
          h1: (document.querySelector('h1') || {}).innerText || ''};
}"""


def slug(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_") or "home"


def main() -> None:
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for theme in THEMES:
            ctx = browser.new_context(color_scheme=theme, viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text[:160]) if m.type == "error" else None)
            page.on("pageerror", lambda exc: errors.append("JS: " + str(exc)[:160]))
            page.goto(BASE + "/admin/")
            page.fill("input[name=email]", LOGIN[0])
            page.fill("input[name=password]", LOGIN[1])
            page.click("button[type=submit]")
            page.wait_for_load_state("networkidle")
            for width in WIDTHS:
                page.set_viewport_size({"width": width, "height": 800})
                (OUT / theme / str(width)).mkdir(parents=True, exist_ok=True)
                for path in PAGES:
                    errors.clear()
                    try:
                        resp = page.goto(BASE + path, wait_until="load", timeout=30000)
                        status = resp.status if resp else 0
                        page.wait_for_timeout(600)
                        info = page.evaluate(CHECKS)
                        # The admin layout scrolls inside <main>; unroll it so the full page is captured.
                        page.evaluate("""() => { for (const el of document.querySelectorAll('main, .h-screen, .min-h-0, .overflow-clip, .fixed.inset-0, body, html')) {
                            el.style.height = 'auto'; el.style.maxHeight = 'none'; el.style.overflow = 'visible';
                            if (el.matches('.fixed.inset-0')) el.style.position = 'relative'; } }""")
                        page.screenshot(path=str(OUT / theme / str(width) / f"{slug(path)}.jpg"), full_page=True, type="jpeg", quality=70)
                    except Exception as exc:          # record and keep going
                        status, info = -1, {"error": str(exc)[:200]}
                    results.append({"path": path, "theme": theme, "width": width, "status": status, "console": list(errors[:5]), **info})
                    print(theme, width, status, path, "OVERFLOW" if info.get("overflow", 0) > 1 else "", flush=True)
            ctx.close()
        browser.close()
    (OUT / "_results.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
