"""Comfort heating and Settings groups on the real authenticated pages.

Frames enter through the real HTTP status ingest; saves go through the public
comfort and installation endpoints the page uses.
"""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

if __name__ == "__main__":
    host = json.loads(Path(sys.argv[1]).read_text())
    width = int(sys.argv[2])
    origin = host["origin"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport={"width": width, "height": 852}, timezone_id="America/Los_Angeles"
        )
        context.add_cookies([{"name": "session", "value": host["cookie"], "url": origin}])
        page = context.new_page()
        page.goto(origin + "/car_heater")
        token = page.locator('meta[name="csrf-token"]').get_attribute("content")
        auth = {"X-CSRFToken": token}

        def frame(**spec):
            response = context.request.post(origin + "/test/frame", headers=auth, data=spec)
            assert response.status == 200, response.text()

        def saved_comfort():
            return context.request.get(origin + "/api/car_heater/comfort").json()

        def no_overflow():
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

        # Battery-charge controls are not part of the redesigned page.
        expect(page.get_by_text("charge", exact=False)).to_have_count(0)

        # Comfort heating is collapsed until opened, then explains itself.
        section = page.locator("#comfortSection")
        summary = section.locator("summary").first
        expect(page.locator("#comfortTarget")).to_be_hidden()
        summary.focus()
        page.keyboard.press("Enter")
        target = page.get_by_label("Target cabin temperature (°C)", exact=True)
        start_comfort = page.get_by_role("button", name="Start comfort heating", exact=True)
        stop_comfort = page.get_by_role("button", name="Stop comfort heating", exact=True)
        expect(target).to_be_visible()
        expect(page.locator("#comfortState")).to_have_text("Comfort heating off")
        expect(page.locator("#comfortHysteresis")).to_have_text("—")
        # Without a saved hysteresis comfort cannot start, and says why.
        expect(start_comfort).to_be_disabled()
        expect(page.locator("#comfortLockReason")).to_contain_text("Heating preferences")

        # Editing alone never saves.
        target.fill("-4")
        page.reload()
        assert saved_comfort()["target_temperature_c"] is None

        # Settings: the shared navigation and four groups.
        page.get_by_role("navigation", name="Car heater").get_by_role(
            "link", name="Settings", exact=True
        ).click()
        groups = page.get_by_role("navigation", name="Settings groups", exact=True)
        for name in ("Heating preferences", "Installation & devices", "Calibration", "Diagnostics"):
            expect(groups.get_by_role("link", name=name, exact=True)).to_be_visible()
            expect(page.get_by_role("heading", name=name, exact=True)).to_be_visible()
            assert groups.get_by_role("link", name=name, exact=True).bounding_box()["height"] >= 44
        no_overflow()

        hysteresis = page.get_by_label("Comfort hysteresis (°C)", exact=True)
        expect(page.locator("#prefHysteresisHelp")).to_contain_text("switches on below -5.5 °C")
        hysteresis.fill("1.5")
        page.reload()
        expect(hysteresis).to_have_value("")
        hysteresis.fill("20")
        page.get_by_role("button", name="Save preferences", exact=True).click()
        assert page.evaluate("document.getElementById('prefHysteresis').validity.rangeOverflow")
        assert saved_comfort()["hysteresis_c"] is None
        hysteresis.fill("1.5")
        page.get_by_role("button", name="Save preferences", exact=True).click()
        expect(page.locator("#preferencesFeedback")).to_have_text("Saved. Hysteresis is 1.5 °C.")
        page.reload()
        expect(hysteresis).to_have_value("1.5")

        # A failed save is reported from the server's reply; nothing changes.
        page.route(
            "**/api/car_heater/comfort",
            lambda route: route.fulfill(
                status=500,
                content_type="application/json",
                body=json.dumps({"error": "Comfort settings were not saved"}),
            )
            if route.request.method == "PATCH"
            else route.continue_(),
        )
        hysteresis.fill("2")
        page.get_by_role("button", name="Save preferences", exact=True).click()
        expect(page.locator("#preferencesFeedback")).to_have_text(
            "Not saved: Comfort settings were not saved"
        )
        page.unroute("**/api/car_heater/comfort")
        page.reload()
        expect(hysteresis).to_have_value("1.5")

        # Installation & devices: nothing recorded. The host's startup relay
        # report carries no firmware version, so none is shown.
        expect(page.locator("#installationSaved")).to_contain_text("No installation recorded yet")
        expect(page.locator("#factFirmware")).to_have_text(
            "Not reported · this server was tested with 3.1.1"
        )
        expect(page.locator("#factRelay")).to_contain_text("Ready for timed heating")
        sensors = page.locator("#deviceSensors li")
        expect(sensors).to_have_count(3)
        expect(sensors.nth(0)).to_contain_text("No reading reported")
        expect(sensors.nth(0)).to_contain_text("TMP117, firmware default address 0x48")

        frame(relay=False, power=0)
        page.get_by_role("button", name="Refresh device facts", exact=True).click()
        expect(page.locator("#factFirmware")).to_have_text("3.1.1 · matches the tested version")
        expect(page.locator("#factRelay")).to_contain_text("Ready for timed heating · protocol 1")
        expect(sensors.nth(2)).to_contain_text("-2.5 °C · current · SHT45")
        expect(sensors.nth(0)).to_contain_text("No reading reported")

        reference = page.get_by_label("Glass reference sensor position", exact=True)
        reference.fill("Lower middle of the driver's view")
        save_installation = page.get_by_role("button", name="Save installation", exact=True)
        save_installation.focus()
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.id") == "installationSave"
        assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") != "none"
        page.keyboard.press("Enter")
        expect(page.locator("#installationFeedback")).to_contain_text("Saved.")
        expect(page.locator("#installationSaved")).to_contain_text("by owner")
        page.get_by_label("Cabin sensor position", exact=True).fill("Shaded console")
        save_installation.click()
        expect(page.locator("#installationHistoryCount")).to_have_text("1")
        page.reload()
        expect(reference).to_have_value("Lower middle of the driver's view")
        expect(page.get_by_label("Cabin sensor position", exact=True)).to_have_value(
            "Shaded console"
        )
        assert save_installation.bounding_box()["height"] >= 44
        no_overflow()
        page.screenshot(path=f"/tmp/car-heater-settings-{width}.png", full_page=True)

        # Back on Heating: comfort uses the saved hysteresis and starts.
        page.get_by_role("navigation", name="Car heater").get_by_role(
            "link", name="Heating", exact=True
        ).click()
        page.locator("#comfortSection summary").first.click()
        expect(page.locator("#comfortHysteresis")).to_have_text("1.5 °C")
        target.fill("-4")
        expect(page.get_by_role("button", name="Save target", exact=True)).to_be_enabled()
        start_comfort.click()
        expect(page.locator("#comfortState")).to_have_text(
            "Comfort heating on · keeping cabin near -4.0 °C"
        )
        expect(page.locator("#comfortSummaryState")).to_have_text("On")
        page.reload()
        page.locator("#comfortSection summary").first.click()
        expect(page.locator("#comfortState")).to_contain_text("Comfort heating on")
        expect(target).to_have_value("-4")

        # A timed session takes ownership: comfort is suspended and cannot
        # start again until the session is confirmed OFF.
        page.get_by_role("button", name="30 min", exact=True).click()
        page.get_by_role("button", name="Start timed heating", exact=True).click()
        expect(page.locator("#heaterState")).to_contain_text("Start requested")
        expect(page.locator("#comfortState")).to_have_text("Comfort heating off")
        expect(start_comfort).to_be_disabled()
        expect(page.locator("#comfortLockReason")).to_have_text(
            "Stop heating and wait for confirmed OFF before starting another mode."
        )
        bypass = context.request.patch(
            origin + "/api/car_heater/comfort", headers=auth, data={"enabled": True}
        )
        assert bypass.status == 409
        assert saved_comfort()["enabled"] is False
        page.locator("#btnTurnOff").click()
        expect(page.locator("#heaterState")).to_contain_text("Stopping")
        expect(start_comfort).to_be_disabled()
        frame(relay=False, power=0, session="current", phase="stopped")
        expect(page.locator("#heaterState")).to_contain_text("OFF confirmed", timeout=12000)
        expect(start_comfort).to_be_enabled()
        expect(page.locator("#comfortLockReason")).to_have_text("")
        expect(stop_comfort).to_be_disabled()

        for control in (start_comfort, stop_comfort, summary):
            assert control.bounding_box()["height"] >= 44
        no_overflow()
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=f"/tmp/car-heater-comfort-{width}.png", full_page=True)
        browser.close()
