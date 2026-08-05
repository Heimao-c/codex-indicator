import subprocess
import unittest
from unittest.mock import patch

from codex_indicator.models import SessionStatus
from codex_indicator.service import SessionView
from codex_indicator.terminal_window import (
    TerminalApprovalController,
    TerminalWindow,
    TerminalWindowResolver,
    _ancestor_chain,
    _nearest_ancestor_window,
    focus_macos_terminal,
    high_risk_approval_summary,
    is_approval_screen,
)


class StubWindowResolver(TerminalWindowResolver):
    def windows(self, force: bool = False) -> list[TerminalWindow]:
        return [
            TerminalWindow(1, "[ ! ] Action Required | CARI4D"),
            TerminalWindow(2, "CARI4D"),
            TerminalWindow(3, "⠋ phi"),
        ]


class TerminalWindowTests(unittest.TestCase):
    def test_selects_nearest_visible_windows_ancestor(self) -> None:
        ancestors = _ancestor_chain(41, {41: 30, 30: 20, 20: 0})
        self.assertEqual(ancestors, [41, 30, 20])
        self.assertEqual(_nearest_ancestor_window(ancestors, {20: [900], 30: [700]}), 700)

    def test_braille_window_title_is_working(self) -> None:
        self.assertTrue(TerminalWindow(1, "⠦ CARI4D").is_working)
        self.assertFalse(TerminalWindow(2, "CARI4D").is_working)

    def test_focuses_matching_macos_terminal_tty(self) -> None:
        responses = [
            subprocess.CompletedProcess([], 0, stdout="ttys004\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="focused\n", stderr=""),
        ]
        with patch("codex_indicator.terminal_window.sys.platform", "darwin"), patch(
            "codex_indicator.terminal_window.subprocess.run", side_effect=responses
        ) as run:
            focus_macos_terminal(123)
        self.assertEqual(run.call_args_list[1].args[0][-1], "/dev/ttys004")

    def test_matches_remote_attention_and_local_done_with_same_project(self) -> None:
        remote = SessionView(
            session_id="remote",
            thread_id="remote-thread",
            status=SessionStatus.WORKING,
            project="CARI4D",
            title="remote",
            cwd="/root/CARI4D",
            updated_at=2,
            source_host="robot-server",
        )
        local = SessionView(
            session_id="local",
            thread_id="local-thread",
            status=SessionStatus.DONE,
            project="CARI4D",
            title="local",
            cwd="/home/user/CARI4D",
            updated_at=1,
        )
        matched = StubWindowResolver().match([remote, local])
        self.assertEqual(matched["remote"].window_id, 1)
        self.assertTrue(matched["remote"].needs_attention)
        self.assertEqual(matched["local"].window_id, 2)

    def test_only_accepts_real_approval_panes(self) -> None:
        self.assertTrue(
            is_approval_screen(
                "Would you like to run the following command?\n"
                "> 1. Yes, proceed\n  2. No, and tell Codex what to do differently"
            )
        )
        self.assertTrue(
            is_approval_screen(
                "Would you like to grant these permissions?\n"
                "> Yes, grant these permissions for this turn"
            )
        )
        self.assertFalse(
            is_approval_screen(
                "Which camera should be used?\n> D435 (Recommended)\n  D455"
            )
        )

    def test_high_risk_detection_is_limited_to_current_approval_pane(self) -> None:
        destructive = (
            "Would you like to run the following command?\n"
            "$ sudo wipefs -a /dev/nvme0n1\n> 1. Yes, proceed"
        )
        self.assertIn("wipefs", high_risk_approval_summary(destructive) or "")
        self.assertIsNone(
            high_risk_approval_summary(
                "Earlier: sudo wipefs -a /dev/nvme0n1\n"
                "Would you like to run the following command?\n"
                "$ rm -rf build\n> 1. Yes, proceed"
            )
        )
        self.assertIsNotNone(
            high_risk_approval_summary(
                "Would you like to run the following command?\n"
                "$ git reset --hard\n> 1. Yes, proceed"
            )
        )

    def test_approve_all_defers_high_risk_but_approves_ordinary_requests(self) -> None:
        active = [99]
        activated: list[int] = []
        pressed: list[int] = []
        screens = {
            1: "Would you like to run the following command?\n$ npm test\n> Yes, proceed",
            2: "Would you like to run the following command?\n$ rm -rf /\n> Yes, proceed",
        }

        def activate(window_id: int) -> None:
            active[0] = window_id
            activated.append(window_id)

        controller = TerminalApprovalController(
            screen_reader=lambda window_id: screens[window_id],
            activate=activate,
            press_enter=lambda: pressed.append(active[0]),
            active_window=lambda: active[0],
            pause=lambda _seconds: None,
        )
        sessions = [
            SessionView(
                session_id=str(window_id),
                thread_id=str(window_id),
                status=SessionStatus.ATTENTION,
                project="project",
                title="approval",
                cwd="/workspace",
                updated_at=1,
                window_id=window_id,
            )
            for window_id in (1, 2)
        ]

        first = controller.approve_all(sessions)
        self.assertEqual(first.approved, 1)
        self.assertEqual([item.session.session_id for item in first.high_risk], ["2"])
        self.assertEqual(pressed, [1])

        second = controller.approve_all(
            [item.session for item in first.high_risk],
            allow_high_risk=True,
        )
        combined = first.merged(second)
        self.assertEqual(combined.approved, 2)
        self.assertEqual(combined.high_risk, ())
        self.assertEqual(pressed, [1, 2])

    def test_approve_all_targets_each_verified_attention_window_once(self) -> None:
        active = [99]
        activated: list[int] = []
        pressed: list[int] = []
        screens = {
            1: "Would you like to make the following edits?\n> Yes, proceed",
            2: "Which option do you prefer?\n> First option",
        }

        def activate(window_id: int) -> None:
            active[0] = window_id
            activated.append(window_id)

        controller = TerminalApprovalController(
            screen_reader=lambda window_id: screens[window_id],
            activate=activate,
            press_enter=lambda: pressed.append(active[0]),
            active_window=lambda: active[0],
            pause=lambda _seconds: None,
        )
        sessions = [
            SessionView(
                session_id="approval",
                thread_id="approval",
                status=SessionStatus.ATTENTION,
                project="one",
                title="one",
                cwd="/one",
                updated_at=2,
                window_id=1,
            ),
            SessionView(
                session_id="question",
                thread_id="question",
                status=SessionStatus.ATTENTION,
                project="two",
                title="two",
                cwd="/two",
                updated_at=1,
                window_id=2,
            ),
        ]

        result = controller.approve_all(sessions)

        self.assertEqual(result.approved, 1)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.errors, ())
        self.assertEqual(pressed, [1])
        self.assertEqual(activated, [1, 99])
        self.assertEqual(active[0], 99)
