from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hrs_platform.jobs.pipeline import PipelineActivities


@pytest.mark.parametrize("enabled", [True, False])
def test_auto_card_switch_only_controls_automatic_creation(enabled):
    pipeline = PipelineActivities(SimpleNamespace(auto_cards_enabled=enabled), None)
    with patch("hrs_platform.jobs.pipeline.Cards") as cards:
        cards.return_value.create_run.return_value = {"run_id": "card-run"}
        result = pipeline.create_card_run("book-run")
        if enabled:
            cards.return_value.create_run.assert_called_once_with("book-run")
            assert result == {"run_id": "card-run"}
        else:
            cards.assert_not_called()
            assert result == {"run_id": "book-run", "skipped": True, "reason": "auto_cards_disabled"}
