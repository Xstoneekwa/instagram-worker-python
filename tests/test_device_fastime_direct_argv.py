import unittest
from unittest.mock import patch

import device


class FastImeDirectArgvTest(unittest.TestCase):
    def test_fast_input_text_uses_direct_adb_shell_argv(self):
        for value in ("salut test", "hello world from DM446", "l\'ami \"test\"", "é漢字", ""):
            with self.subTest(value=value), patch.object(device, "_adb_run", return_value=(0, "ok", "")) as run:
                code, tail = device.fast_input_text("RFGL62SZ7NR", value)
            self.assertEqual(code, 0)
            self.assertEqual(tail, "ok")
            run.assert_called_once_with("RFGL62SZ7NR", ["shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", str(value or "")])
            argv = run.call_args.args[1]
            self.assertNotIn("sh", argv)
            self.assertNotIn("-c", argv)


if __name__ == "__main__":
    unittest.main()
