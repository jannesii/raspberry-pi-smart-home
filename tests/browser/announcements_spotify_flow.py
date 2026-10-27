"""Phone/desktop optional Spotify controls; only simulated account endpoints."""

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

host = json.loads(Path(sys.argv[1]).read_text())
width = int(sys.argv[2])
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    context = browser.new_context(
        viewport={"width": width, "height": 852}, timezone_id="Asia/Tokyo"
    )
    context.add_cookies([{"name": "session", "value": host["cookie"], "url": host["origin"]}])
    context.route("https://**", lambda route: route.abort())
    origin = host["origin"]

    def authorize(route):
        state = parse_qs(urlsplit(route.request.url).query)["state"][0]
        route.fulfill(
            status=302,
            headers={
                "Location": origin
                + "/api/announcements/spotify/callback?code=test-code&state="
                + state
            },
            body="",
        )

    context.route("https://accounts.spotify.com/authorize**", authorize)
    page = context.new_page()
    page.goto(origin + "/settings/announcements")
    page.locator("#announceSpotifySettings summary").click()
    expect(page.locator("#announceSpotifyConnect")).to_be_disabled()
    expect(page.locator("#announceSpotifyState")).to_contain_text("configure Spotify access")
    context.request.get(origin + "/test/spotify")
    page.reload()
    page.locator("#announceSpotifySettings summary").click()
    page.get_by_role("button", name="Connect Spotify", exact=True).click()
    expect(page).to_have_url(origin + "/settings/announcements")
    page.locator("#announceSpotifySettings summary").click()
    expect(page.locator("#announceSpotifyState")).to_contain_text("Restoration is off")
    page.get_by_role("button", name="Find Spotify devices").click()
    page.get_by_label("Jbl-Right: Spotify device").select_option("spotify-right")
    page.get_by_label("Restore music interrupted by urgent reminders").check()
    page.get_by_role("button", name="Save music restoration").click()
    expect(page.locator("#announceSpotifyState")).to_contain_text("Restoration enabled")
    page.get_by_role("button", name="Disconnect Spotify", exact=True).click()
    expect(page.locator("#announceSpotifyState")).to_contain_text("No account connected")
    assert context.request.get(origin + "/test/cast/plays").json() == []
    assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 0
    browser.close()
