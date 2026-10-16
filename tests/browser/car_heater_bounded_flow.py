"""Phone/desktop bounded controls with an isolated server and relay evidence."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

if __name__ == "__main__":
    host = json.loads(Path(sys.argv[1]).read_text())
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": int(sys.argv[2]), "height": 852})
        context.add_cookies([{"name": "session", "value": host["cookie"], "url": host["origin"]}])
        page = context.new_page()
        page.goto(host["origin"] + "/car_heater")
        duration = page.get_by_label("Manual heating duration (minutes)", exact=True)
        expect(duration).to_have_value("60")
        duration.fill("181")
        page.get_by_role("button", name="Start timed heating", exact=False).click()
        assert page.evaluate("document.getElementById('manualDuration').validity.rangeOverflow")
        duration.fill("60")
        page.get_by_role("button", name="Start timed heating", exact=False).click()
        expect(page.get_by_text("Start queued;", exact=False)).to_be_visible()
        start = page.locator("#btnTurnOn")
        expect(start).to_be_disabled()
        run = context.request.get(host["origin"] + "/api/car_heater/bounded").json()
        page.reload()
        expect(start).to_be_disabled()
        assert (
            context.request.get(host["origin"] + "/api/car_heater/bounded").json()["finish_at"]
            == run["finish_at"]
        )
        page.get_by_role("button", name="Stop heating", exact=False).click()
        expect(page.get_by_text("Stop pending;", exact=False)).to_be_visible()
        expect(start).to_be_disabled()
        token = page.locator('meta[name="csrf-token"]').get_attribute("content")
        response = context.request.post(
            host["origin"] + "/test/relay-off", headers={"X-CSRFToken": token}
        )
        assert response.status == 200
        expect(page.get_by_text("Stop confirmed", exact=False)).to_be_visible(timeout=12000)
        expect(start).to_be_enabled()
        # Hold the start response after the server accepts it. Stop must still
        # submit immediately, and the late start response must not undo Stop UI.
        held = {}

        def hold_start(route):
            if route.request.method == "POST":
                held["route"] = route
                held["response"] = route.fetch()
            else:
                route.continue_()

        page.route("**/api/car_heater/bounded", hold_start)
        page.get_by_role("button", name="Start timed heating", exact=False).click()
        expect(page.get_by_text("Requesting timed heating", exact=False)).to_be_visible()
        page.get_by_role("button", name="Stop heating", exact=False).click()
        expect(page.get_by_text("Stop pending;", exact=False)).to_be_visible()
        held["route"].fulfill(response=held["response"])
        expect(page.get_by_text("Stop pending;", exact=False)).to_be_visible()
        assert (
            context.request.get(host["origin"] + "/api/car_heater/bounded").json()["phase"]
            == "stopping"
        )
        context.request.post(host["origin"] + "/test/relay-off", headers={"X-CSRFToken": token})
        expect(page.get_by_text("Stop confirmed", exact=False)).to_be_visible(timeout=12000)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=f"/tmp/car-heater-bounded-{sys.argv[2]}.png", full_page=True)
        page.get_by_role("link", name="Review this heating session").click()
        expect(page.get_by_role("heading", name="Heating execution")).to_be_visible()
        expect(page.get_by_text("off confirmed", exact=False)).to_be_visible()
        browser.close()
