import unittest

from hrs_runtime.source_contracts import usage_matches


class SourceContractTests(unittest.TestCase):
    def test_a_usage_reply_cannot_authorize_invalid_reference_bounds(self):
        reference = {"snapshot_id": "snapshot", "source_span_ref": "span", "start": -1, "end": 3}
        value = {"purpose": "card_input", "requested_fields": ["text"], "policy_version": "ingestion-usage-2",
                 "checked_at": "2026-09-11T00:00:00Z", "observed_change_cursor": "cursor",
                 "items": [{"reference": reference, "status": "allowed", "storage_availability": "verified",
                            "fields": [{"field": "text", "status": "allowed"}]}]}
        self.assertFalse(usage_matches(value, [reference], "card_input", ["text"]))
