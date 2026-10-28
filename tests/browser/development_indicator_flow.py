"""Development is marked, and login/navigation work, at phone and desktop widths."""

import sys

from playwright.sync_api import expect, sync_playwright

if __name__ == "__main__":
    origin, username, password = sys.argv[1:4]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 393, "height": 852})
        page.goto(origin + "/login")
        banner = page.locator(".env-banner")
        expect(banner).to_be_visible()
        expect(banner).to_contain_text("Kehitysympäristö")
        page.fill("#username", username)
        page.fill("#password", password)
        page.get_by_role("button", name="Kirjaudu").click()
        page.wait_for_url(lambda url: "/login" not in url)

        for width in (393, 1440):
            page.set_viewport_size({"width": width, "height": 852})
            page.goto(origin + "/")
            expect(banner).to_be_visible()
            box = banner.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1, box
            assert banner.evaluate("el => el.scrollWidth <= el.clientWidth")
            toggle = page.locator("#menuToggle")
            if toggle.is_visible():
                toggle.click()
            page.locator("nav.navbar").get_by_role("link", name="Asetukset").click()
            page.wait_for_url(lambda url: url.endswith("/settings"))
            expect(banner).to_be_visible()
            # The indicator stays visible while scrolling the page.
            page.mouse.wheel(0, 2000)
            box = banner.bounding_box()
            assert box and box["y"] <= 1, box
        browser.close()
