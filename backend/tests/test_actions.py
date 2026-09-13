from unittest.mock import Mock

import actions


def test_alert_webhook_respects_rule_cooldown(monkeypatch):
    sender = Mock()
    monkeypatch.setattr(actions.requests, "post", sender)
    monkeypatch.setattr(actions.settings, "RECOGNITION_WEBHOOK_URL", "http://alerts.test/hook")
    monkeypatch.setattr(actions.settings, "AZURE_TIMEOUT", 1.0)
    rule = {"id": "test-cooldown", "name": "Entrée", "enabled": True, "notify": True, "cooldown_seconds": 60}

    assert actions.trigger_alert(rule, {"track_id": "face-1"}) == "Alerte webhook envoyée"
    assert actions.trigger_alert(rule, {"track_id": "face-1"}) == "Alerte ignorée (cooldown)"
    sender.assert_called_once()
