"""Integrated reminder journey; real page/API/storage, external doubles only.

Runs on phone and desktop in Los Angeles. Never connects to a real account,
webhook or speaker. Host test routes control only clock and external adapters.
"""

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from playwright.sync_api import expect, sync_playwright

host = json.loads(Path(sys.argv[1]).read_text())
origin = host["origin"]
width = int(sys.argv[2])
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    context = browser.new_context(
        viewport={"width": width, "height": 900}, timezone_id="America/Los_Angeles"
    )
    context.add_cookies([{"name": "session", "value": host["cookie"], "url": origin}])
    context.route("https://**", lambda route: route.abort())
    page = context.new_page()

    def control(path, **query):
        response = context.request.get(origin + "/test/" + path + "?" + urlencode(query))
        assert response.ok, response.text()
        return response.json()

    def advance(at):
        control("clock", at=at)
        page.reload()

    def row(text):
        return page.locator(".announce-occurrence", has_text=text).first

    def save(text, time, *, recurring=False, urgent=False, target="jbl-right"):
        page.get_by_label("Reminder text").fill(text)
        page.locator(f'input[name="announceTarget"][value="{target}"]').check()
        if recurring:
            page.get_by_label("Every day").check()
            page.get_by_label("Time 1", exact=True).fill(time)
            page.get_by_role("button", name="Add time").click()
            page.get_by_label("Time 2", exact=True).fill("14:03")
        else:
            page.get_by_label("Once", exact=True).check()
            page.get_by_label("Date (Helsinki)").fill("2027-01-15")
            page.get_by_label("Time (Helsinki)").fill(time)
        if page.locator("#announceAdvanced").get_attribute("open") is None:
            page.locator("#announceAdvanced summary").click()
        page.get_by_label("Urgent: may interrupt music").set_checked(urgent)
        if recurring:
            page.get_by_label("Announcements per reminder").fill("3")
            page.get_by_label("Minutes between them").fill("1")
        page.get_by_role("button", name="Save reminder", exact=True).click()
        expect(page.locator("#announceMessage")).to_contain_text("Reminder saved")

    page.goto(origin + "/settings/announcements")
    save("Integrated routine", "14:01", recurring=True)
    expect(page.locator(".announce-reminder").first).to_contain_text("14.01")
    advance("2027-01-15T12:01:00+00:00")
    control("cast/report", state="finished")
    page.reload()
    expect(row("Integrated routine")).to_contain_text("Speaker reported playback")
    advance("2027-01-15T12:02:00+00:00")
    assert len(control("cast/plays")) == 2  # bounded repeat, same occurrence
    row("Integrated routine").get_by_role(
        "button", name="Stop speaking: Integrated routine"
    ).click()
    page.reload()
    expect(row("Integrated routine")).to_contain_text("Not acknowledged")
    row("Integrated routine").get_by_label("Snooze minutes: Integrated routine").select_option("30")
    row("Integrated routine").get_by_role(
        "button", name="Snooze: Integrated routine", exact=True
    ).click()
    expect(row("Integrated routine")).to_contain_text("Snoozed until")
    advance("2027-01-15T12:03:00+00:00")
    old = page.locator(".announce-occurrence", has_text="Integrated routine").filter(
        has_text="Superseded"
    )
    expect(old).to_have_count(1)
    expect(old).to_contain_text("Not acknowledged")
    current = page.locator(".announce-occurrence", has_text="Integrated routine").filter(
        has_text="Speaking"
    )
    current.get_by_role("button", name="Stop speaking: Integrated routine").click()
    page.reload()
    current = (
        page.locator(".announce-occurrence", has_text="Integrated routine")
        .filter(has_text="Stopped")
        .first
    )
    occurrence_id = current.get_attribute("data-occurrence-id")
    current.get_by_role("button", name="Done: Integrated routine", exact=True).click()
    current = page.locator(f'[data-occurrence-id="{occurrence_id}"]')
    expect(current).to_contain_text("Done by owner")
    # A deliberate two-step deletion must also be operable with a keyboard.
    current.get_by_role("button", name="Delete history", exact=True).focus()
    page.keyboard.press("Enter")
    current.get_by_role("button", name="Confirm delete history", exact=True).click()
    expect(page.locator(f'[data-occurrence-id="{occurrence_id}"]')).to_have_count(0)
    expect(page.locator(".announce-reminder", has_text="Integrated routine")).to_have_count(1)

    # One urgent group stream, partial member evidence and uncertain Discord acceptance.
    control("group")
    page.reload()
    save("Day group", "14:04", urgent=True, target="jbl-group")
    control("cast/speaker", host="192.168.1.148", state="unavailable")
    control("discord", outcome="unknown")
    advance("2027-01-15T12:04:00+00:00")
    assert control("cast/plays")[-1] == {"target": "jbl-group"}
    control("cast/report", state="finished")
    page.reload()
    expect(row("Day group")).to_contain_text("Jbl-Left")
    expect(row("Day group")).to_contain_text("Unknown")
    expect(row("Day group")).to_contain_text("Not acknowledged")
    requests_after_unknown = control("discord")["requests"]
    page.reload()
    assert control("discord")["requests"] == requests_after_unknown

    # Urgency alone never bypasses quiet hours; expiry generates a missed fallback.
    save("Night group", "23:00", urgent=True, target="jbl-group")
    plays = len(control("cast/plays"))
    advance("2027-01-15T21:00:00+00:00")
    expect(row("Night group")).to_contain_text("quiet hours")
    assert len(control("cast/plays")) == plays
    advance("2027-01-15T21:16:00+00:00")
    expect(row("Night group")).to_contain_text("Accepted by Discord")
    expect(row("Night group")).to_contain_text("Not acknowledged")
    assert control("discord")["requests"] >= 1

    # Optional Spotify connection and a newer owner action during urgent audio.
    advance("2027-01-16T10:00:00+00:00")
    control("spotify")

    def authorize(route):
        state = parse_qs(urlsplit(route.request.url).query)["state"][0]
        route.fulfill(
            status=302,
            headers={
                "Location": origin + "/api/announcements/spotify/callback?code=test&state=" + state
            },
            body="",
        )

    context.route("https://accounts.spotify.com/authorize**", authorize)
    page.reload()
    page.locator("#announceSpotifySettings summary").click()
    page.get_by_role("button", name="Connect Spotify", exact=True).click()
    expect(page).to_have_url(origin + "/settings/announcements")
    page.locator("#announceSpotifySettings summary").click()
    page.get_by_role("button", name="Find Spotify devices").click()
    page.get_by_label("Jbl-Right: Spotify device").select_option("spotify-right")
    page.get_by_label("Restore music interrupted by urgent reminders").check()
    page.get_by_role("button", name="Save music restoration").click()
    # Save via the same form, now using tomorrow's Helsinki date.
    page.get_by_label("Reminder text").fill("Respect owner music")
    page.locator('input[name="announceTarget"][value="jbl-right"]').check()
    page.get_by_label("Date (Helsinki)").fill("2027-01-16")
    page.get_by_label("Time (Helsinki)").fill("12:01")
    page.locator("#announceAdvanced summary").click()
    page.get_by_label("Urgent: may interrupt music").check()
    page.get_by_role("button", name="Save reminder", exact=True).click()
    expect(page.locator("#announceMessage")).to_contain_text("Reminder saved")
    control("cast/speaker", host="192.168.1.119", state="playing")
    advance("2027-01-16T10:01:00+00:00")
    control("cast/report", state="playing")
    control("spotify/owner")
    control("cast/report", state="finished")
    page.reload()
    expect(row("Respect owner music")).to_contain_text("Left as changed by you")
    expect(row("Respect owner music")).to_contain_text("Not acknowledged")
    expect(row("Respect owner music")).to_contain_text("Interrupted by this announcement")
    expect(row("Respect owner music")).not_to_contain_text("restoration unavailable")
    assert control("spotify/effects")["resumes"] == 0
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    browser.close()
