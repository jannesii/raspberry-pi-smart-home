"""Actual planner/persistence and Heating page with controlled integrations."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

host = json.loads(Path(sys.argv[1]).read_text())
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    context = browser.new_context(
        viewport={"width": int(sys.argv[2]), "height": 852}, timezone_id="America/Los_Angeles"
    )
    context.add_cookies([{"name": "session", "value": host["cookie"], "url": host["origin"]}])
    context.route("https://**", lambda route: route.abort())
    page = context.new_page()
    page.clock.set_fixed_time(datetime(2027, 1, 15, 22, 30, tzinfo=UTC))
    page.goto(host["origin"] + "/car_heater")
    form = page.locator("#departureForm")
    expect(form).to_be_visible()
    workspace = page.get_by_role("region", name="Next departure", exact=True).bounding_box()
    controls = page.get_by_role(
        "complementary", name="Heater controls and conditions", exact=True
    ).bounding_box()
    if int(sys.argv[2]) >= 768:
        assert workspace["x"] + workspace["width"] <= controls["x"]
    else:
        assert workspace["y"] + workspace["height"] <= controls["y"]
    page.get_by_role("button", name="Tomorrow", exact=True).click()
    expect(page.get_by_label("Departure date", exact=True)).to_have_value("2027-01-17")
    page.get_by_label("Departure date", exact=True).fill("2027-01-16")
    page.get_by_label("Departure time", exact=True).fill("08:00")
    page.get_by_role("button", name="Preview departure", exact=True).click()
    expect(page.locator("#departurePreview")).to_contain_text("150 min preheat")
    expect(page.locator("#departurePreview")).to_contain_text("05:30")
    expect(page.locator("#departurePreview")).to_contain_text("08:30")
    assert context.request.get(host["origin"] + "/api/car_heater/departure").json() is None
    page.get_by_role("button", name="Save departure", exact=True).click()
    expect(page.locator("#departureSaved")).to_contain_text("Scheduled")
    first = context.request.get(host["origin"] + "/api/car_heater/departure").json()
    page.get_by_label("Departure time", exact=True).fill("09:00")
    expect(page.get_by_role("button", name="Save departure", exact=True)).to_be_disabled()
    page.reload()
    expect(page.get_by_label("Departure time", exact=True)).to_have_value("08:00")
    page.get_by_label("Departure time", exact=True).fill("09:00")
    page.get_by_role("button", name="Preview departure", exact=True).click()
    expect(page.get_by_role("button", name="Save departure", exact=True)).to_be_enabled()
    page.get_by_role("button", name="Save departure", exact=True).click()
    expect(page.locator("#departureSaved")).to_contain_text("09:00")
    second = context.request.get(host["origin"] + "/api/car_heater/departure").json()
    assert second["supersedes"] == first["session_id"]
    page.get_by_role("button", name="Cancel upcoming departure", exact=True).click()
    expect(page.locator("#departureSaved")).to_contain_text("Cancelled")
    page.reload()
    expect(page.locator("#departureSaved")).to_contain_text("Cancelled")
    page.get_by_label("Departure date", exact=True).fill("2027-03-28")
    page.get_by_label("Departure time", exact=True).fill("03:30")
    page.get_by_role("button", name="Preview departure", exact=True).click()
    expect(page.locator("#departureFeedback")).to_contain_text("does not exist")
    expect(page.get_by_role("button", name="Save departure", exact=True)).to_be_disabled()
    page.get_by_label("Departure date", exact=True).fill("2027-10-31")
    page.get_by_role("button", name="Preview departure", exact=True).click()
    expect(page.locator("#departureFeedback")).to_contain_text("occurs twice")
    page.get_by_text("Clock changes and departure grace", exact=True).click()
    page.get_by_label("Repeated autumn time", exact=True).select_option("1")
    page.get_by_role("button", name="Preview departure", exact=True).click()
    expect(page.locator("#departurePreview")).to_contain_text("EET")
    expect(page.locator("#departurePreview")).to_contain_text("EEST")
    page.get_by_label("Departure date", exact=True).fill("2027-01-16")
    page.get_by_label("Departure time", exact=True).fill("09:00")
    page.get_by_label("Repeated autumn time", exact=True).select_option("")
    token = page.locator('meta[name="csrf-token"]').get_attribute("content")
    auth = {"X-CSRFToken": token}
    assert (
        context.request.post(
            host["origin"] + "/test/departure-weather", headers=auth, data={"temperature": None}
        ).status
        == 200
    )
    page.get_by_role("button", name="Preview departure", exact=True).click()
    expect(page.locator("#departurePreview")).to_contain_text("180 min preheat")
    expect(page.locator("#departurePreview")).to_contain_text("Fallback")
    page.get_by_role("button", name="Save departure", exact=True).click()
    expect(page.locator("#departureSaved")).to_contain_text("Scheduled")
    advanced = context.request.post(host["origin"] + "/test/departure-time", headers=auth)
    assert advanced.status == 200
    page.clock.set_fixed_time(datetime.fromtimestamp(advanced.json()["now"], UTC))
    page.reload()
    expect(page.locator("#departureSaved")).to_contain_text("late start")
    expect(page.get_by_role("button", name="Save departure", exact=True)).to_be_disabled()
    expect(page.locator("#boundedHeatingStatus")).to_contain_text("Start queued")
    expect(page.get_by_role("button", name="Stop heating", exact=False)).to_be_enabled()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=f"/tmp/car-heater-departure-{sys.argv[2]}.png", full_page=True)
    page.get_by_role("link", name="Review departure session", exact=True).click()
    expect(page.get_by_role("heading", name="Departure plan", exact=True)).to_be_visible()
    expect(page.locator("#sessionDetail")).to_contain_text("FMI observations")
    browser.close()
