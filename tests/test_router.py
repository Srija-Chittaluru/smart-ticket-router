import json

from app import router
from app.router import route_ticket


REQUIRED = ["category", "priority", "assigned_team", "reasoning"]


def test_empty_message_does_not_crash():
    result = route_ticket("")
    for field in REQUIRED:
        assert field in result
    assert result["clarification_needed"] is True


def test_mock_mode_short_message_flags_clarification(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "1")
    result = route_ticket("broken")
    for field in REQUIRED:
        assert field in result
    assert result["clarification_needed"] is True


def test_mock_mode_billing_keyword_routes_to_billing(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "1")
    result = route_ticket("I was charged twice for my subscription this month")
    assert result["category"] == "Billing & Payments"
    assert result["assigned_team"] == "Billing Team"


def test_malformed_json_falls_back_instead_of_crashing(monkeypatch):
    monkeypatch.delenv("MOCK_MODE", raising=False)
    monkeypatch.setattr(router, "_call_llm", lambda message, model: "not valid json {{{")
    monkeypatch.setattr(router.time, "sleep", lambda s: None)

    result = route_ticket("anything")

    for field in REQUIRED:
        assert field in result
    assert result["clarification_needed"] is True
    assert "manual triage" in result["reasoning"]


def test_missing_field_in_llm_response_falls_back(monkeypatch):
    monkeypatch.delenv("MOCK_MODE", raising=False)
    incomplete = json.dumps({"category": "Billing & Payments", "priority": "Low"})
    monkeypatch.setattr(router, "_call_llm", lambda message, model: incomplete)
    monkeypatch.setattr(router.time, "sleep", lambda s: None)

    result = route_ticket("anything")

    for field in REQUIRED:
        assert field in result


def test_valid_llm_response_passes_through(monkeypatch):
    monkeypatch.delenv("MOCK_MODE", raising=False)
    valid = json.dumps(
        {
            "category": "Backend Issue",
            "priority": "High",
            "assigned_team": "Backend Team",
            "reasoning": "Total outage affecting all users.",
            "clarification_needed": False,
        }
    )
    monkeypatch.setattr(router, "_call_llm", lambda message, model: valid)

    result = route_ticket("everything is down")

    assert result["category"] == "Backend Issue"
    assert result["priority"] == "High"
