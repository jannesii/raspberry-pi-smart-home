"""Playwright driver kept in a process without Eventlet patching."""

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def record_label_reload_filter_and_navigation(browser_page, viewport):
    from playwright.sync_api import expect

    page, origin = browser_page
    page.set_viewport_size(viewport)
    mutations = []
    page.on(
        "request", lambda req: mutations.append(req.url) if req.method in ("POST", "PUT") else None
    )
    page.goto(origin + "/car_heater/sessions")
    expect(page.get_by_text("No sessions for this date.", exact=False)).to_be_visible()
    page.get_by_text("Record a heating attempt", exact=True).click()
    page.get_by_label("Observation began", exact=True).fill("2026-10-14T06:00")
    page.get_by_label("Installation context (optional)").fill(
        "Two glass sensors; original heater bracket"
    )
    page.get_by_role("button", name="Save attempt", exact=True).click()
    expect(page.get_by_role("heading", name="Outcome not recorded", exact=True)).to_be_visible()
    expect(
        page.get_by_text("Two glass sensors; original heater bracket", exact=True)
    ).to_be_visible()
    expect(page.get_by_text("14/10/2026, 06:00:00", exact=True)).to_be_visible()
    assert (
        page.request.get(page.url.replace(origin, origin + "/api", 1)).json()["observed_at"]
        == "2026-10-14T03:00:00+00:00"
    )
    page.get_by_label("Wipers enough", exact=True).check()
    page.get_by_label("Already clear when observation began", exact=True).check()
    page.get_by_role("button", name="Save outcome", exact=True).focus()
    page.keyboard.press("Enter")
    expect(page.get_by_text("Outcome saved. Heating unchanged.", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role("region", name="Wipers enough", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Wipers enough", exact=True)).to_be_visible()
    expect(page.get_by_label("Already clear when observation began", exact=True)).to_be_checked()
    page.get_by_role("link", name="Back to sessions", exact=True).click()
    expect(page.get_by_text("Wipers enough", exact=True)).to_be_visible()
    page.get_by_label("Departure or session date", exact=True).fill("2026-10-15")
    page.get_by_role("button", name="Filter", exact=True).click()
    expect(page.get_by_text("No sessions for this date.", exact=False)).to_be_visible()
    page.reload()
    expect(page.get_by_text("No sessions for this date.", exact=False)).to_be_visible()
    page.get_by_role("button", name="Show all", exact=True).click()
    expect(page.get_by_text("Wipers enough", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=f"/tmp/car-heater-sessions-{viewport['width']}.png", full_page=True)
    nav = page.get_by_role("navigation", name="Car heater", exact=True)
    nav.get_by_role("link", name="Heating", exact=True).click()
    expect(page.get_by_role("button", name="Stop heating", exact=False)).to_be_visible()
    expect(page.get_by_text("Battery Charge Mode", exact=True)).to_have_count(0)
    page.get_by_role("navigation", name="Car heater", exact=True).get_by_role(
        "link", name="Settings", exact=True
    ).click()
    page.wait_for_load_state("domcontentloaded")
    page.get_by_role("button", name="Open calibration tools", exact=True).click()
    expect(page.get_by_role("heading", name="Car Heater Settings", exact=True)).to_be_visible()
    page.get_by_role("button", name="Close", exact=True).first.click()
    expect(page.get_by_role("button", name="View Charts", exact=False)).to_be_visible()
    expect(page.get_by_role("button", name="Get Logs", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert len(mutations) == 2
    assert all("/sessions" in url for url in mutations)


def loading_failure_retry_and_save_failure_preserve_input(browser_page):
    from playwright.sync_api import expect

    page, origin = browser_page
    page.route(
        "**/api/car_heater/sessions",
        lambda route: route.fulfill(status=503, json={"error": "Sessions unavailable"}),
    )
    page.goto(origin + "/car_heater/sessions")
    expect(page.get_by_text("Sessions unavailable", exact=True)).to_be_visible()
    page.unroute("**/api/car_heater/sessions")
    page.get_by_role("button", name="Retry loading", exact=True).click()
    expect(page.get_by_text("No sessions for this date.", exact=False)).to_be_visible()
    page.get_by_text("Record a heating attempt", exact=True).click()
    page.get_by_label("Installation context (optional)").fill(
        "Keep this evidence after a failed save"
    )
    page.route(
        "**/api/car_heater/sessions",
        lambda route: route.fulfill(status=503, json={"error": "Save unavailable"}),
    )
    page.get_by_role("button", name="Save attempt", exact=True).click()
    expect(page.get_by_text("Save unavailable", exact=True)).to_be_visible()
    expect(page.get_by_label("Installation context (optional)")).to_have_value(
        "Keep this evidence after a failed save"
    )
    expect(page.get_by_role("button", name="Save attempt", exact=True)).to_be_enabled()
    # Spring DST skips 03:30 in Helsinki, even when the browser is abroad.
    page.get_by_label("Observation began", exact=True).fill("2026-03-29T03:30")
    page.get_by_role("button", name="Save attempt", exact=True).click()
    expect(
        page.get_by_text(
            "This time does not exist in Europe/Helsinki. Choose another time.", exact=True
        )
    ).to_be_visible()


if __name__ == "__main__":
    host = json.loads(Path(sys.argv[1]).read_text())
    width = int(sys.argv[2])
    with sync_playwright() as p:
        browser = p.chromium.launch(timeout=15000)
        context = browser.new_context(timezone_id="America/New_York", reduced_motion="reduce")
        context.add_cookies([{"name": "session", "value": host["cookie"], "url": host["origin"]}])
        context.route("https://**", lambda route: route.abort())
        page = context.new_page()
        if width:
            record_label_reload_filter_and_navigation(
                (page, host["origin"]), {"width": width, "height": 852 if width < 600 else 1000}
            )
        else:
            loading_failure_retry_and_save_failure_preserve_input((page, host["origin"]))
        browser.close()
