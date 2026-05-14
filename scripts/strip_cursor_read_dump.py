#!/usr/bin/env python3
"""Strip Cursor `read_file` output (` 12345|code`) into plain source lines."""
from __future__ import annotations

import re
import sys


def main() -> None:
    pat = re.compile(r"^\s*\d+\|(.*)$")
    for line in sys.stdin.read().splitlines():
        s = line.strip()
        if s.startswith("...") and "lines not shown" in s:
            continue
        m = pat.match(line)
        if m:
            print(m.group(1))
        else:
            print(line)


if __name__ == "__main__":
    main()
