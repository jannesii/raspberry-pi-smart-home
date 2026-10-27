"""Save, inspect and edit recurring reminders in a browser in Asia/Tokyo.

Times are Helsinki wall-clock text; the browser timezone must not shift them.
The host clock starts on Saturday 2027-03-27, the day before the spring change.
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
    context.request.get(origin + "/test/clock?at=2027-03-27T12:00:00%2B00:00")
    page = context.new_page()
    page.goto(origin + "/settings/announcements")
    page.locator('input[name="announceTarget"][value="jbl-right"]').check()

    # Daily at a time that does not exist on the night clocks move forward.
    page.get_by_label("Reminder text").fill("Yölääke")
    expect(page.get_by_label("Date (Helsinki)")).to_be_visible()
    page.get_by_label("Every day").check()
    expect(page.get_by_label("Date (Helsinki)")).to_be_hidden()
    expect(page.locator("#announceWeekdayGroup")).to_be_hidden()
    page.get_by_label("Time 1", exact=True).fill("03:30")
    page.get_by_role("button", name="Save reminder").click()
    message = page.locator("#announceMessage")
    expect(message).to_contain_text("Every day at 03:30")
    expect(message).to_contain_text("does not exist")
    nightly = page.locator(".announce-reminder", has_text="Yölääke")
    expect(nightly).to_contain_text("28.3.2027 03:30")

    context.request.get(origin + "/test/clock?at=2027-03-28T01:30:00%2B00:00")
    page.reload()
    missed = page.locator(".announce-occurrence", has_text="Yölääke").first
    expect(missed).to_contain_text("Missed (clock change)")
    expect(missed).to_contain_text("28.3.2027 03:30")
    expect(missed).to_contain_text("Helsinki clocks moved forward")
    assert context.request.get(origin + "/test/cast/plays").json() == []

    # Selected weekdays with two times; validation keeps the entered values.
    page.get_by_label("Reminder text").fill("Mittaa verenpaine")
    page.locator('input[name="announceTarget"][value="jbl-right"]').check()
    page.get_by_label("Selected weekdays").check()
    for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
        page.get_by_label(day, exact=True).uncheck()
    page.get_by_label("Time 1", exact=True).fill("08:00")
    page.get_by_role("button", name="Save reminder").click()
    expect(page.locator("#announceRecurrenceError")).to_have_text("Choose at least one weekday.")
    expect(page.get_by_label("Mon", exact=True)).to_be_focused()
    page.get_by_label("Mon", exact=True).check()
    page.get_by_label("Wed", exact=True).check()
    page.get_by_role("button", name="Add time").click()
    expect(page.get_by_label("Time 2", exact=True)).to_be_focused()
    page.get_by_role("button", name="Save reminder").click()
    expect(page.locator("#announceRecurrenceError")).to_contain_text("Fill in every time")
    expect(page.get_by_label("Time 1", exact=True)).to_have_value("08:00")
    page.get_by_label("Time 2", exact=True).fill("20:30")
    page.get_by_role("button", name="Save reminder").click()
    expect(message).to_contain_text("Mon, Wed at 08:00, 20:30")
    expect(message).to_contain_text("29.3.2027")  # Monday, now summer time
    expect(message).to_contain_text("08.00")  # Helsinki, not Tokyo (14.00)
    weekly = page.locator(".announce-reminder", has_text="Mittaa verenpaine")
    expect(weekly).to_contain_text("Next")

    # Editing restores the recurring controls; changes apply to the future.
    weekly.get_by_role("button", name="Edit reminder: Mittaa verenpaine").click()
    expect(page.get_by_label("Selected weekdays")).to_be_checked()
    expect(page.get_by_label("Mon", exact=True)).to_be_checked()
    expect(page.get_by_label("Tue", exact=True)).not_to_be_checked()
    expect(page.get_by_label("Time 2", exact=True)).to_have_value("20:30")
    page.get_by_role("button", name="Remove time 1 (08:00)").click()
    expect(page.get_by_label("Time 1", exact=True)).to_be_focused()
    page.get_by_role("button", name="Save changes").click()
    expect(message).to_contain_text("Reminder updated")
    expect(weekly.locator("strong").first).to_have_text("Mon, Wed at 20:30")

    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 0, f"horizontal scroll at {width}px: {overflow}"
    browser.close()
