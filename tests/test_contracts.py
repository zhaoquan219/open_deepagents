from __future__ import annotations

import unittest

from verification.contracts import (
    ContractValidationError,
    load_contract_definition,
    validate_event_sequence,
    validate_sse_event,
)


class ContractValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.definition = load_contract_definition()
        self.base_event = {
            "id": "1",
            "event_id": "1",
            "type": "message.final",
            "run_id": "run-123",
            "session_id": "session-123",
            "timestamp": "2026-04-12T14:00:00Z",
            "label": "assistant.message",
            "detail": "done",
            "data": {"message": {"role": "assistant", "content": "done"}},
        }

    def test_valid_message_event_passes(self) -> None:
        self.assertIn("message.final", self.definition.event_types)
        validate_sse_event(self.base_event)

    def test_message_delta_requires_text(self) -> None:
        event = dict(self.base_event, type="message.delta", data={})
        with self.assertRaises(ContractValidationError):
            validate_sse_event(event)

    def test_status_requires_status_value(self) -> None:
        event = dict(self.base_event, type="status", data={})
        with self.assertRaises(ContractValidationError):
            validate_sse_event(event)

    def test_rejects_unknown_event_type(self) -> None:
        event = dict(self.base_event, type="message.stream")
        with self.assertRaises(ContractValidationError):
            validate_sse_event(event)

    def test_enforces_monotonic_sequence_per_session(self) -> None:
        events = [
            dict(self.base_event, id="1", event_id="1"),
            dict(self.base_event, id="2", event_id="2", run_id="run-124"),
        ]
        validate_event_sequence(events)

        with self.assertRaises(ContractValidationError):
            validate_event_sequence([
                dict(self.base_event, id="2", event_id="2"),
                dict(self.base_event, id="2", event_id="2", run_id="run-124"),
            ])


if __name__ == "__main__":
    unittest.main()
