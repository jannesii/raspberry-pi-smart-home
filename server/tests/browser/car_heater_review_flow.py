"""Session measurement review in a real browser against isolated persistence."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def top(locator):
    return locator.bounding_box()["y"]


def review_reads_outcome_then_timeline_then_honest_charts(context, origin, width):
    page = context.new_page()
    page.set_viewport_size({"width": width, "height": 852 if width < 600 else 1000})
    page.goto(origin + "/car_heater/sessions")
    token = page.locator('meta[name="csrf-token"]').get_attribute("content")
    seeded = context.request.post(origin + "/test/review-session", headers={"X-CSRFToken": token})
    assert seeded.status == 200, seeded.text()
    session_id = seeded.json()["session_id"]
    mutations = []
    page.on(
        "request",
        lambda req: mutations.append((req.method, req.url))
        if req.method in ("POST", "PUT", "DELETE")
        else None,
    )
    page.goto(f"{origin}/car_heater/sessions/{session_id}")

    outcome = page.get_by_role("heading", name="Outcome not recorded", exact=True)
    timeline = page.get_by_role("heading", name="Heating timeline", exact=True)
    temperatures = page.get_by_role("heading", name="Glass and cabin temperature (°C)", exact=True)
    humidity = page.get_by_role("heading", name="Cabin humidity (% RH)", exact=True)
    power = page.get_by_role("heading", name="Heater power (W)", exact=True)
    expect(temperatures).to_be_visible()
    assert top(outcome) < top(timeline) < top(temperatures) < top(humidity) < top(power)
    photos = page.get_by_role("heading", name="Photos", exact=True)
    if width >= 960:
        assert page.locator(".review-side").bounding_box()["x"] > temperatures.bounding_box()["x"]
    else:
        assert top(photos) > top(power)

    chart = page.locator("#temperatureChart")
    for name in ("Glass reference (°C)", "Glass comparison (°C)", "Cabin temperature (°C)"):
        expect(chart.locator(".review-legend")).to_contain_text(name)
    expect(chart.locator(".review-legend")).to_contain_text("Experimental +3 °C")
    expect(chart.locator(".review-summary")).to_contain_text(
        "Glass comparison: 111 readings, -7.5 °C to 4.5 °C"
    )
    expect(chart.locator(".review-summary")).to_contain_text("2 gaps in readings")
    assert chart.locator("polyline").count() >= 6  # three series, each broken by gaps
    expect(page.locator("#humidityChart .review-summary")).to_contain_text(
        "Cabin humidity: 117 readings"
    )
    expect(page.locator("#powerChart .review-summary")).to_contain_text("Heater power: 60 readings")
    expect(page.locator("#powerChart")).to_contain_text("Energy from the Shelly counter")
    expect(page.locator("#powerChart")).to_contain_text(
        "Shelly relay reported ON in 60 and OFF in 0 of 60 reports"
    )
    indicator = page.get_by_role("group", name="Experimental glass indicator")
    expect(indicator).to_contain_text("Not validated defrost readiness. It never stops heating")
    expect(indicator).to_contain_text("02/10/2026, 05:55:00 (candidate time)")
    expect(page.locator("#measurementWindow")).to_contain_text("never as zero")
    expect(page.get_by_text("Captured 02/10/2026, 05:50:00", exact=False)).to_be_visible()
    expect(page.get_by_role("link", name="Settings", exact=True).last).to_be_visible()

    page.get_by_role("link", name="Record outcome", exact=True).click()
    page.get_by_label("Clear", exact=True).check()
    page.get_by_label("Already clear when observation began", exact=True).check()
    page.get_by_role("button", name="Save outcome", exact=True).click()
    expect(page.get_by_text("Outcome saved. Heating unchanged.", exact=True)).to_be_visible()
    clearing = page.locator("#sessionClearing")
    expect(clearing).to_contain_text("first photo (02/10/2026, 05:50:00)")
    expect(clearing).to_contain_text("exact clearing time is unknown")
    expect(clearing).to_contain_text("not a fully observed clearing trajectory")
    expect(page.locator(".evidence-photo")).to_have_count(2)  # saving keeps the loaded photos

    page.reload()
    expect(page.get_by_role("heading", name="Clear", exact=True)).to_be_visible()
    expect(page.locator("#sessionClearing")).to_contain_text("first photo (02/10/2026, 05:50:00)")
    expect(page.locator("#temperatureChart .review-legend")).to_contain_text("Glass reference")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=f"/tmp/car-heater-review-{width}.png", full_page=True)
    assert mutations == [("PUT", f"{origin}/api/car_heater/sessions/{session_id}/outcome")]


def measurement_failure_keeps_session_and_offers_retry(context, origin):
    page = context.new_page()
    page.goto(origin + "/car_heater/sessions")
    token = page.locator('meta[name="csrf-token"]').get_attribute("content")
    session_id = context.request.post(
        origin + "/test/review-session", headers={"X-CSRFToken": token}
    ).json()["session_id"]
    page.route(
        "**/measurements",
        lambda route: route.fulfill(status=503, json={"error": "Measurements unavailable"}),
    )
    page.goto(f"{origin}/car_heater/sessions/{session_id}")
    expect(page.get_by_role("heading", name="Outcome not recorded", exact=True)).to_be_visible()
    expect(page.locator("#measurementWindow")).to_contain_text("Measurements unavailable")
    page.unroute("**/measurements")
    page.get_by_role("button", name="Retry measurements", exact=True).click()
    expect(page.locator("#temperatureChart .review-legend")).to_contain_text("Glass reference")


if __name__ == "__main__":
    host = json.loads(Path(sys.argv[1]).read_text())
    width = int(sys.argv[2])
    with sync_playwright() as p:
        browser = p.chromium.launch(timeout=15000)
        context = browser.new_context(timezone_id="America/New_York", reduced_motion="reduce")
        context.add_cookies([{"name": "session", "value": host["cookie"], "url": host["origin"]}])
        context.route("https://**", lambda route: route.abort())
        if width:
            review_reads_outcome_then_timeline_then_honest_charts(context, host["origin"], width)
        else:
            measurement_failure_keeps_session_and_offers_retry(context, host["origin"])
        browser.close()
