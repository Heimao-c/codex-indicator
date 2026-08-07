import unittest

from cc_indicator.models import SessionStatus, status_for_event


class EventStatusTests(unittest.TestCase):
    def test_core_lifecycle(self) -> None:
        cases = {
            "SessionStart": SessionStatus.DONE,
            "UserPromptSubmit": SessionStatus.WORKING,
            "PermissionRequest": SessionStatus.ATTENTION,
            "PostToolUse": SessionStatus.WORKING,
            "Stop": SessionStatus.DONE,
            "SessionEnd": SessionStatus.CLOSED,
        }
        for event, expected in cases.items():
            with self.subTest(event=event):
                self.assertEqual(status_for_event(event, {}), expected)

    def test_request_user_input_needs_attention(self) -> None:
        self.assertEqual(
            status_for_event("PreToolUse", {"tool_name": "request_user_input"}),
            SessionStatus.ATTENTION,
        )

    def test_compaction_session_start_is_working(self) -> None:
        self.assertEqual(status_for_event("SessionStart", {"source": "compact"}), SessionStatus.WORKING)

    def test_claude_notification_permission_prompt_needs_attention(self) -> None:
        self.assertEqual(
            status_for_event("Notification", {"notification_type": "permission_prompt"}),
            SessionStatus.ATTENTION,
        )

    def test_claude_notification_other_types_are_done(self) -> None:
        for payload in (
            {"notification_type": "idle_prompt"},
            {"notification_type": "agent_completed"},
            {"notification_type": "auth_success"},
            {},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(status_for_event("Notification", payload), SessionStatus.DONE)


if __name__ == "__main__":
    unittest.main()
