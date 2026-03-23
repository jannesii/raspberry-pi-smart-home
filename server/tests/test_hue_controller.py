from __future__ import annotations

from app.services.hue.controller import HueController


class _DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_hue_controller_uses_timeout_for_get_requests(monkeypatch):
    calls: list[tuple[str, float]] = []

    def fake_get(url, timeout):
        calls.append((url, timeout))
        return _DummyResponse({"1": {"state": {"on": True}}})

    monkeypatch.setattr("app.services.hue.controller.requests.get", fake_get)
    ctrl = HueController("192.0.2.10", "user", request_timeout_s=7.5)

    lights = ctrl.get_lights()

    assert lights["1"]["state"]["on"] is True
    assert calls == [("http://192.0.2.10/api/user/lights", 7.5)]


def test_hue_controller_uses_timeout_for_put_requests(monkeypatch):
    calls: list[tuple[str, dict, float]] = []

    def fake_put(url, json, timeout):
        calls.append((url, json, timeout))
        return _DummyResponse([{"success": True}])

    monkeypatch.setattr("app.services.hue.controller.requests.put", fake_put)
    ctrl = HueController("192.0.2.10", "user", request_timeout_s=4.0)

    result = ctrl.set_light_state("5", {"bri": 200})

    assert result == [{"success": True}]
    assert calls == [("http://192.0.2.10/api/user/lights/5/state", {"bri": 200}, 4.0)]
