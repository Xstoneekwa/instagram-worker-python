from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def command_branch(source: str, command: str) -> str:
    marker = f"\n  {command})"
    return source.split(marker, 1)[1].split("\n    ;;", 1)[0]


class PromotionKeepAliveQuiesceTests(unittest.TestCase):
    def test_all_three_wrappers_have_persistent_pause_and_launchd_bootout(self) -> None:
        wrappers = {
            "dispatcher": ROOT / "scripts/run_control_dispatcher_service.sh",
            "heartbeat": ROOT / "scripts/device_heartbeat_service.sh",
            "notifier": ROOT / "scripts/incident_notifier_service.sh",
        }
        for service, path in wrappers.items():
            source = path.read_text(encoding="utf-8")
            self.assertIn('PAUSE_FILE="$RUN_DIR/paused"', source, service)
            pause = command_branch(source, "pause")
            self.assertIn('touch "$PAUSE_FILE"', pause, service)
            self.assertIn('launchctl bootout "gui/$(id -u)"', pause, service)
            self.assertIn("_kill_all_", pause, service)
            self.assertLess(pause.index('touch "$PAUSE_FILE"'), pause.index("launchctl bootout"), service)

    def test_all_three_wrappers_resume_only_after_clearing_pause(self) -> None:
        names = (
            "run_control_dispatcher_service.sh",
            "device_heartbeat_service.sh",
            "incident_notifier_service.sh",
        )
        for name in names:
            source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            resume = command_branch(source, "resume")
            self.assertIn('rm -f "$PAUSE_FILE"', resume, name)
            self.assertIn("launchctl bootstrap", resume, name)
            self.assertIn("launchctl enable", resume, name)
            self.assertIn("launchctl kickstart", resume, name)

    def test_notifier_foreground_start_cannot_respawn_while_paused(self) -> None:
        source = (ROOT / "scripts/incident_notifier_service.sh").read_text(encoding="utf-8")
        foreground = source.split("_start_foreground() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('if [[ -f "$PAUSE_FILE" ]]', foreground)
        self.assertIn("incident_notifier_paused", foreground)

    def test_transaction_adapter_quiesces_and_starts_each_service_through_controller(self) -> None:
        source = (ROOT / "promotion_runtime_adapter_v1.py").read_text(encoding="utf-8")
        quiesce = source.split("    def quiesce(", 1)[1].split("\n    def ", 1)[0]
        start = source.split("    def start(", 1)[1].split("\n    def ", 1)[0]
        self.assertIn('service, "pause"', quiesce)
        self.assertIn('service, "resume"', start)
        controller = (ROOT / "phonefarm_runtime_control.py").read_text(encoding="utf-8")
        self.assertIn('command not in {"pause", "resume", "restart"}', controller)
        self.assertIn('"/bin/launchctl", "asuser"', controller)


if __name__ == "__main__":
    unittest.main()
