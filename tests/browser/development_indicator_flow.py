"""Trusted HTTPS development works at phone and desktop widths."""

import os
import shutil
import sys

from playwright.sync_api import expect, sync_playwright

if __name__ == "__main__":
    origin, username, password = sys.argv[1:4]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=os.getenv("DEV_TEST_CHROMIUM") or shutil.which("chromium"),
            args=["--use-fake-device-for-media-stream"],
        )
        context = browser.new_context(viewport={"width": 393, "height": 852})
        context.grant_permissions(["camera"], origin=origin)
        page = context.new_page()
        page.goto(origin + "/login")
        banner = page.locator(".env-banner")
        expect(banner).to_be_visible()
        expect(banner).to_contain_text("Kehitysympäristö")
        expect(banner).to_contain_text("feat/sample")
        page.fill("#username", username)
        page.fill("#password", password)
        page.check("#remember")
        page.get_by_role("button", name="Kirjaudu").click()
        page.wait_for_url(lambda url: "/login" not in url)
        cookies = {cookie["name"]: cookie for cookie in context.cookies(origin)}
        assert {"jannenkoti_dev_session", "jannenkoti_dev_remember"} <= cookies.keys()
        for cookie in cookies.values():
            assert cookie["secure"] and cookie["httpOnly"]

        page.goto(origin + "/car_heater")
        assert page.url.endswith("/car_heater")
        page.goto(origin + "/car_heater/sessions")
        session_id = page.evaluate("""async () => {
            const token = document.querySelector('meta[name=csrf-token]').content;
            const response = await fetch('/api/car_heater/sessions', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': token},
                body: JSON.stringify({trigger: 'observation', observed_at: new Date().toISOString()})
            });
            if (!response.ok) throw new Error(await response.text());
            return (await response.json()).id;
        }""")
        page.goto(f"{origin}/car_heater/sessions/{session_id}/capture")
        assert page.evaluate("window.isSecureContext")
        page.get_by_role("button", name="Enable camera", exact=True).click()
        expect(page.locator("#cameraStatus")).to_contain_text("Preview live")

        def socket_origin_status(value):
            return context.request.get(
                origin + "/socket.io/?EIO=4&transport=polling", headers={"Origin": value}
            ).status

        assert socket_origin_status(origin) == 200
        assert socket_origin_status("https://dev.jannenkoti.com") == 400

        for width in (393, 1440):
            page.set_viewport_size({"width": width, "height": 852})
            page.goto(origin + "/")
            expect(banner).to_be_visible()
            box = banner.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1, box
            assert banner.evaluate("el => el.scrollWidth <= el.clientWidth")
            toggle = page.locator("#menuToggle")
            if toggle.is_visible():
                toggle.click()
            page.locator("nav.navbar").get_by_role("link", name="Asetukset").click()
            page.wait_for_url(lambda url: url.endswith("/settings"))
            expect(banner).to_be_visible()
            # The indicator stays visible while scrolling the page.
            page.mouse.wheel(0, 2000)
            box = banner.bounding_box()
            assert box and box["y"] <= 1, box
        browser.close()
