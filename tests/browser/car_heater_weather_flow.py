"""Collection health in Settings → Diagnostics with a real collector and archive."""

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
    expect(status).to_contain_text(
        "Seinäjoki Pelmaa (101486): Collecting. History from 14/01, 05:00."
    )
    expect(status).to_contain_text(
        "(137188): Last collection failed: FMI timed out. Initializing history from 14/01, 05:00."
    )
    expect(status).not_to_contain_text("not running")
    pelmaa = section.get_by_role("listitem").filter(has_text="101486")
    # A station's missing cloud cover is a source limitation, shown apart from failures.
    expect(pelmaa).to_contain_text("Received without values")
    expect(pelmaa).to_contain_text("Cloud cover")
    expect(pelmaa).to_contain_text("Temperature")
    expect(pelmaa).to_contain_text("Visibility")
    # Source times FMI answered without values are a gap, not a recovery.
    expect(pelmaa.locator("dt", has_text="Gaps since").locator("+ dd")).to_have_text(
        "1 without source values"
    )
    expect(pelmaa.locator("dt", has_text="Latest gap").locator("+ dd")).to_have_text(
        "14/01, 05:00 \u2013 15/01, 04:50 \u00b7 FMI returned no values"
    )
    link = pelmaa.get_by_role("link", name="Download original reply", exact=True)
    reply = context.request.get(host["origin"] + link.get_attribute("href"))
    assert reply.status == 200
    assert b"FeatureCollection" in reply.body()
    expect(status).to_contain_text("Parking-point forecast (62.8000 N, 22.8260 E): Collecting.")
    point = section.get_by_role("listitem").filter(has_text="Parking-point forecast")
    # Model run and received hours are shown in Helsinki time.
    expect(point).to_contain_text("15/01, 02:00 · revision 1")
    expect(point).to_contain_text("15/01, 02:00 \u2013 15/01, 04:00")
    expect(point.locator("dt", has_text="Received without values").locator("+ dd")).to_have_text(
        "Long-wave radiation"
    )
    expect(point.locator("dt", has_text="Not returned").locator("+ dd")).to_have_text("Gusts")
    # Before its first check the archive holds nothing: an explicit unchecked stretch.
    expect(point.locator("dt", has_text="Unchecked stretches (24 h)").locator("+ dd")).to_have_text(
        "1 \u00b7 latest 14/01, 05:00 \u2013 15/01, 05:00"
    )
    forecast_link = point.get_by_role("link", name="Download original reply", exact=True)
    forecast_reply = context.request.get(host["origin"] + forecast_link.get_attribute("href"))
    assert forecast_reply.status == 200
    assert b"harmonie_scandinavia_surface" in forecast_reply.body()
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
    expect(status).to_contain_text(
        "Seinäjoki airport (137188): Collecting. History from 14/01, 05:00."
    )
    airport = section.get_by_role("listitem").filter(has_text="137188")
    expect(airport).to_contain_text("FMI timed out")
    # The weather readout does not appear on the everyday Heating view.
    page.goto(host["origin"] + "/car_heater")
    expect(page.get_by_role("region", name="Weather collection", exact=True)).to_be_hidden()
    browser.close()
