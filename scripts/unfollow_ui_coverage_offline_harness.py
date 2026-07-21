#!/usr/bin/env python3
"""Run the mandatory Unfollow UI coverage replay without a device."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.unfollow_ui_coverage_offline_harness import run_offline_harness


if __name__ == "__main__":
    print(json.dumps(run_offline_harness(), indent=2, sort_keys=True))
