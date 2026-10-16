import json

from app.services.sodexo import sodexo


def test_main_prints_payload_without_sending_or_starting_thread(monkeypatch, capsys):
    meals = [sodexo.Meal(type="FROM OUR FAVORITES", name="Lihapullat")]
    monkeypatch.setattr(sodexo, "_fetch_today_meals", lambda: meals)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("preview must not send a webhook or start the scheduler")

    monkeypatch.setattr(sodexo.requests, "post", fail_if_called)
    monkeypatch.setattr(sodexo, "start_sodexo_webhook_thread", fail_if_called)

    assert sodexo.main() == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["username"] == "Lounasbotti"
    assert "**Perussetti**" in payload["content"]
    assert "• Lihapullat" in payload["content"]


def test_main_returns_error_when_menu_fetch_fails(monkeypatch, capsys):
    def fail_fetch():
        raise RuntimeError("fetch failed")

    monkeypatch.setattr(sodexo, "_fetch_today_meals", fail_fetch)

    assert sodexo.main() == 1
    assert capsys.readouterr().out == ""
