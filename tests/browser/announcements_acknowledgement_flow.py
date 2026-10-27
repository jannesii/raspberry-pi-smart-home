"""Repeat explanation, Stop speaking, Done and Skip in a browser in Asia/Tokyo.

The host clock starts at 2027-01-15 12:00 UTC (14:00 Helsinki). Page requests
tick the service, so reloading after moving the clock drives processing.
"""

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
    page.get_by_label("Reminder text").fill("Ota iltalääke")
    page.get_by_label("Date (Helsinki)").fill("2027-01-15")
    page.get_by_label("Time (Helsinki)").fill("14:30")

    # Advanced repeats explain what fits inside the default fifteen minutes.
    page.locator("#announceAdvanced summary").click()
    fit = page.locator("#announceRepeatFit")
    expect(fit).to_contain_text("One announcement")
    expect(page.get_by_label("Minutes between them")).to_be_disabled()
    page.get_by_label("Announcements per reminder").fill("3")
    page.get_by_role("button", name="Save reminder").click()
    expect(page.locator("#announceRepeatError")).to_contain_text("minutes between")
    expect(page.get_by_label("Minutes between them")).to_be_focused()
    page.get_by_label("Minutes between them").fill("10")
    expect(fit).to_contain_text("2 of 3 announcements fit before the reminder expires (15 min)")
    expect(fit).to_contain_text("third would come after expiry")
    page.get_by_role("button", name="Save reminder").click()
    message = page.locator("#announceMessage")
    expect(message).to_contain_text("Reminder saved")
    reminder = page.locator(".announce-reminder", has_text="Ota iltalääke")
    expect(reminder).to_contain_text("Up to 3, every 10 min; only 2 fit before expiry")

    # Due: it speaks; Stop speaking silences it without acknowledging it.
    context.request.get(origin + "/test/clock?at=2027-01-15T12:30:00%2B00:00")
    page.reload()
    context.request.get(origin + "/test/cast/report?state=playing")
    page.reload()
    occurrence = page.locator(".announce-occurrence", has_text="Ota iltalääke").first
    expect(occurrence).to_contain_text("Not acknowledged")
    occurrence.get_by_role("button", name="Stop speaking: Ota iltalääke").click()
    expect(message).to_contain_text("Stop requested")
    page.reload()
    occurrence = page.locator(".announce-occurrence", has_text="Ota iltalääke").first
    expect(occurrence).to_contain_text("speaker confirmed it stopped")
    expect(occurrence).to_contain_text("Stopped")
    expect(occurrence.get_by_role("button", name="Stop speaking: Ota iltalääke")).to_have_count(0)

    # Done stays available after stopping and is shown apart from delivery.
    occurrence.get_by_role("button", name="Done: Ota iltalääke").click()
    expect(message).to_contain_text("Marked done")
    occurrence = page.locator(".announce-occurrence", has_text="Ota iltalääke").first
    expect(occurrence).to_contain_text("Done by owner")
    expect(
        occurrence.get_by_role("button", name="Skip this occurrence: Ota iltalääke")
    ).to_have_count(0)
    context.request.get(origin + "/test/clock?at=2027-01-15T12:44:00%2B00:00")
    page.reload()
    assert len(context.request.get(origin + "/test/cast/plays").json()) == 1

    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    browser.close()
