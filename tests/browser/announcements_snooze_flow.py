"""Stop, deliberate snooze, adjusted deadline and next-instance supersession."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

host = json.loads(Path(sys.argv[1]).read_text())
width = int(sys.argv[2])
origin = host["origin"]
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    context = browser.new_context(
        viewport={"width": width, "height": 900}, timezone_id="Asia/Tokyo"
    )
    context.add_cookies([{"name": "session", "value": host["cookie"], "url": origin}])
    context.route("https://**", lambda route: route.abort())
    page = context.new_page()
    page.goto(origin + "/settings/announcements")
    page.locator('input[name="announceTarget"][value="jbl-right"]').check()
    page.get_by_label("Reminder text").fill("Muista tauko")
    page.get_by_label("Every day").check()
    page.get_by_label("Time 1", exact=True).fill("14:30")
    page.get_by_role("button", name="Add time").click()
    page.get_by_label("Time 2", exact=True).fill("14:35")
    page.get_by_role("button", name="Save reminder").click()
    expect(page.locator("#announceMessage")).to_contain_text("Reminder saved")
    context.request.get(origin + "/test/clock?at=2027-01-15T12:30:00%2B00:00")
    page.reload()
    occurrence = page.locator(".announce-occurrence", has_text="Muista tauko").first
    occurrence.get_by_role("button", name="Stop speaking: Muista tauko", exact=True).click()
    expect(page.locator("#announceMessage")).to_contain_text("Stop requested")
    page.reload()
    choices = occurrence.get_by_label("Snooze minutes: Muista tauko")
    expect(choices).to_have_value("10")
    assert choices.locator("option").all_text_contents() == [
        "5 minutes",
        "10 minutes",
        "15 minutes",
        "30 minutes",
    ]
    choices.select_option("30")
    occurrence.get_by_role("button", name="Snooze: Muista tauko", exact=True).click()
    expect(page.locator("#announceMessage")).to_contain_text("Quiet hours still apply")
    expect(occurrence).to_contain_text("Snoozed until")
    expect(occurrence).to_contain_text("15.00")  # Helsinki; original expiry was 14:45
    expect(occurrence).to_contain_text("15.15")
    expect(occurrence).to_contain_text("Not acknowledged")
    assert len(context.request.get(origin + "/test/cast/plays").json()) == 1
    context.request.get(origin + "/test/clock?at=2027-01-15T12:35:00%2B00:00")
    page.reload()
    old = page.locator(".announce-occurrence", has_text="Muista tauko").filter(
        has_text="Superseded"
    )
    expect(old).to_have_count(1)
    expect(old).to_contain_text("Not acknowledged")
    expect(old.get_by_role("button", name="Snooze: Muista tauko", exact=True)).to_have_count(0)
    assert len(context.request.get(origin + "/test/cast/plays").json()) == 2
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    browser.close()
