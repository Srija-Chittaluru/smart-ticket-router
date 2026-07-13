from __future__ import annotations

import json
import os
import time

from jsonschema import validate as jsonschema_validate
from jsonschema.exceptions import ValidationError

from app.schema import REQUIRED_FIELDS, SYSTEM_PROMPT, TICKET_JSON_SCHEMA

FALLBACK_RESPONSE = {
    "category": "General Inquiry",
    "priority": "Medium",
    "assigned_team": "Tier 1 Support",
    "reasoning": (
        "Automatic classification failed after repeated malformed/invalid "
        "responses; routed to Tier 1 for manual triage rather than left "
        "unrouted."
    ),
    "clarification_needed": True,
}

MAX_ATTEMPTS = 3


class RouterError(Exception):
    pass


def _get_client():
    from openai import OpenAI

    return OpenAI()


def _mock_classify(message: str) -> dict:
    """Deterministic keyword-based stand-in for the LLM, used only when
    MOCK_MODE=1 (offline dev/testing, no API key required)."""
    text = message.lower().strip()

    if len(text) < 12:
        return {
            "category": "Technical Issue",
            "priority": "Medium",
            "assigned_team": "Tier 1 Support",
            "reasoning": "Message is too short to determine severity; flagged for clarification.",
            "clarification_needed": True,
        }
    if any(w in text for w in ["charge", "invoice", "refund", "billed", "payment", "subscription"]):
        return {
            "category": "Billing & Payments",
            "priority": "Medium",
            "assigned_team": "Billing Team",
            "reasoning": "Message references a billing/payment problem.",
            "clarification_needed": False,
        }
    if any(w in text for w in ["log in", "login", "locked out", "password", "access"]):
        return {
            "category": "Account Access",
            "priority": "High",
            "assigned_team": "Account & Security Team",
            "reasoning": "Message describes an inability to access the account.",
            "clarification_needed": False,
        }
    if any(w in text for w in ["feature", "would be nice", "please add", "request"]):
        return {
            "category": "Feature Request",
            "priority": "Low",
            "assigned_team": "Product Team",
            "reasoning": "Message asks for new/changed functionality, nothing is broken.",
            "clarification_needed": False,
        }
    if any(w in text for w in ["down", "not working", "nothing works", "crash", "error", "broken"]):
        return {
            "category": "Technical Issue",
            "priority": "High",
            "assigned_team": "Tier 2 Engineering",
            "reasoning": "Message describes the product being non-functional.",
            "clarification_needed": False,
        }
    return {
        "category": "General Inquiry",
        "priority": "Low",
        "assigned_team": "Customer Success",
        "reasoning": "No specific problem or defect described; treated as a general question.",
        "clarification_needed": False,
    }


def _call_llm(message: str, model: str) -> str:
    client = _get_client()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ],
        response_format={"type": "json_schema", "json_schema": TICKET_JSON_SCHEMA},
        temperature=0,
    )
    return response.choices[0].message.content


def _validate(parsed: dict) -> None:
    jsonschema_validate(instance=parsed, schema=TICKET_JSON_SCHEMA["schema"])
    missing = [f for f in REQUIRED_FIELDS if f not in parsed or not str(parsed[f]).strip()]
    if missing:
        raise ValidationError(f"missing required fields: {missing}")


def route_ticket(message: str, model: str | None = None) -> dict:
    """Classify a single raw support message into structured routing fields.

    Returns a dict with category, priority, assigned_team, reasoning,
    clarification_needed, and is_fallback (True when classification could
    not be completed and FALLBACK_RESPONSE was returned instead). Never
    raises for a well-formed `message` string -- on repeated LLM/parse
    failures it returns FALLBACK_RESPONSE instead of crashing the caller.
    """
    if not isinstance(message, str) or not message.strip():
        result = dict(FALLBACK_RESPONSE)
        result["reasoning"] = "Empty message received; routed to Tier 1 for manual triage."
        result["is_fallback"] = True
        return result

    if os.getenv("MOCK_MODE") == "1":
        result = _mock_classify(message)
        result["is_fallback"] = False
        return result

    model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = _call_llm(message, model)
            parsed = json.loads(raw)
            _validate(parsed)
            parsed["is_fallback"] = False
            return parsed
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc
            time.sleep(0.5 * attempt)
        except Exception as exc:  # openai API/network errors
            last_error = exc
            time.sleep(0.5 * attempt)

    fallback = dict(FALLBACK_RESPONSE)
    fallback["reasoning"] = (
        f"Automatic classification failed after {MAX_ATTEMPTS} attempts "
        f"({type(last_error).__name__}); routed to Tier 1 for manual triage."
    )
    fallback["is_fallback"] = True
    return fallback
