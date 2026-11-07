"""Research runtime diagnostics exclude credentials and arbitrary caller fields."""

import re

PROVIDER_CODES = frozenset(
    {
        "429",
        "rate_limit",
        "capacity",
        "service_unavailable",
        "quota_exhausted",
        "authentication",
        "transport",
        "timeout",
    }
)


def provider_failure(value):
    """Local contracts and supervision are not evidence of a provider outage."""
    if not isinstance(value, dict) or value.get("origin") == "local":
        return False
    code = value.get("code")
    return code in PROVIDER_CODES and (
        code not in {"transport", "timeout"} or value.get("origin") == "provider"
    )


def redact_error(value):
    text = str(value)
    text = re.sub(r"(?i)((?:Bearer|Basic)\s+)[^\s,;\"']+", r"\1[redacted]", text)
    text = re.sub(
        r"(?i)((?:api[_-]?key|password|passwd|secret|access[_-]?token|refresh[_-]?token|token)[\"']?\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        r"\1[redacted]",
        text,
    )
    text = re.sub(r"([a-z][a-z0-9+.-]*://[^/\s:@]+:)[^@\s/]+@", r"\1[redacted]@", text, flags=re.I)
    return re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "[redacted]", text)


def failure_details(value):
    if not isinstance(value, dict):
        return None if value is None else {"message": redact_error(value)}
    value = dict(value)
    for key in ("details", "error"):
        if isinstance(value.get(key), dict):
            value = {**failure_details(value[key]), **value}
    if not value.get("reason") and isinstance(value.get("reason_code"), str):
        value["reason"] = value["reason_code"]
    if not value.get("message"):
        explanation = next(
            (
                value.get(key)
                for key in ("detail", "details", "error", "description")
                if isinstance(value.get(key), str)
            ),
            None,
        )
        if explanation:
            value["message"] = explanation
    allowed = {
        "type",
        "message",
        "code",
        "scope",
        "model",
        "retry_after",
        "event_id",
        "evidence",
        "reason",
        "exception_type",
        "terminal_evidence",
        "origin",
    }

    def clean(item):
        if isinstance(item, str):
            return redact_error(item)
        if isinstance(item, dict):
            return {
                key: clean(child)
                for key, child in item.items()
                if key in {"file", "line", "exception_type"}
            }
        return item if type(item) in {int, float, bool, type(None)} else None

    result = {key: clean(item) for key, item in value.items() if key in allowed}
    if value and not result:
        result["message"] = (
            "Technical diagnostics supplied unrecognized fields: " + ", ".join(sorted(value))[:300]
        )
    return result
