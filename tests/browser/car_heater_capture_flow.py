"""Observation capture with a fake camera, controlled clock and routed network.

Runs in a process without Eventlet patching. Chromium's fake media device stands
in for the phone camera; actual iPhone lens/wake-lock behaviour is commissioning.
"""

import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

MINUTE = 60_000
# The browser clock runs behind the server so advanced capture times stay in the past.
START = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)

ULTRA_WIDE = """
(() => {
  const mode = %s;
  const devices = navigator.mediaDevices;
  const enumerate = devices.enumerateDevices.bind(devices);
  const open = devices.getUserMedia.bind(devices);
  devices.enumerateDevices = async () => [
    ...(await enumerate()),
    {deviceId: 'ultra', groupId: 'rear', kind: 'videoinput', label: 'Back Ultra Wide Camera'},
  ];
  devices.getUserMedia = async constraints => {
    if (mode === 'denied') throw new DOMException('Permission denied', 'NotAllowedError');
    if (constraints.video?.deviceId?.exact === 'ultra') {
      if (mode === 'ultra-fails') throw new DOMException('Lens unavailable', 'OverconstrainedError');
      const {deviceId, ...rest} = constraints.video;
      return open({...constraints, video: rest});
    }
    return open(constraints);
  };
})();
"""


def api(context, origin, path, method="GET", token=None, body=None):
    response = context.request.fetch(
        origin + path,
        method=method,
        headers={"X-CSRFToken": token} if token else {},
        data=body,
    )
    assert response.ok, response.text()
    return response.json()


def new_session(context, page, origin):
    page.goto(origin + "/car_heater/sessions")
    token = page.locator('meta[name="csrf-token"]').get_attribute("content")
    row = api(
        context,
        origin,
        "/api/car_heater/sessions",
        "POST",
        token,
        {"trigger": "observation", "observed_at": START.isoformat()},
    )
    return row["id"], token


def hide(page, hidden):
    page.evaluate(
        """hidden => {
          Object.defineProperty(document, 'hidden', {configurable: true, get: () => hidden});
          Object.defineProperty(document, 'visibilityState', {configurable: true,
            get: () => hidden ? 'hidden' : 'visible'});
          document.dispatchEvent(new Event('visibilitychange'));
        }""",
        hidden,
    )


