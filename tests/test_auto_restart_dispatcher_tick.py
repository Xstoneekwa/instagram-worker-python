import unittest

from auto_restart_dispatcher_tick import should_run_auto_restart_tick


class AutoRestartDispatcherTickTests(unittest.TestCase):
    def test_should_run_after_interval(self) -> None:
        self.assertFalse(
            should_run_auto_restart_tick(
                last_tick_monotonic=100.0,
                check_every_minutes=15,
                now_monotonic=100.0 + 60.0,
            )
        )
        self.assertTrue(
            should_run_auto_restart_tick(
                last_tick_monotonic=100.0,
                check_every_minutes=15,
                now_monotonic=100.0 + 900.0,
            )
        )


if __name__ == "__main__":
    unittest.main()
