#!/usr/bin/env python3
"""Status-only diagnostic gate; no cache installation or service action."""
import argparse
import json
from pathlib import Path
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from follow60_status_trust_cache_v1 import verify_status_trust

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--target-root', required=True, type=Path)
    args = parser.parse_args()
    result = verify_status_trust(args.target_root)
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result['ok'] else 2)
