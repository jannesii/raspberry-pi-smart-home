"""Speaker group on the announcement page: check, select, partial evidence."""

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
    origin = host["origin"]
    page.goto(origin + "/settings/announcements")

    # Unconfigured group: visible, explained, not selectable.
    group = page.locator('input[name="announceTarget"][value="jbl-group"]')
    expect(group).to_be_disabled()
    expect(page.locator("#announceTargets")).to_contain_text("Not configured")

    # Read-only check finds the existing group and suggests its configuration.
    context.request.get(origin + "/test/cast/speaker?host=192.168.1.148&state=unavailable")
    page.get_by_role("button", name="Check speakers and find Cast group").click()
    expect(page.locator("#announceDiscoveryState")).to_contain_text("Found 2 Cast receivers")
    expect(page.locator("#announceDiscoveryList")).to_contain_text("speaker group")
    page.locator("#announceDiscoveryConfig summary").click()
    expect(page.locator("#announceDiscoveryJson")).to_contain_text('"device_id": "uuid-group"')
    assert context.request.get(origin + "/test/cast/plays").json() == []

    # Once configured, the group is selectable and plays as one stream.
    context.request.get(origin + "/test/group")
    page.reload()
    group = page.locator('input[name="announceTarget"][value="jbl-group"]')
    expect(group).to_be_enabled()
    group.check()
    page.get_by_label("Reminder text").fill("Muista ottaa lääkkeet.")
    page.get_by_role("button", name="Test on speaker").click()
    item = page.locator(".announce-test").first
    expect(item).to_contain_text("Group playback report only")
    expect(item).to_contain_text("Jbl-Left: not responding")
    assert context.request.get(origin + "/test/cast/plays").json() == [{"target": "jbl-group"}]
    context.request.get(origin + "/test/cast/report?state=finished")
    expect(item).to_contain_text("Speaker reported playback finished")

    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 0, f"horizontal scroll at {width}px: {overflow}"
    browser.close()
