"""One authenticated journey through the complete car-heater interface.

Heating, Sessions, Settings, session review, capture and outcome entry on the
real pages, with the real planner and persistence in an isolated app. Time,
weather, device frames (real HTTP/WebSocket ingest), camera and photo network
failures are controlled. Each view gets the same accessibility audit.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

LOCK_REASON = "Stop heating and wait for confirmed OFF before starting another mode."

# Visible-surface checks shared by every view; issues are returned as text.
AUDIT = """
() => {
  const issues = [];
  const root = document.querySelector('.car-heater-page, .heater-evidence');
  const shown = el => {
    if (el.closest('[hidden]') || el.closest('details:not([open]) > :not(summary)')) return false;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const describe = el => `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''} "${(el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 40)}"`;
  if (document.documentElement.scrollWidth > innerWidth) issues.push(`horizontal overflow ${document.documentElement.scrollWidth} > ${innerWidth}`);

  // Touch targets: 44 px for controls; inline text links and radio/checkbox
  // inputs are judged by their surrounding label row.
  root.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea, summary').forEach(el => {
    if (!shown(el)) return;
    const inlineLink = el.tagName === 'A' && el.closest('p, li, dd, figcaption, td');
    const target = ['checkbox', 'radio'].includes(el.type) ? el.closest('label') || el : el;
    const r = target.getBoundingClientRect();
    if (!inlineLink && (r.height < 44 || r.width < 44)) issues.push(`small target ${describe(el)} ${Math.round(r.width)}x${Math.round(r.height)}`);
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName) && !el.labels?.length && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby')) issues.push(`unlabelled ${describe(el)}`);
    const name = el.innerText || el.getAttribute('aria-label') || [...el.querySelectorAll('img[alt]')].map(img => img.alt).join(' ');
    if (['BUTTON', 'A', 'SUMMARY'].includes(el.tagName) && !name.trim()) issues.push(`unnamed ${describe(el)}`);
  });

  // Text contrast against the composited background (WCAG AA).
  const rgba = value => { const n = (value.match(/[\\d.]+/g) || []).map(Number); return n.length >= 3 ? [n[0], n[1], n[2], n.length > 3 ? n[3] : 1] : null; };
  const over = (top, bottom) => [0, 1, 2].map(i => top[i] * top[3] + bottom[i] * (1 - top[3])).concat(1);
  const background = el => {
    const layers = [];
    for (let node = el; node; node = node.parentElement) {
      const bg = rgba(getComputedStyle(node).backgroundColor);
      if (bg && bg[3] > 0) { layers.push(bg); if (bg[3] >= 1) break; }
    }
    let color = [255, 255, 255, 1];
    layers.reverse().forEach(layer => { color = over(layer, color); });
    return color;
  };
  const luminance = c => { const [r, g, b] = c.slice(0, 3).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  while (walker.nextNode()) {
    const el = walker.currentNode.parentElement;
    if (!walker.currentNode.textContent.trim() || seen.has(el) || !shown(el)) continue;
    seen.add(el);
    if (el.closest(':disabled, [aria-disabled="true"], option, svg, .review-chart, canvas')) continue;
    const cs = getComputedStyle(el);
    const bg = background(el);
    const fg = over(rgba(cs.color), bg);
    const [hi, lo] = [luminance(fg), luminance(bg)].sort((a, b) => b - a);
    const ratio = (hi + 0.05) / (lo + 0.05);
    const size = parseFloat(cs.fontSize);
    const large = size >= 24 || (size >= 18.66 && Number(cs.fontWeight) >= 700);
    if (ratio < (large ? 3 : 4.5)) issues.push(`contrast ${ratio.toFixed(2)} ${describe(el)}`);
  }

  // Reduced motion: nothing in the page transitions or animates.
  root.querySelectorAll('*').forEach(el => {
    const cs = getComputedStyle(el);
    const moving = cs.transitionDuration.split(',').some(d => parseFloat(d) > 0) || cs.animationName !== 'none';
    if (moving && shown(el)) issues.push(`motion ${describe(el)}`);
  });
  return issues;
}
"""


def audit(page, name, width, shots):
    page.evaluate("window.scrollTo(0, 0)")
    issues = page.evaluate(AUDIT)
    assert issues == [], f"{name}: {issues}"
    # Keyboard focus stays visible and unobscured on the first stops in the view.
    page.evaluate("document.activeElement?.blur()")
    for _ in range(10):
        page.keyboard.press("Tab")
        focus = page.evaluate(
            """() => {
              const el = document.activeElement;
              // The shared app navbar is outside the car-heater redesign.
              if (!el || !el.closest('.car-heater-page, .heater-evidence')) return null;
              el.scrollIntoView({block: 'center'});
              const cs = getComputedStyle(el);
              const r = el.getBoundingClientRect();
              return {outline: cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) >= 2,
                      inView: r.top >= 0 && r.bottom <= innerHeight, id: el.id || el.tagName};
            }"""
        )
        if focus:
            assert focus["outline"] and focus["inView"], f"{name}: focus {focus}"
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(shots / f"car-heater-{name}-{width}.png"), full_page=True)


if __name__ == "__main__":
    host = json.loads(Path(sys.argv[1]).read_text())
    width = int(sys.argv[2])
    shots = Path(os.environ.get("CAR_HEATER_SCREENSHOTS") or tempfile.gettempdir())
    shots.mkdir(parents=True, exist_ok=True)
    origin = host["origin"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"]
        )
        context = browser.new_context(
            viewport={"width": width, "height": 852 if width < 600 else 1000},
            timezone_id="America/Los_Angeles",
            reduced_motion="reduce",
        )
        context.grant_permissions(["camera"], origin=origin)
        context.add_cookies([{"name": "session", "value": host["cookie"], "url": origin}])
        context.route("https://**", lambda route: route.abort())
        page = context.new_page()
        dialogs = []

        def answer(dialog):
            # Only the Ready-by cancel confirmation is expected; accept it.
            dialogs.append(dialog.message)
            if dialog.message.startswith("Cancel the Ready-by schedule?"):
                dialog.accept()
            else:
                dialog.dismiss()

        page.on("dialog", answer)
        mutations = []
        page.on(
            "request",
            lambda r: mutations.append((r.method, r.url.split("?")[0].replace(origin, "")))
            if r.method not in ("GET", "HEAD")
            else None,
        )
        page.goto(origin + "/car_heater")
        token = page.locator('meta[name="csrf-token"]').get_attribute("content")
        auth = {"X-CSRFToken": token}
        nav = page.get_by_role("navigation", name="Car heater", exact=True)

        def frame(**spec):
            response = context.request.post(origin + "/test/frame", headers=auth, data=spec)
            assert response.status == 200, response.text()

        def api(path):
            return context.request.get(origin + path).json()

        def view(name):
            nav.get_by_role("link", name=name, exact=True).click()
            expect(page.get_by_role("heading", level=1)).to_have_text(name)

        # 1. Heating: the saved Ready-by (cabin target) schedule reloads, edits
        # only on Save, and cancels, all without operating the heater.
        expect(page.get_by_role("heading", level=1)).to_have_text("Heating")
        ready_by = page.locator("#readyBySection")
        expect(ready_by.locator("summary")).to_contain_text("Scheduled")
        ready_by.locator("summary").click()
        expect(page.locator("#readyByState")).to_contain_text("cabin -8.0 °C")
        ready_target = page.get_by_label("Ready-by cabin target (°C)", exact=True)
        expect(ready_target).to_have_value("-8")
        saved_at = api("/api/car_heater/ready_by/schedule")["schedule"]["ready_by_ts"]
        ready_target.fill("-6")
        page.reload()
        assert api("/api/car_heater/ready_by/schedule")["schedule"]["target_temp_c"] == -8
        ready_by.locator("summary").click()
        ready_target.fill("-6")
        page.get_by_role("button", name="Save Ready-by", exact=True).click()
        expect(page.locator("#readyByFeedback")).to_contain_text("Saved. Cabin -6.0 °C")
        page.reload()
        ready_by.locator("summary").click()
        expect(page.locator("#readyByState")).to_contain_text("cabin -6.0 °C")
        replaced = api("/api/car_heater/ready_by/schedule")["schedule"]
        assert (replaced["ready_by_ts"], replaced["status"]) == (saved_at, "scheduled")
        page.get_by_role("button", name="Cancel Ready-by", exact=True).click()
        assert dialogs[-1].startswith("Cancel the Ready-by schedule?")
        expect(page.locator("#readyByFeedback")).to_have_text(
            "Ready-by schedule cancelled. Current heating is unchanged."
        )
        expect(page.get_by_role("button", name="Cancel Ready-by", exact=True)).to_be_disabled()
        assert api("/api/car_heater/ready_by/schedule")["schedule"]["status"] == "canceled"
        assert api("/api/car_heater/bounded") is None
        page.reload()
        expect(ready_by.locator("summary")).to_contain_text("Off")

        # 2. Settings: a comfort preference saves explicitly and survives reload.
        view("Settings")
        hysteresis = page.get_by_label("Comfort hysteresis (°C)", exact=True)
        hysteresis.fill("1")
        page.get_by_role("button", name="Save preferences", exact=True).click()
        expect(page.locator("#preferencesFeedback")).to_have_text("Saved. Hysteresis is 1.0 °C.")
        page.reload()
        expect(hysteresis).to_have_value("1")
        expect(page.get_by_label("Allow Ready-by schedules")).to_be_visible()

        # 3. Heating: Tomorrow's departure is previewed, saved and reloaded.
        view("Heating")
        page.get_by_role("button", name="Tomorrow", exact=True).click()
        page.get_by_label("Departure time", exact=True).fill("08:00")
        page.get_by_role("button", name="Preview departure", exact=True).click()
        expect(page.locator("#departurePreview")).to_contain_text("150 min preheat")
        assert api("/api/car_heater/departure") is None
        page.get_by_role("button", name="Save departure", exact=True).click()
        expect(page.locator("#departureSaved")).to_contain_text("Scheduled")
        plan = api("/api/car_heater/departure")
        page.reload()
        expect(page.locator("#departureBigTime")).to_have_text("08:00")
        expect(page.locator("#departureSaved")).to_contain_text("05:30")

        # 4. Comfort runs on its own outside a timed session.
        frame(relay=False, power=0)
        expect(page.locator("#heaterState")).to_contain_text("Heater OFF", timeout=12000)
        page.locator("#comfortSection > summary").click()
        page.get_by_label("Target cabin temperature (°C)", exact=True).fill("-5")
        page.get_by_role("button", name="Start comfort heating", exact=True).click()
        expect(page.locator("#comfortState")).to_contain_text("Comfort heating on")

        # 5. The saved departure starts: comfort is suspended, competing starts
        # say why, and the service refuses requests that bypass the page.
        started = context.request.post(origin + "/test/departure-start", headers=auth)
        assert started.status == 200, started.text()
        page.reload()
        expect(page.locator("#heaterState")).to_contain_text("Start requested")
        expect(page.locator("#boundedMode")).to_have_text("Departure")
        assert api("/api/car_heater/comfort")["enabled"] is False
        expect(page.get_by_role("button", name="Start timed heating")).to_be_disabled()
        expect(page.locator("#modeLockReason")).to_have_text(LOCK_REASON)
        page.locator("#comfortSection > summary").click()
        expect(page.get_by_role("button", name="Start comfort heating")).to_be_disabled()
        expect(page.locator("#comfortLockReason")).to_have_text(LOCK_REASON)
        page.locator("#readyBySection > summary").click()
        expect(page.get_by_role("button", name="Save Ready-by", exact=True)).to_be_disabled()
        expect(page.locator("#readyByLockReason")).to_have_text(LOCK_REASON)
        manual = context.request.post(
            origin + "/api/car_heater/bounded", headers=auth, data={"duration_minutes": 180}
        )
        comfort = context.request.patch(
            origin + "/api/car_heater/comfort", headers=auth, data={"enabled": True}
        )
        later = context.request.post(
            origin + "/api/car_heater/ready_by/schedule",
            headers=auth,
            data={"ready_by_ts": saved_at[:16].replace(" ", "T"), "target_temp_c": -5},
        )
        assert (manual.status, comfort.status, later.status) == (409, 409, 409), (
            manual.text(),
            comfort.text(),
            later.text(),
        )
        run = api("/api/car_heater/bounded")
        assert run["session_id"] == plan["session_id"]
        frame(transport="ws", relay=True, power=1500, session="current", phase="heating")
        expect(page.locator("#heaterState")).to_contain_text("confirmed ON", timeout=12000)
        audit(page, "heating-active", width, shots)

        # 6. Capture from the heating session; a failed upload stays unsaved
        # until retried, and Finish leads to the outcome without heating changes.
        page.get_by_role("link", name="Observe windshield", exact=True).click()
        expect(page.get_by_role("heading", level=1)).to_have_text("Watch the windshield")
        page.get_by_role("button", name="Enable camera", exact=True).click()
        expect(page.locator("#cameraStatus")).to_contain_text("Preview live")
        page.get_by_role("button", name="Start observation", exact=True).click()
        summary = page.locator("#captureSummary")
        expect(summary).to_have_text("1 saved")
        page.route(
            "**/photos/**",
            lambda route: route.fulfill(status=503, json={"error": "Storage unavailable"})
            if route.request.method == "PUT"
            else route.continue_(),
        )
        page.get_by_role("button", name="Capture now", exact=True).click()
        expect(summary).to_have_text("1 saved · 1 not saved")
        finish = page.get_by_role("button", name="Finish observation", exact=True)
        finish.click()
        unsaved = page.get_by_role("dialog", name="Some photos are not saved")
        expect(unsaved).to_be_visible()
        page.keyboard.press("Escape")
        expect(unsaved).to_be_hidden()
        expect(finish).to_be_focused()
        audit(page, "capture", width, shots)
        page.unroute("**/photos/**")
        finish.click()
        unsaved.get_by_role("button", name="Retry uploads", exact=True).click()
        expect(summary).to_have_text("2 saved")
        finish.click()
        page.wait_for_url(f"**/car_heater/sessions/{run['session_id']}?observation=finished*")
        expect(page.get_by_role("heading", name="Departure outcome", exact=True)).to_be_focused()
        expect(page.locator(".evidence-photo")).to_have_count(2)
        page.get_by_label("Wipers enough", exact=True).check()
        page.get_by_role("button", name="Save outcome", exact=True).click()
        expect(page.get_by_text("Outcome saved. Heating unchanged.", exact=True)).to_be_visible()
        expect(page.get_by_role("region", name="Weather evidence", exact=True)).to_be_visible()
        assert api("/api/car_heater/bounded")["phase"] == "heating"
        assert not [m for m in mutations if m[1].startswith("/api/car_heater/bounded")]
        audit(page, "review", width, shots)

        # 7. Sessions lists the attempt newest first with its evidence.
        view("Sessions")
        first = page.locator("#sessionList .evidence-attempt").first
        expect(first).to_contain_text("Wipers enough")
        expect(first).to_contain_text("2 photos saved")
        audit(page, "sessions", width, shots)

        # 8. Stop: pending, unknown while disconnected, confirmed only by the relay.
        view("Heating")
        state = page.locator("#heaterState")
        expect(state).to_contain_text("confirmed ON", timeout=12000)
        stop = page.locator("#btnTurnOff")
        dialogs_before_stop = len(dialogs)
        stop.click()
        expect(state).to_contain_text("Stopping")
        assert len(dialogs) == dialogs_before_stop, "Stop must not ask for confirmation"
        frame(connected=False)
        expect(state).to_contain_text("relay state unknown", timeout=12000)
        expect(stop).to_have_text("Request OFF again")
        frame(transport="ws", relay=False, power=0, session="current", phase="stopped")
        expect(state).to_contain_text("OFF confirmed", timeout=12000)
        # Shutdown never resumes comfort; it is available again by choice.
        assert api("/api/car_heater/comfort")["enabled"] is False
        page.locator("#comfortSection > summary").click()
        expect(page.get_by_role("button", name="Start comfort heating")).to_be_enabled()
        assert api(f"/api/car_heater/sessions/{run['session_id']}")["outcome"]["result"] == (
            "wipers_enough"
        )
        audit(page, "heating-stopped", width, shots)

        # 9. Settings: a restart needs confirmation, and only a device
        # acknowledgement reports its result. Saved preferences persisted.
        view("Settings")
        expect(hysteresis).to_have_value("1")
        restart = page.get_by_role("button", name="Restart ESP", exact=True)
        restart.click()
        confirm = page.get_by_role("dialog", name="Restart ESP?")
        expect(confirm.get_by_role("button", name="Cancel")).to_be_focused()
        page.keyboard.press("Escape")
        expect(restart).to_be_focused()
        assert not [m for m in mutations if m[1] == "/api/car_heater/queue"]
        restart.click()
        confirm.get_by_role("button", name="Restart device").click()
        expect(page.locator("#cmdStatus_esp_restart")).to_have_text("Queued · awaiting delivery")
        frame(relay=False, power=0)
        frame(relay=False, power=0, ack={"action": "esp_restart", "success": True})
        expect(page.locator("#cmdStatus_esp_restart")).to_have_text(
            "Device acknowledged success", timeout=12000
        )
        expect(page.locator("#maintenanceStatus")).to_have_text("Device acknowledged success")
        expect(page.locator("#detailPower")).to_have_text("0.0 W", timeout=12000)
        audit(page, "settings", width, shots)
        browser.close()
