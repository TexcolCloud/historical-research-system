import json

import pytest
from agents import AgentOutputSchema
from pydantic import ValidationError

from hrs_platform.reading import coverage, scoped_check


def test_context_ids_are_excluded_from_both_model_schema_and_validated_receipts():
    model = scoped_check({"target-a", "target-b"})
    payload = {
        "checked_object_ids": ["target-a", "target-b"],
        "unverified_object_ids": [],
        "findings": [],
        "conclusion": "pass",
        "reasoning_summary": "The two targets were checked using separate context.",
    }
    value = model.model_validate(payload)
    coverage(value, {"target-a", "target-b"})
    assert value.model_dump(mode="json") == payload
    for field in ("checked_object_ids", "unverified_object_ids"):
        with pytest.raises(ValidationError):
            model.model_validate({**payload, field: ["context-only"]})
    schema = json.dumps(AgentOutputSchema(model).json_schema())
    assert '"enum": ["target-a", "target-b"]' in schema
    with pytest.raises(ValueError, match="Missing IDs"):
        coverage(
            model.model_validate({**payload, "checked_object_ids": ["target-a"]}), {"target-a", "target-b"}
        )
