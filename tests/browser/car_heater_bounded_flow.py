"""Manual heating on the real Heating page, driven by firmware-shaped frames.

Frames enter through the real HTTP status and WebSocket bridge ingest, so
delivery, acknowledgements and relay evidence take the production paths.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
        dialogs = []
        page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
        page.goto(origin + "/car_heater")
        token = page.locator('meta[name="csrf-token"]').get_attribute("content")
        auth = {"X-CSRFToken": token}

        def frame(**spec):
            response = context.request.post(origin + "/test/frame", headers=auth, data=spec)
            assert response.status == 200, response.text()

        def bounded():
            return context.request.get(origin + "/api/car_heater/bounded").json()

        state = page.locator("#heaterState")
        start = page.get_by_role("button", name="Start timed heating", exact=True)
        stop = page.locator("#btnTurnOff")
        duration = page.get_by_label("Manual heating duration (minutes)", exact=True)
        comfort = page.get_by_label("Keep at Temperature", exact=True)
        nav = page.get_by_role("navigation", name="Car heater", exact=True)
        expect(nav.get_by_role("link", name="Heating", exact=True)).to_have_attribute(
            "aria-current", "page"
        )

        # A's principal departure column and secondary controls/conditions column.
        principal = page.get_by_role("region", name="Next departure", exact=True).bounding_box()
        secondary = page.get_by_role(
            "complementary", name="Heater controls and conditions", exact=True
        ).bounding_box()
        if width >= 768:
            assert principal["x"] + principal["width"] <= secondary["x"]
        else:
            assert principal["y"] + principal["height"] <= secondary["y"]
        expect(page.locator("#departureBigTime")).to_have_text("—")
        # This host has no departure planner: the plan is unavailable, not absent.
        expect(page.locator("#departureBigDate")).to_have_text("Departure plan unavailable")

        # Without a device report nothing is assumed, including OFF.
        expect(state).to_contain_text("Heater state unknown")
        expect(page.locator("#deviceFreshness")).to_contain_text("No device report yet")
        expect(page.locator("#conditionPower")).to_have_text("—")
        expect(page.locator("#heroOutsideTemp")).to_have_text("—")

        # The first frame: true zero power and humidity stay zero; a missing
        # glass reading stays missing and is labelled per measurement.
        frame(relay=False, power=0)
        expect(page.locator("#deviceFreshness")).to_contain_text("Device reported", timeout=12000)
        expect(page.locator("#conditionPower")).to_have_text("0 W")
        expect(page.locator("#sensorCabinHumidity .sensor-value")).to_have_text("0 %")
        expect(page.locator("#sensorCabinTemp .sensor-value")).to_have_text("-2.5 °C")
        expect(page.locator("#sensorGlassReference .sensor-value")).to_have_text("—")
        expect(page.locator("#sensorGlassReference .sensor-state")).to_have_text("Missing")
        expect(state).to_contain_text("Heater OFF")

        # Default and selected durations; the limit is enforced in the browser.
        expect(duration).to_have_value("60")
        page.get_by_role("button", name="120 min", exact=True).click()
        expect(duration).to_have_value("120")
        expect(page.get_by_role("button", name="120 min", exact=True)).to_have_attribute(
            "aria-pressed", "true"
        )
        duration.fill("181")
        start.click()
        assert page.evaluate("document.getElementById('manualDuration').validity.rangeOverflow")
        assert bounded() is None
        duration.fill("45")
        frame(relay=False, power=0)
        start.click()
        expect(state).to_contain_text("Start requested")
        run = bounded()
        assert run["mode"] == "manual"
        finish = datetime.fromtimestamp(run["finish_at"], UTC).astimezone(
            ZoneInfo("Europe/Helsinki")
        )
        expect(page.locator("#boundedMode")).to_have_text("Manual")
        expect(page.locator("#boundedFinish")).to_contain_text(finish.strftime("%H:%M"))
        expect(page.locator("#boundedRemaining")).to_contain_text("45 min")

        # Competing starts are unavailable with a visible reason, and the
        # service refuses a request that bypasses the disabled controls.
        reason = "Stop heating and wait for confirmed OFF before starting another mode."
        expect(start).to_be_disabled()
        expect(duration).to_be_disabled()
        expect(page.locator("#modeLockReason")).to_have_text(reason)
        page.get_by_text("Comfort heating", exact=True).click()
        expect(comfort).to_be_disabled()
        expect(page.locator("#comfortLockReason")).to_have_text(reason)
        bypass = context.request.post(
            origin + "/api/car_heater/bounded", headers=auth, data={"duration_minutes": 180}
        )
        assert bypass.status == 409
        page.reload()
        expect(start).to_be_disabled()
        assert bounded()["finish_at"] == run["finish_at"]

        # Relay evidence over the WebSocket bridge confirms ON; an old
        # session's acknowledgement changes nothing.
        frame(transport="ws", relay=True, power=1500, session="current", phase="heating")
        expect(state).to_contain_text("Heating · confirmed ON", timeout=12000)
        expect(page.locator("#conditionPower")).to_have_text("1500 W", timeout=12000)
        frame(relay=True, power=1500, session="current", phase="heating")
        frame(
            relay=True,
            power=1500,
            session="current",
            phase="heating",
            ack={"action": "turn_off", "success": True, "session": "old"},
        )
        assert bounded()["phase"] == "heating"

        # A's principal column carries the active session: its limit, Stop and
        # the session's observation capture.
        notice = page.locator("#activeNotice")
        expect(notice).to_contain_text("No restart or new selection extends this limit.")
        expect(page.get_by_role("link", name="Observe windshield", exact=True)).to_have_attribute(
            "href", f"/car_heater/sessions/{run['session_id']}/capture"
        )
        for control in (stop, page.get_by_role("button", name="60 min", exact=True)):
            assert control.bounding_box()["height"] >= 44
        if width < 768:
            # Stop is reachable before the departure form on a phone.
            form = page.locator("#departureForm").bounding_box()
            assert stop.bounding_box()["y"] < form["y"]

        # Stop needs no confirmation dialog and is operable from the keyboard,
        # with visible focus.
        stop.focus()
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.id") == "btnTurnOff"
        outline = page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
        assert outline != "none", outline
        page.keyboard.press("Enter")
        expect(state).to_contain_text("Stopping")
        assert dialogs == []
        assert bounded()["off_state"] == "pending"
        # An acknowledged OFF is not shutdown; only matching relay evidence is.
        frame(
            relay=False,
            power=0,
            session="current",
            phase="heating",
            ack={"action": "turn_off", "success": True, "session": "current"},
        )
        expect(state).to_contain_text("Stopping")
        expect(start).to_be_disabled()

        # Disconnection and a failed OFF stay explicit, and OFF stays available.
        frame(connected=False)
        expect(state).to_contain_text("relay state unknown", timeout=12000)
        expect(page.locator("#conditionPower")).to_have_text("—", timeout=12000)
        expect(stop).to_be_enabled()
        expect(stop).to_have_text("Request OFF again")
        expect(notice).to_contain_text("OFF has not been confirmed")
        frame(connected=False, ack={"action": "turn_off", "success": False, "session": "current"})
        expect(state).to_contain_text("Stop not confirmed", timeout=12000)
        stop.click()
        expect(state).to_contain_text("Stopping")

        # Reconnection with matching stopped evidence confirms OFF.
        frame(transport="ws", relay=False, power=0, session="current", phase="stopped")
        expect(state).to_contain_text("OFF confirmed", timeout=12000)
        expect(start).to_be_enabled()
        expect(comfort).to_be_enabled()
        expect(page.locator("#modeLockReason")).to_have_text("")
        expect(page.locator("#boundedRemaining")).to_have_text("—")

        # A late start response must not undo a Stop submitted while it was held.
        held = {}

        def hold_start(route):
            if route.request.method == "POST":
                held["route"] = route
                held["response"] = route.fetch()
            else:
                route.continue_()

        frame(relay=False, power=0)
        page.route("**/api/car_heater/bounded", hold_start)
        start.click()
        expect(page.locator("#boundedHeatingStatus")).to_contain_text("Requesting timed heating")
        stop.click()
        expect(state).to_contain_text("Stopping")
        held["route"].fulfill(response=held["response"])
        expect(state).to_contain_text("Stopping")
        assert bounded()["phase"] == "stopping"
        frame(relay=False, power=0, session="current", phase="stopped")
        expect(state).to_contain_text("OFF confirmed", timeout=12000)
        # With the relay reporting OFF the active-session block steps aside.
        expect(page.locator("#activeSession")).to_be_hidden()

        # Touch targets, reduced motion, no overflow.
        assert start.bounding_box()["height"] >= 44
        start.focus()
        page.emulate_media(reduced_motion="reduce")
        assert page.evaluate("getComputedStyle(document.activeElement).transitionDuration") in (
            "0s",
            "0s, 0s",
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=f"/tmp/car-heater-bounded-{width}.png", full_page=True)
        page.get_by_role("link", name="Review this heating session").click()
        expect(page.get_by_role("heading", name="Heating execution")).to_be_visible()
        expect(page.get_by_text("off confirmed", exact=False)).to_be_visible()
        browser.close()
