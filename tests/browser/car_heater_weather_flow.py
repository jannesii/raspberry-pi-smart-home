"""Collection health in Settings & diagnostics with a real collector and archive."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

host = json.loads(Path(sys.argv[1]).read_text())
width = int(sys.argv[2])
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    context = browser.new_context(viewport={"width": width, "height": 852})
    context.add_cookies([{"name": "session", "value": host["cookie"], "url": host["origin"]}])
    context.route("https://**", lambda route: route.abort())
    page = context.new_page()
    page.clock.set_fixed_time(datetime(2027, 1, 15, 3, 5, tzinfo=UTC))
    page.goto(host["origin"] + "/car_heater/settings")
    section = page.get_by_role("region", name="Weather collection", exact=True)
    expect(section).to_be_visible()
    status = section.get_by_role("status")
    expect(status).to_contain_text("Seinäjoki Pelmaa (101486): Collecting.")
    expect(status).to_contain_text("(137188): Last collection failed: FMI timed out.")
    expect(status).not_to_contain_text("not running")
    pelmaa = section.get_by_role("listitem").filter(has_text="101486")
    # A station's missing cloud cover is a source limitation, shown apart from failures.
    expect(pelmaa).to_contain_text("Received without values")
    expect(pelmaa).to_contain_text("Cloud cover")
    expect(pelmaa).to_contain_text("Temperature")
    expect(pelmaa).to_contain_text("Visibility")
    link = pelmaa.get_by_role("link", name="Download original reply", exact=True)
    reply = context.request.get(host["origin"] + link.get_attribute("href"))
    assert reply.status == 200
    assert b"FeatureCollection" in reply.body()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    assert (
        context.request.post(
            host["origin"] + "/test/weather-recover",
            headers={
                "X-CSRFToken": page.locator('meta[name="csrf-token"]').get_attribute("content")
            },
        ).status
        == 200
    )
    page.get_by_role("button", name="Refresh collection status", exact=True).click()
    expect(status).to_contain_text("Seinäjoki airport (137188): Collecting.")
    airport = section.get_by_role("listitem").filter(has_text="137188")
    expect(airport).to_contain_text("FMI timed out")
    # The weather readout does not appear on the everyday Heating view.
    page.goto(host["origin"] + "/car_heater")
    expect(page.get_by_role("region", name="Weather collection", exact=True)).to_be_hidden()
    browser.close()
