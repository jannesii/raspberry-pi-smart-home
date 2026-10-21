"""Session weather evidence and exports in a real browser against isolated persistence."""

import csv
import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

host = json.loads(Path(sys.argv[1]).read_text())
width = int(sys.argv[2])
origin = host["origin"]
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    context = browser.new_context(viewport={"width": width, "height": 852})
    context.add_cookies([{"name": "session", "value": host["cookie"], "url": origin}])
    context.route("https://**", lambda route: route.abort())
    page = context.new_page()
    page.clock.set_fixed_time(datetime(2027, 1, 15, 3, 5, tzinfo=UTC))
    page.goto(origin + "/car_heater/sessions")
    token = page.locator('meta[name="csrf-token"]').get_attribute("content")
    seeded = context.request.post(origin + "/test/weather-session", headers={"X-CSRFToken": token})
    assert seeded.status == 200, seeded.text()
    sessions = seeded.json()
    mutations = []
    page.on(
        "request",
        lambda req: mutations.append((req.method, req.url))
        if req.method in ("POST", "PUT", "DELETE")
        else None,
    )

    # An expired sign-in fails closed with an explained retry, not partial evidence.
    weather_api = f"**/api/car_heater/sessions/{sessions['backfilled']}/weather"
    page.route(weather_api, lambda route: route.fulfill(status=401, json={"error": "login"}))
    page.goto(f"{origin}/car_heater/sessions/{sessions['backfilled']}")
    section = page.get_by_role("region", name="Weather evidence", exact=True)
    expect(section.locator("#sessionWeatherWindow")).to_have_text(
        "Weather evidence could not be loaded. Check your sign-in and retry."
    )
    expect(section.get_by_role("listitem")).to_have_count(0)
    page.unroute(weather_api)
    section.get_by_role("button", name="Retry weather evidence", exact=True).click()
    expect(section.get_by_role("button", name="Retry weather evidence")).to_be_hidden()
    expect(section.locator("#sessionWeatherWindow")).to_contain_text(
        "Weather archived 14/01, 23:30 \u2013 15/01, 01:15 (Helsinki)"
    )
    status = section.get_by_role("status")
    expect(status).to_contain_text(
        "Seinäjoki Pelmaa (101486): Collecting. 11 readings, no gaps in the window."
    )
    expect(status).to_contain_text("(137188): Last collection failed 15/01, 05:00: FMI timed out.")
    pelmaa = section.get_by_role("listitem").filter(has_text="101486").first
    expect(pelmaa).to_contain_text("11 · 0 corrected by FMI · 11 recovered later")
    airport = section.get_by_role("listitem").filter(has_text="137188").first
    expect(airport).to_contain_text(
        "14/01, 23:30 \u2013 15/01, 01:15: not collected yet; repair pending"
    )
    expect(airport).to_contain_text("Gaps up to 7 days old are repaired automatically.")
    # Forecast evidence before the archive's first check is honestly absent.
    point = section.get_by_role("listitem").filter(has_text="Parking-point forecast")
    expect(point.locator("dt", has_text="Held when heating began").locator("+ dd")).to_have_text(
        "No forecast had been received"
    )

    pelmaa.get_by_text("Original FMI replies (1)").click()
    reply = pelmaa.get_by_role("link", name="Retrieved 15/01, 05:00 · initialization")
    reply_href = reply.get_attribute("href")
    original = context.request.get(origin + reply_href)
    assert original.status == 200 and b"2027-01-14T21:30:00Z" in original.body()

    csv_link = section.get_by_role("link", name="Station observations (CSV)", exact=True)
    with page.expect_download() as download:
        csv_link.click()
    rows = list(csv.DictReader(io.StringIO(Path(download.value.path()).read_text())))
    assert {r["fmisid"] for r in rows} == {"101486"}
    assert {r["backfill"] for r in rows} == {"true"}
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    # A session during the first collection: the forecast held at its start is listed.
    page.goto(f"{origin}/car_heater/sessions/{sessions['current']}")
    section = page.get_by_role("region", name="Weather evidence", exact=True)
    point = section.get_by_role("listitem").filter(has_text="Parking-point forecast")
    expect(point.locator("dt", has_text="Held when heating began").locator("+ dd")).to_contain_text(
        "Model run 15/01, 02:00 · revision 1"
    )
    expect(section.locator("#sessionWeatherWindow")).to_contain_text(
        "Source times after 15/01, 05:00 are not due yet."
    )
    with page.expect_download() as download:
        section.get_by_role("link", name="Forecast values (JSON)", exact=True).click()
    exported = json.loads(Path(download.value.path()).read_text())
    temperatures = {r["valid_at"]: r for r in exported["rows"] if r["parameter"] == "Temperature"}
    assert temperatures["2027-01-15T04:00:00+00:00"]["value"] == -2.0
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    # Settings export: inline, announced validation; a valid range downloads.
    page.goto(origin + "/car_heater/settings")
    form = page.locator("#weatherExport")
    form.get_by_label("Evidence").select_option("forecast")
    form.get_by_label("From (Helsinki date)").fill("2027-01-10")
    form.get_by_label("To, inclusive").fill("2027-01-15")
    form.get_by_role("button", name="Download export").click()
    expect(form.get_by_role("alert")).to_have_text("Choose at most 2 days for this export.")
    form.get_by_label("From (Helsinki date)").fill("2027-01-14")
    with page.expect_download() as download:
        form.get_by_role("button", name="Download export").click()
    assert download.value.suggested_filename.endswith(".csv")
    expect(form.get_by_role("alert")).to_have_text("")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert mutations == [], mutations
    # Without the session cookie, evidence, exports and replies are refused.
    anonymous = browser.new_context()
    for path in (
        f"/api/car_heater/sessions/{sessions['current']}/weather",
        "/api/car_heater/weather/export?kind=observation&format=csv",
        reply_href,
    ):
        refused = anonymous.request.get(origin + path, max_redirects=0)
        assert refused.status in (401, 302), (path, refused.status)
        assert b"FeatureCollection" not in refused.body()
    browser.close()
