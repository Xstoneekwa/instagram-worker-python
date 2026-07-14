"""Tests for runtime UI state hard-stop publisher."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import runtime_ui_state


class RuntimeUiStateTests(unittest.TestCase):
    def test_publish_ui_state_unknown_calls_hard_stop(self) -> None:
        with patch("runtime_ui_state.execute_auto_restart_hard_stop") as hard_stop:
            hard_stop.return_value = {"executed": True, "incident": {"incident_id": "inc-1"}}
            out = runtime_ui_state.publish_ui_state_unknown_hard_stop(
                account_id="acct-1",
                observed_screen="mystery",
                evidence={"password": "secret"},
            )
        self.assertTrue(out["executed"])
        hard_stop.assert_called_once()
        kwargs = hard_stop.call_args.kwargs
        self.assertEqual(kwargs["evidence"]["signal"], "ui_state_unknown")
        self.assertEqual(kwargs["reason"], "ui_state_unknown_blocked")


if __name__ == "__main__":
    unittest.main()
