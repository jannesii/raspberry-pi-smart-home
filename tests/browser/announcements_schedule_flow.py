"""Save, inspect, deliver, edit, pause and delete a one-time reminder in a browser.

The browser runs in America/Los_Angeles: the saved time must still be Helsinki.
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
        viewport={"width": width, "height": 900}, timezone_id="America/Los_Angeles"
    )
    context.add_cookies([{"name": "session", "value": host["cookie"], "url": origin}])
    context.route("https://**", lambda route: route.abort())
    page = context.new_page()
    page.goto(origin + "/settings/announcements")
    expect(page.locator("#announceStatusQuiet")).to_have_text("22:00\u201307:00")
    expect(page.locator("#announceStatusRestore")).to_contain_text("Unavailable")
    expect(page.locator("#announceSchedulesList")).to_contain_text("No reminders yet")

    page.get_by_label("Reminder text").fill("Muista ottaa <i>lääkkeet</i>.")
    page.locator('input[name="announceTarget"][value="jbl-right"]').check()
    page.get_by_label("Date (Helsinki)").fill("2027-01-15")
    page.get_by_role("button", name="Save reminder").click()
    expect(page.locator("#announceDueError")).to_have_text("Choose a date and a time.")
    expect(page.get_by_label("Time (Helsinki)")).to_be_focused()

    # Progressive disclosure: advanced settings are collapsed but reachable.
    advanced = page.locator("#announceAdvanced")
    expect(page.get_by_label("Expires after (minutes)")).to_be_hidden()
    advanced.locator("summary").click()
    expect(page.get_by_label("Expires after (minutes)")).to_have_value("15")
    expect(page.get_by_label("Urgent: may interrupt music")).not_to_be_checked()
    expect(advanced).to_contain_text("Urgent does not allow speaking during quiet hours")

    page.get_by_label("Time (Helsinki)").fill("14:30")
    page.get_by_role("button", name="Save reminder").click()
    expect(page.locator("#announceMessage")).to_contain_text("Reminder saved")
    expect(page.locator("#announceMessage")).to_contain_text("14.30")
    reminder = page.locator(".announce-reminder").first
    expect(reminder).to_contain_text("14.30")
    expect(reminder).to_contain_text("Scheduled")
    expect(reminder.locator(".announce-test-text")).to_have_text("Muista ottaa <i>lääkkeet</i>.")

    # A repeated autumn time needs an explicit choice.
    page.get_by_label("Reminder text").fill("Kaksi kertaa")
    page.get_by_label("Date (Helsinki)").fill("2027-10-31")
    page.get_by_label("Time (Helsinki)").fill("03:30")
    page.get_by_role("button", name="Save reminder").click()
    expect(page.locator("#announceDueError")).to_contain_text("occurs twice")
    page.get_by_label("Second (winter time, UTC+2)").check()
    page.get_by_role("button", name="Save reminder").click()
    expect(page.locator("#announceMessage")).to_contain_text("Reminder saved")
    expect(page.locator(".announce-reminder")).to_have_count(2)

    # Pause and resume the second reminder; then delete it with confirmation.
    second = page.locator(".announce-reminder", has_text="Kaksi kertaa")
    second.get_by_role("button", name="Pause reminder: Kaksi kertaa").click()
    expect(second).to_contain_text("Paused")
    second.get_by_role("button", name="Resume reminder: Kaksi kertaa").click()
    expect(second).to_contain_text("Scheduled")
    second.get_by_role("button", name="Delete reminder: Kaksi kertaa").click()
    second.get_by_role("button", name="Keep reminder: Kaksi kertaa").click()
    expect(page.locator(".announce-reminder")).to_have_count(2)
    second.get_by_role("button", name="Delete reminder: Kaksi kertaa").click()
    second.get_by_role("button", name="Confirm deleting reminder: Kaksi kertaa").click()
    expect(page.locator(".announce-reminder")).to_have_count(1)

    # Edit keeps the form values and changes the future time.
    reminder.get_by_role("button", name="Edit reminder: Muista ottaa <i>lääkkeet</i>.").click()
    expect(page.get_by_label("Time (Helsinki)")).to_have_value("14:30")
    page.get_by_label("Time (Helsinki)").fill("14:40")
    page.get_by_role("button", name="Save changes").click()
    expect(page.locator("#announceMessage")).to_contain_text("Reminder updated")
    expect(reminder).to_contain_text("14.40")

    # Due: the service speaks it and history shows the speaker's evidence.
    context.request.get(origin + "/test/clock?at=2027-01-15T12:40:00%2B00:00")
    page.reload()
    occurrence = page.locator(".announce-occurrence").first
    expect(occurrence).to_contain_text("Speaking")
    assert context.request.get(origin + "/test/cast/plays").json() == [{"target": "jbl-right"}]
    context.request.get(origin + "/test/cast/report?state=finished")
    page.reload()
    expect(occurrence).to_contain_text("Speaker reported playback")
    expect(occurrence).to_contain_text("Acknowledged")
    expect(occurrence).to_contain_text("Not recorded")
    expect(page.locator(".announce-reminder").first).to_contain_text("Due time passed")

    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 0, f"horizontal scroll at {width}px: {overflow}"
    browser.close()