def observe_upload_retry_interrupt_finish(context, origin, viewport):
    page = context.new_page()
    page.set_viewport_size(viewport)
    session_id, token = new_session(context, page, origin)
    run = api(context, origin, "/api/car_heater/bounded", "POST", token, {"duration_minutes": 60})
    mutations = []
    page.on(
        "request",
        lambda r: mutations.append((r.method, r.url.split("?")[0]))
        if r.method not in ("GET", "HEAD")
        else None,
    )
    page.clock.install(time=START)
    page.goto(f"{origin}/car_heater/sessions/{session_id}/capture")
    start = page.get_by_role("button", name="Start observation", exact=True)
    expect(start).to_be_disabled()
    page.get_by_role("button", name="Enable camera", exact=True).click()
    expect(page.locator("#cameraStatus")).to_contain_text("Preview live")
    expect(page.locator("#cameraStatus")).to_contain_text("Ultra-wide is not exposed")
    expect(start).to_be_enabled()
    summary = page.locator("#captureSummary")

    # Start takes the initial photo immediately; the saved count is the server ack.
    start.click()
    expect(summary).to_have_text("1 saved")
    expect(page.locator("#captureCountdown")).to_have_text("10:00")
    page.get_by_role("button", name="Capture now", exact=True).click()
    expect(summary).to_have_text("2 saved")

    # Stop heating requests OFF without ending capture or inventing an outcome.
    page.get_by_role("button", name="Stop heating", exact=True).click()
    expect(page.locator("#captureHeating")).to_contain_text("Stop")
    expect(page.locator("#captureState")).to_have_text("OBSERVATION RUNNING")
    assert api(context, origin, "/api/car_heater/sessions/" + session_id)["outcome"] is None

    page.clock.run_for(10 * MINUTE)
    expect(summary).to_have_text("3 saved")

    # Hidden page: no capture, and no catch-up photos for the slots it missed.
    hide(page, True)
    page.clock.run_for(25 * MINUTE)
    hide(page, False)
    expect(page.get_by_role("alert")).to_contain_text("Page visible again")
    expect(summary).to_have_text("3 saved · 2 missed")
    expect(page.locator("#captureLog")).to_contain_text("missed (page hidden); no catch-up photo")

    # Failed upload stays visibly unsaved until a retry is acknowledged.
    page.route(
        "**/photos/**",
        lambda route: route.fulfill(status=503, json={"error": "Storage unavailable"})
        if route.request.method == "PUT"
        else route.continue_(),
    )
    page.get_by_role("button", name="Capture now", exact=True).click()
    expect(summary).to_have_text("3 saved · 1 not saved · 2 missed")
    expect(page.locator("#captureLog")).to_contain_text("Not saved: Storage unavailable")
    page.get_by_role("button", name="Finish observation", exact=True).click()
    dialog = page.get_by_role("dialog", name="Some photos are not saved")
    expect(dialog).to_be_visible()
    # Escape behaves like Keep observing: capture resumes instead of stalling.
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(page.locator("#captureCountdown")).not_to_have_text("Paused")
    expect(page.get_by_role("button", name="Finish observation", exact=True)).to_be_focused()
    page.get_by_role("button", name="Finish observation", exact=True).click()
    expect(dialog).to_be_visible()
    page.unroute("**/photos/**")
    page.keyboard.press("Enter")  # Retry uploads has focus and resumes observing.
    expect(dialog).to_be_hidden()
    expect(summary).to_have_text("4 saved · 2 missed")
    expect(page.locator("#captureState")).to_have_text("OBSERVATION RUNNING")

    detail = api(context, origin, "/api/car_heater/sessions/" + session_id)
    photos = detail["photos"]
    assert [p["capture_kind"] for p in photos] == ["scheduled", "manual", "scheduled", "manual"]
    assert [p["sequence"] for p in photos] == [0, None, 1, None]
    first, scheduled = photos[0], photos[2]
    started = datetime.fromisoformat(first["due_at"])
    assert START <= started < START + timedelta(minutes=2)
    assert first["observation_started_at"] == first["due_at"]
    assert datetime.fromisoformat(scheduled["due_at"]) == started + timedelta(minutes=10)
    assert datetime.fromisoformat(scheduled["captured_at"]) >= datetime.fromisoformat(
        scheduled["due_at"]
    )

    page.get_by_role("button", name="Finish observation", exact=True).click()
    page.wait_for_url("**/car_heater/sessions/" + session_id + "?observation=finished*")
    expect(
        page.get_by_text("Observation finished. Record the departure outcome", exact=False)
    ).to_be_visible()
    expect(page.get_by_role("heading", name="Departure outcome", exact=True)).to_be_focused()
    expect(page.locator(".evidence-photo")).to_have_count(4)
    windows = page.locator("#sessionObservations")
    expect(windows).to_contain_text("· 2 scheduled photos saved")
    expect(windows).to_contain_text("scheduled photo missed (page hidden)")
    expect(windows.locator("ul li")).to_have_count(2)
    first_image = page.locator(".evidence-photo img").first
    expect(first_image).to_have_attribute("alt", re.compile(r"^Windshield photo captured \d"))
    assert first_image.evaluate("img => img.complete && img.naturalWidth > 0")
    page.get_by_label("Clear", exact=True).check()
    page.get_by_role("button", name="Save outcome", exact=True).click()
    expect(page.get_by_text("Outcome saved. Heating unchanged.", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("link", name="Back to sessions", exact=True).click()
    expect(page.get_by_text("4 photos saved", exact=False)).to_be_visible()

    # Only photo uploads, the one Stop request and the outcome save mutated anything.
    heater = [m for m in mutations if "/bounded" in m[1] or "/departure" in m[1]]
    assert heater == [("DELETE", origin + "/api/car_heater/bounded")], heater
    assert {m[0] for m in mutations if "/photos/" in m[1]} == {"PUT"}
    assert ("PUT", f"{origin}/api/car_heater/sessions/{session_id}/outcome") in mutations
    [window] = api(context, origin, "/api/car_heater/sessions/" + session_id)["observations"]
    assert window["started_at"] == first["due_at"]
    assert window["finished_at"] is not None
    assert [(m["sequence"], m["reason"]) for m in window["missed"]] == [
        (2, "page_hidden"),
        (3, "page_hidden"),
    ]
    stopped = api(context, origin, "/api/car_heater/bounded")
    assert stopped["finish_at"] == run["finish_at"]
    page.screenshot(path=f"/tmp/car-heater-capture-review-{viewport['width']}.png", full_page=True)


def capture_page_layout(context, origin, viewport):
    page = context.new_page()
    page.set_viewport_size(viewport)
    session_id, _ = new_session(context, page, origin)
    page.goto(f"{origin}/car_heater/sessions/{session_id}/capture")
    page.get_by_role("button", name="Enable camera", exact=True).click()
    expect(page.locator("#cameraStatus")).to_contain_text("Preview live")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for name in ("Start observation", "Capture now", "Finish observation", "Stop heating"):
        box = page.get_by_role("button", name=name, exact=True).bounding_box()
        assert box["height"] >= 44 and box["width"] >= 44, (name, box)
    page.screenshot(path=f"/tmp/car-heater-capture-{viewport['width']}.png", full_page=True)
    page.close()


def camera_choice_permission_and_withdrawal(context, origin):
    page = context.new_page()
    session_id, _ = new_session(context, page, origin)
    capture = f"{origin}/car_heater/sessions/{session_id}/capture"

    denied = context.new_page()
    denied.add_init_script(ULTRA_WIDE % "'denied'")
    denied.goto(capture)
    denied.get_by_role("button", name="Enable camera", exact=True).click()
    expect(denied.locator("#cameraStatus")).to_contain_text("Camera permission denied")
    expect(denied.get_by_role("button", name="Start observation", exact=True)).to_be_disabled()
    expect(denied.get_by_role("button", name="Capture now", exact=True)).to_be_disabled()
    denied.close()

    fallback = context.new_page()
    fallback.add_init_script(ULTRA_WIDE % "'ultra-fails'")
    fallback.goto(capture)
    fallback.get_by_role("button", name="Enable camera", exact=True).click()
    expect(fallback.locator("#cameraStatus")).to_contain_text(
        "The ultra-wide camera could not start; using the regular rear camera."
    )
    expect(fallback.get_by_role("button", name="Start observation", exact=True)).to_be_enabled()
    fallback.close()

    ultra = context.new_page()
    ultra.add_init_script(ULTRA_WIDE % "'ultra-works'")
    ultra.clock.install(time=START)
    ultra.goto(capture)
    # Keyboard-only operation reaches Enable camera and Start.
    ultra.get_by_role("button", name="Enable camera", exact=True).focus()
    ultra.keyboard.press("Enter")
    expect(ultra.locator("#cameraStatus")).to_contain_text("Using the ultra-wide camera.")
    expect(ultra.locator("#cameraSelect option")).to_contain_text(["Back Ultra Wide Camera"])
    ultra.get_by_role("button", name="Start observation", exact=True).focus()
    ultra.keyboard.press("Enter")
    expect(ultra.locator("#captureSummary")).to_have_text("1 saved")
    # Permission withdrawn / camera ended: capture pauses and records the miss.
    ultra.evaluate(
        """() => document.getElementById('capturePreview').srcObject.getVideoTracks()
             .forEach(track => { track.stop(); track.dispatchEvent(new Event('ended')); })"""
    )
    expect(ultra.locator("#captureState")).to_have_text("OBSERVATION PAUSED")
    expect(ultra.get_by_role("alert")).to_contain_text("Camera stopped")
    ultra.clock.run_for(13 * MINUTE)
    expect(ultra.locator("#captureSummary")).to_have_text("1 saved · 1 missed")
    expect(ultra.locator("#captureLog")).to_contain_text("missed (camera unavailable)")
    saved = api(context, origin, "/api/car_heater/sessions/" + session_id)["photos"]
    assert [p["camera_label"] for p in saved] == [saved[0]["camera_label"]]
    assert len(saved) == 1
    ultra.close()


def abandon_unsaved_photo_and_failed_record(context, origin):
    page = context.new_page()
    session_id, _ = new_session(context, page, origin)
    page.clock.install(time=START)
    page.goto(f"{origin}/car_heater/sessions/{session_id}/capture")
    page.get_by_role("button", name="Enable camera", exact=True).click()
    expect(page.locator("#cameraStatus")).to_contain_text("Preview live")
    page.route(
        "**/photos/**",
        lambda route: route.fulfill(status=503, json={"error": "Storage unavailable"}),
    )
    page.get_by_role("button", name="Start observation", exact=True).click()
    summary = page.locator("#captureSummary")
    expect(summary).to_have_text("0 saved · 1 not saved")

    # A record save that gets no acknowledgement keeps the page and says so.
    page.route(
        "**/observations/**",
        lambda route: route.fulfill(status=503, json={"error": "Unavailable"}),
    )
    page.get_by_role("button", name="Finish observation", exact=True).click()
    page.get_by_role("dialog", name="Some photos are not saved").get_by_role(
        "button", name="Finish without them"
    ).click()
    record_dialog = page.get_by_role("dialog", name="Observation record not saved")
    expect(record_dialog).to_be_visible()
    record_dialog.get_by_role("button", name="Keep observing").click()
    expect(page.get_by_role("alert")).to_contain_text("new observation window")
    page.unroute("**/observations/**")
    expect(summary).to_contain_text("observation record not saved")
    page.get_by_role("button", name="Retry failed uploads", exact=True).click()
    expect(summary).to_have_text("0 saved · 2 not saved · 1 missed")

    page.get_by_role("button", name="Finish observation", exact=True).click()
    page.get_by_role("dialog", name="Some photos are not saved").get_by_role(
        "button", name="Finish without them"
    ).click()
    page.wait_for_url("**?observation=finished*")
    windows = page.locator("#sessionObservations")
    expect(windows).to_contain_text("scheduled photo missed (upload failed)")
    detail = api(context, origin, "/api/car_heater/sessions/" + session_id)
    assert detail["photos"] == []
    reasons = [m["reason"] for o in detail["observations"] for m in o["missed"]]
    assert "upload_failed" in reasons, detail["observations"]
    assert any(o["finished_at"] for o in detail["observations"])


if __name__ == "__main__":
    host = json.loads(Path(sys.argv[1]).read_text())
    width = int(sys.argv[2])
    with sync_playwright() as p:
        browser = p.chromium.launch(
            timeout=15000,
            args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
        )
        context = browser.new_context(timezone_id="America/New_York", reduced_motion="reduce")
        context.grant_permissions(["camera"], origin=host["origin"])
        context.add_cookies([{"name": "session", "value": host["cookie"], "url": host["origin"]}])
        context.route("https://**", lambda route: route.abort())
        origin = host["origin"]
        if width:
            viewport = {"width": width, "height": 852 if width < 600 else 1000}
            capture_page_layout(context, origin, viewport)
            observe_upload_retry_interrupt_finish(context, origin, viewport)
        else:
            camera_choice_permission_and_withdrawal(context, origin)
            abandon_unsaved_photo_and_failed_record(context, origin)
        browser.close()
