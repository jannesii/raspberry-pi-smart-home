"""Phone/desktop diagnostics use real queue/status endpoints, never live devices."""

import json
import re
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

if __name__ == "__main__":
    host = json.loads(Path(sys.argv[1]).read_text())
    width = int(sys.argv[2])
    origin = host["origin"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport={"width": width, "height": 852},
            timezone_id="America/Los_Angeles",
            reduced_motion="reduce",
        )
        context.add_cookies([{"name": "session", "value": host["cookie"], "url": origin}])
        page = context.new_page()
        page.clock.install()
        page.goto(origin + "/car_heater/settings#diagnostics")
        token = page.locator('meta[name="csrf-token"]').get_attribute("content")
        auth = {"X-CSRFToken": token}

        def frame(**spec):
            response = context.request.post(origin + "/test/frame", headers=auth, data=spec)
            assert response.status == 200, response.text()

        requests = []
        page.on("request", lambda req: requests.append(req) if req.url.endswith("/queue") else None)
        restart = page.get_by_role("button", name="Restart ESP", exact=True)
        restart.click()
        dialog = page.get_by_role("dialog", name="Restart ESP?")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_role("button", name="Cancel")).to_be_focused()
        page.keyboard.press("Tab")
        expect(dialog.get_by_role("button", name="Restart device")).to_be_focused()
        assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") != "none"
        focus = dialog.get_by_role("button", name="Restart device").bounding_box()
        assert focus["height"] >= 44 and focus["width"] >= 44
        assert focus["y"] >= 0 and focus["y"] + focus["height"] <= 852
        page.keyboard.press("Shift+Tab")
        expect(dialog.get_by_role("button", name="Cancel")).to_be_focused()
        page.keyboard.press("Escape")
        expect(dialog).not_to_be_visible()
        expect(restart).to_be_focused()
        assert not requests, "Cancelling a restart must not queue it"
        restart.click()
        dialog.get_by_role("button", name="Cancel").click()
        expect(restart).to_be_focused()
        restart.click()
        dialog.get_by_role("button", name="Restart device").click()
        expect(page.locator("#cmdStatus_esp_restart")).to_have_text("Queued · awaiting delivery")
        assert len(requests) == 1
        assert requests[0].post_data_json["action"] == "esp_restart"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        # Real command delivery/acknowledgement is visible through HTTP fallback.
        frame(relay=False)
        expect(page.locator("#cmdStatus_esp_restart")).to_have_text(
            "Sent · awaiting device acknowledgement", timeout=12000
        )
        frame(ack={"action": "esp_restart", "success": False})
        expect(page.locator("#cmdStatus_esp_restart")).to_have_text(
            "Device reported failure", timeout=12000
        )
        expect(page.locator("#diagnosticsRelay")).to_contain_text("Connected")
        shelly = page.get_by_role("button", name="Restart Shelly", exact=True)
        shelly.click()
        page.get_by_role("dialog", name="Restart Shelly?").get_by_role(
            "button", name="Restart device"
        ).click()
        expect(page.locator("#cmdStatus_shelly_restart")).to_have_text("Queued · awaiting delivery")
        frame(relay=False)
        frame(transport="ws", ack={"action": "shelly_restart", "success": True})
        expect(page.locator("#cmdStatus_shelly_restart")).to_have_text(
            "Device acknowledged success", timeout=12000
        )
        frame(connected=False)
        expect(page.locator("#diagnosticsRelay")).to_contain_text("Not connected", timeout=12000)

        # Logs retain copy/clear, keyboard close and request/error feedback.
        logs_button = page.get_by_role("button", name="Get Logs", exact=True)
        logs_button.click()
        logs = page.get_by_role("dialog", name="ESP32 Logs")
        expect(logs).to_be_visible()
        expect(page.locator("#carHeaterLogsState")).to_contain_text("Waiting for logs")
        frame(logs="diagnostic line one\nline two")
        expect(page.locator("#carHeaterLogsContent")).to_have_text(
            "diagnostic line one\nline two", timeout=12000
        )
        expect(page.locator("#carHeaterLogsReceived")).not_to_contain_text("Received: —")
        assert logs.evaluate("el => el.scrollWidth <= el.clientWidth")
        page.keyboard.press("Escape")
        expect(logs_button).to_be_focused()
        logs_button.click()
        frame()
        frame(ack={"action": "get_logs", "success": False})
        expect(page.locator("#carHeaterLogsState")).to_have_text("Get Logs failed", timeout=12000)
        logs.get_by_role("button", name="Clear", exact=True).click()
        expect(page.locator("#carHeaterLogsContent")).to_have_text("—")
        logs.get_by_role("button", name="Close logs", exact=True).click()

        logs_button.click()
        page.keyboard.press("Escape")
        page.clock.fast_forward(13000)
        expect(logs).not_to_be_visible()
        expect(logs_button).to_be_focused()

        # Detailed history is retrieved from real isolated persistence.
        page.get_by_role("button", name="View Charts", exact=True).click()
        charts = page.get_by_role("dialog", name="Detailed measurements")
        expect(charts).to_be_visible()
        date = charts.get_by_label("Measurement date (Helsinki)")
        date.fill("2026-10-21")
        charts.get_by_role("button", name="Load date", exact=True).click()
        expect(page.locator("#carHeaterChartState")).to_contain_text("Loaded 2026-10-21")
        expect(
            charts.get_by_role(
                "heading", name="Glass, cabin and device temperatures (°C)", exact=True
            )
        ).to_be_visible()
        expect(charts.get_by_role("heading", name="Cabin humidity (%)", exact=True)).to_be_visible()
        expect(charts.get_by_role("heading", name="Heater power (W)", exact=True)).to_be_visible()
        expect(page.locator("#carHeaterChartEnergyTotal")).to_have_text(
            "Recorded energy change: 0.0 Wh"
        )
        expect(charts).to_contain_text("last 3 °C at 00:40")
        expect(charts).to_contain_text("last 0 % at 00:40")
        expect(charts).to_contain_text("gap in readings")
        charts.get_by_role("button", name="Glass reference", exact=True).click()
        expect(charts.get_by_role("button", name="Glass reference", exact=True)).to_have_attribute(
            "aria-pressed", "false"
        )
        expect(charts).not_to_contain_text("Glass reference: 3 readings")
        charts.get_by_role("button", name="Glass reference", exact=True).click()
        charts.get_by_role("button", name="Previous day", exact=True).click()
        expect(date).to_have_value("2026-10-20")
        expect(charts).to_contain_text("Missing data is not zero")
        charts.get_by_role("button", name="Next day", exact=True).click()
        expect(page.locator("#carHeaterChartState")).to_contain_text("Loaded 2026-10-21")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert charts.evaluate("el => el.scrollWidth <= el.clientWidth")
        charts.evaluate("el => el.scrollTop = 0")
        page.screenshot(path=str(Path(sys.argv[1]).with_name(f"diagnostics-charts-{width}.png")))
        charts.get_by_role(
            "heading", name="Glass, cabin and device temperatures (°C)", exact=True
        ).scroll_into_view_if_needed()
        page.screenshot(path=str(Path(sys.argv[1]).with_name(f"diagnostics-plot-{width}.png")))
        page.keyboard.press("Escape")
        expect(charts).not_to_be_visible()
        expect(page.get_by_role("button", name="View Charts", exact=True)).to_be_focused()
        # HTTP errors clear stale chart content and offer an explicit retry.
        page.route(
            "**/api/car_heater/history?*",
            lambda route: route.fulfill(status=503, json={"error": "unavailable"}),
        )
        page.get_by_role("button", name="View Charts", exact=True).click()
        expect(page.locator("#carHeaterChartState")).to_contain_text("Measurements unavailable")
        expect(charts.get_by_role("img")).to_have_count(0)
        page.keyboard.press("Escape")
        page.unroute("**/api/car_heater/history?*")

        # A rejected queue request must not be labelled queued or executed.
        page.route(
            "**/api/car_heater/queue",
            lambda route: route.fulfill(status=503, json={"error": "unavailable"}),
        )
        restart.click()
        dialog.get_by_role("button", name="Restart device").click()
        expect(page.locator("#cmdStatus_esp_restart")).to_have_text("Request rejected · not queued")
        page.unroute("**/api/car_heater/queue")
        # Exercise the real Socket.IO client with a controlled wire peer.
        # Reconnection, partial events and missing acknowledgements stay honest.
        peers = []
        connected = []

        def wire(ws):
            peers.append(ws)
            ws.send(
                "0"
                + json.dumps(
                    {
                        "sid": "isolated",
                        "upgrades": [],
                        "pingInterval": 1000000,
                        "pingTimeout": 1000000,
                        "maxPayload": 1000000,
                    }
                )
            )

            def message(raw):
                if raw.startswith("40"):
                    ws.send("40" + json.dumps({"sid": "isolated-view"}))
                    connected.append(ws)
                elif raw.startswith("42"):
                    event, payload = json.loads(raw[2:])
                    if event == "car_heater_control":
                        response = context.request.post(
                            origin + "/api/car_heater/queue", headers=auth, data=payload
                        )
                        assert response.status == 200
                        ws.send(
                            "42"
                            + json.dumps(
                                [
                                    "car_heater_action_result",
                                    {
                                        "action": payload["action"],
                                        "stage": "queued",
                                        "ok": response.json()["ok"],
                                    },
                                ]
                            )
                        )

            ws.on_message(message)

        page.route_web_socket(re.compile(r".*/socket.io/.*"), wire)
        page.reload()
        expect(page.locator("#diagnosticsConnection")).to_contain_text(
            "Live connection", timeout=15000
        )
        restart.click()
        dialog.get_by_role("button", name="Restart device").click()
        expect(page.locator("#cmdStatus_esp_restart")).to_have_text("Queued · awaiting delivery")
        assert connected
        frame()
        payload = context.request.get(origin + "/api/car_heater/status").json()
        connected[-1].send("42" + json.dumps(["car_heater_status", payload]))
        expect(page.locator("#cmdStatus_esp_restart")).to_have_text(
            "Sent · awaiting device acknowledgement"
        )
        connected[-1].send(
            "42" + json.dumps(["car_heater_status", {"command_status": {"get_logs": "queued"}}])
        )
        expect(page.locator("#cmdStatus_esp_restart")).to_have_text(
            "Sent · awaiting device acknowledgement"
        )
        page.clock.fast_forward(31000)
        expect(page.locator("#cmdStatus_esp_restart")).to_have_text("Unknown · no acknowledgement")
        connected[-1].send(
            "42" + json.dumps(["car_heater_status", {"command_status": {"esp_restart": "sent"}}])
        )
        expect(page.locator("#cmdStatus_esp_restart")).to_have_text("Unknown · no acknowledgement")
        connected[-1].send(
            "42"
            + json.dumps(
                ["car_heater_action_result", {"action": "esp_restart", "stage": "executed"}]
            )
        )
        expect(page.locator("#cmdStatus_esp_restart")).to_have_text("Unknown · no acknowledgement")
        frame(ack={"action": "esp_restart", "success": True})
        connected[-1].send(
            "42"
            + json.dumps(
                [
                    "car_heater_action_result",
                    {"action": "esp_restart", "stage": "executed", "success": True},
                ]
            )
        )
        expect(page.locator("#cmdStatus_esp_restart")).to_have_text("Device acknowledged success")
        # The peer disconnects; the client must reconnect without losing recovery.
        connected[-1].close()
        page.clock.resume()
        expect(page.locator("#diagnosticsConnection")).to_contain_text(
            "Live connection", timeout=15000
        )
        assert len(peers) >= 2
        browser.close()
