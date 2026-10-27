"""Preview, speaker test and stop on the announcement page at one viewport width."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

host = json.loads(Path(sys.argv[1]).read_text())
width = int(sys.argv[2])
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    context = browser.new_context(
        viewport={"width": width, "height": 852}, timezone_id="America/Los_Angeles"
    )
    context.add_cookies([{"name": "session", "value": host["cookie"], "url": host["origin"]}])
    context.route("https://**", lambda route: route.abort())
    page = context.new_page()
    page.goto(host["origin"] + "/settings/announcements")
    expect(page.get_by_role("heading", name="Announcements", exact=True)).to_be_visible()
    expect(page.locator("#announceStatusSynth")).to_contain_text("Available")
    expect(page.locator("#announceStatusMedia")).to_contain_text("speaker-media.test")

    # Validation feedback stays next to the field and keeps focus there.
    page.get_by_role("button", name="Preview in browser").click()
    expect(page.locator("#announceTextError")).to_have_text("Write the announcement text first.")
    expect(page.get_by_label("Reminder text")).to_be_focused()

    page.get_by_label("Reminder text").fill("Muista ottaa lääkkeet <b>nyt</b>.")
    expect(page.get_by_label("Language")).to_have_value("fi")
    page.get_by_role("button", name="Preview in browser").click()
    expect(page.locator("#announcePreviewState")).to_contain_text("Ready")
    audio = page.locator("#announcePreviewAudio")
    expect(audio).to_be_visible()
    assert audio.get_attribute("src").startswith("/api/announcements/previews/")
    assert context.request.get(host["origin"] + "/test/cast/plays").json() == []

    page.locator('input[name="announceTarget"][value="jbl-left"]').check()
    page.get_by_role("button", name="Test on speaker").click()
    expect(page.locator("#announceMessage")).to_contain_text("Speaker test requested on Jbl-Left")
    item = page.locator(".announce-test").first
    expect(item).to_contain_text("Sent")
    expect(item.locator(".announce-test-text")).to_have_text("Muista ottaa lääkkeet <b>nyt</b>.")
    assert context.request.get(host["origin"] + "/test/cast/plays").json() == [
        {"target": "jbl-left"}
    ]
    context.request.get(host["origin"] + "/test/cast/report?state=playing")
    expect(item).to_contain_text("Speaker reports playing")
    item.get_by_role("button", name="Stop speaker test on Jbl-Left").click()
    expect(item).to_contain_text("Stopped")
    expect(item).to_contain_text("Speaker stopped")
    expect(item.get_by_role("button", name="Stop speaker test on Jbl-Left")).to_have_count(0)

    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 0, f"horizontal scroll at {width}px: {overflow}"
    form = page.locator("#announceForm").bounding_box()
    tests = page.get_by_role("region", name="Speaker tests").bounding_box()
    if width >= 900:
        assert form["x"] + form["width"] <= tests["x"]
    else:
        assert form["y"] + form["height"] <= tests["y"]
    browser.close()
