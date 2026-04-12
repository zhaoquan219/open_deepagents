from __future__ import annotations

import unittest

from verification.contracts import ContractValidationError, load_contract_definition, validate_event_sequence, validate_sse_event


class ContractValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.definition = load_contract_definition()
        self.base_event = {
            "event_version": self.definition.version,
            "session_id": "session-123",
            "run_id": "run-123",
            "message_id": "message-123",
            "step_id": None,
            "sequence": 0,
            "event_type": "message.completed",
            "status": "completed",
            "ts": "2026-04-12T14:00:00Z",
            "payload": {"content": "done"},
        }

    def test_valid_message_event_passes(self) -> None:
        validate_sse_event(self.base_event)

    def test_message_event_requires_message_id(self) -> None:
        event = dict(self.base_event, message_id=None)
        with self.assertRaises(ContractValidationError):
            validate_sse_event(event)

    def test_step_scoped_event_requires_step_id(self) -> None:
        event = dict(
            self.base_event,
            message_id=None,
            step_id=None,
            event_type="tool.started",
            status="started",
        )
        with self.assertRaises(ContractValidationError):
            validate_sse_event(event)

    def test_rejects_unknown_event_type(self) -> None:
        event = dict(self.base_event, event_type="message.stream")
        with self.assertRaises(ContractValidationError):
            validate_sse_event(event)

    def test_enforces_monotonic_sequence_per_run(self) -> None:
        events = [
            dict(self.base_event, sequence=1),
            dict(self.base_event, sequence=2, message_id="message-124"),
        ]
        validate_event_sequence(events)

        with self.assertRaises(ContractValidationError):
            validate_event_sequence([
                dict(self.base_event, sequence=2),
                dict(self.base_event, sequence=2, message_id="message-124"),
            ])


if __name__ == "__main__":
    unittest.main()
