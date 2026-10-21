"""Calibration Settings on the real authenticated page.

Saves go through the public calibration endpoints the page uses; the timed
session goes through the public bounded endpoint and real HTTP status ingest.
"""

import json
import re
import sys
from datetime import datetime
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
            viewport={"width": width, "height": 852},
            timezone_id="America/Los_Angeles",
            reduced_motion="reduce",
        )
        context.add_cookies([{"name": "session", "value": host["cookie"], "url": origin}])
        page = context.new_page()
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(origin + "/car_heater/settings#calibration")
        token = page.locator('meta[name="csrf-token"]').get_attribute("content")
        auth = {"X-CSRFToken": token}

        def frame(**spec):
            response = context.request.post(origin + "/test/frame", headers=auth, data=spec)
            assert response.status == 200, response.text()

        def saved():
            return context.request.get(origin + "/api/car_heater/calibration").json()

        def no_overflow():
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

        group = page.locator("#calibration")
        expect(group.get_by_role("heading", name="Calibration", exact=True)).to_be_visible()
        expect(group.locator(".calibration-badge")).to_have_text("Experimental")
        expect(group).to_contain_text("never controls departure heating")
        expect(page.locator("#calState")).to_have_text("Idle, waiting for heating to record")
        expect(page.locator("#calVersion")).to_have_text("Model v1 · defaults, never saved")
        expect(page.locator("#calLastSession")).to_have_text("No calibration session recorded yet")
        expect(page.locator("#calWindow")).to_have_text("00:00\u201323:59 (Helsinki)")
        expect(page.locator("#calAutonomousState")).to_have_text("Autonomous calibration off")

        # Advanced tuning starts collapsed and opens from the keyboard.
        grace = page.get_by_label("Grace samples (reports)", exact=True)
        expect(grace).to_be_hidden()
        summary = page.locator("#calAdvanced > summary")
        summary.focus()
        assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") != "none"
        page.keyboard.press("Enter")
        expect(grace).to_be_visible()
        expect(grace).to_have_value("3")
        expect(page.locator("#cal_grace_samples_help")).to_contain_text("1\u201310.")
        expect(page.get_by_label("η minimum (0\u20131)", exact=True)).to_be_hidden()
        page.locator("#calExpert > summary").click()
        expect(page.get_by_label("η minimum (0\u20131)", exact=True)).to_have_value("0.05")
        no_overflow()

        # Editing alone never saves.
        grace.fill("5")
        page.reload()
        assert saved()["config"]["grace_samples"] == 3

        # Out-of-range input is refused by the browser and saves nothing.
        summary.click()
        grace.fill("11")
        page.get_by_role("button", name="Save calibration settings", exact=True).click()
        assert page.evaluate("document.getElementById('cal_grace_samples').validity.rangeOverflow")
        assert saved()["config"]["grace_samples"] == 3

        grace.fill("5")
        page.get_by_label("Start (HH:MM)", exact=True).fill("22:30")
        page.get_by_label("Command cooldown (s)", exact=True).fill("60")
        page.get_by_role("button", name="Save calibration settings", exact=True).click()
        expect(page.locator("#calibrationFeedback")).to_have_text(
            "Saved 3 settings. Future fits and estimates use them."
        )
        helsinki_hour = datetime.now(ZoneInfo("Europe/Helsinki")).strftime("%H:")
        expect(page.locator("#calVersion")).to_contain_text("Model v1 · settings saved")
        expect(page.locator("#calVersion")).to_contain_text(helsinki_hour)
        expect(page.locator("#calWindow")).to_have_text("22:30\u201323:59 (Helsinki)")
        page.reload()
        page.locator("#calAdvanced > summary").click()
        expect(grace).to_have_value("5")
        expect(page.get_by_label("Start (HH:MM)", exact=True)).to_have_value("22:30")
        expect(page.get_by_label("Command cooldown (s)", exact=True)).to_have_value("60")
        expect(page.get_by_label("Reach tolerance (min)", exact=True)).to_have_value("2")

        # A failed save is reported from the server's reply; nothing changes.
        page.route(
            "**/api/car_heater/calibration",
            lambda route: route.fulfill(
                status=500,
                content_type="application/json",
                body=json.dumps({"error": "Calibration settings were not saved"}),
            )
            if route.request.method == "PATCH"
            else route.continue_(),
        )
        grace.fill("7")
        page.get_by_role("button", name="Save calibration settings", exact=True).click()
        expect(page.locator("#calibrationFeedback")).to_have_text(
            "Not saved: Calibration settings were not saved"
        )
        page.unroute("**/api/car_heater/calibration")
        page.reload()
        page.locator("#calAdvanced > summary").click()
        expect(grace).to_have_value("5")

        # Autonomous calibration is explained, confirmed and can be turned on.
        toggle = page.get_by_role("button", name="Turn on autonomous calibration", exact=True)
        toggle.click()
        expect(page.locator("#calAutonomousState")).to_have_text("Autonomous calibration on")
        expect(page.locator("#calActionFeedback")).to_have_text("Autonomous calibration is on.")

        # A timed session takes ownership: autonomous calibration is suspended
        # and cannot start again until the session is confirmed OFF.
        frame(relay=False, power=0)
        started = context.request.post(
            origin + "/api/car_heater/bounded", headers=auth, data={"duration_minutes": 30}
        )
        assert started.ok, started.text()
        expect(page.locator("#calAutonomousLock")).to_have_text(
            "Stop heating and wait for confirmed OFF before starting another mode.",
            timeout=12000,
        )
        expect(page.locator("#calAutonomousState")).to_have_text("Autonomous calibration off")
        expect(toggle).to_be_disabled()
        bypass = context.request.patch(
            origin + "/api/car_heater/calibration",
            headers=auth,
            data={"autonomous_enabled": True},
        )
        assert bypass.status == 409
        assert saved()["autonomous_enabled"] is False
        # Model settings stay editable while the session owns the relay.
        grace.fill("4")
        page.get_by_role("button", name="Save calibration settings", exact=True).click()
        expect(page.locator("#calibrationFeedback")).to_have_text(
            "Saved 1 setting. Future fits and estimates use them."
        )

        context.request.delete(origin + "/api/car_heater/bounded", headers=auth)
        expect(toggle).to_be_disabled()
        frame(relay=False, power=0, session="current", phase="stopped")
        expect(toggle).to_be_enabled(timeout=12000)
        expect(page.locator("#calAutonomousLock")).to_have_text("")

        # Reset restores defaults after confirmation and keeps autonomous off.
        page.get_by_role("button", name="Reset model settings to defaults", exact=True).click()
        expect(page.locator("#calActionFeedback")).to_have_text(
            "Model settings reset to defaults. Autonomous calibration is off."
        )
        expect(grace).to_have_value("3")
        expect(page.get_by_label("Start (HH:MM)", exact=True)).to_have_value("00:00")
        expect(page.get_by_label("Command cooldown (s)", exact=True)).to_have_value("60")
        page.get_by_role("button", name="Reset cooldown", exact=True).click()
        expect(page.locator("#calActionFeedback")).to_have_text(
            "Cooldown cleared. Heating was not started."
        )
        expect(page.locator("#calCooldown")).to_have_text("No cooldown")

        # Recorded data loads when opened and stays honest when empty.
        data = page.locator("#calData > summary")
        data.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#calSessions tbody")).to_have_text(
            "No calibration sessions recorded yet."
        )
        expect(page.locator("#calBuckets")).to_have_text("No bucket has been calibrated yet.")
        expect(page.locator("#calSessionCount")).to_have_text("0")
        expect(page.locator("#calStats")).to_contain_text("Sessions recorded0")
        expect(page.locator("#calStats")).to_contain_text("Average quality—")
        page.get_by_role("button", name="Refresh recorded data", exact=True).click()
        expect(page.locator("#calDataStatus")).to_have_text("")

        for control in (
            page.get_by_role("button", name="Turn on autonomous calibration", exact=True),
            page.get_by_role("button", name="Save calibration settings", exact=True),
            page.get_by_role("button", name="Reset model settings to defaults", exact=True),
            summary,
            data,
            grace,
        ):
            assert control.bounding_box()["height"] >= 44
        assert re.search(r"\d", page.locator("#calParams").inner_text())
        no_overflow()
        page.screenshot(path=f"/tmp/car-heater-calibration-{width}.png", full_page=True)
        group.screenshot(path=f"/tmp/car-heater-calibration-group-{width}.png")
        browser.close()
