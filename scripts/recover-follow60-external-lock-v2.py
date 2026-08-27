#!/usr/bin/env python3
"""Explicit stale-owner recovery only; does not acquire a replacement or promote."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from follow60_external_deployment_lock_v2 import recover_stale

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--target-root', required=True)
    parser.add_argument('--expected-lock-sha256', required=True)
    args = parser.parse_args()
    archived = recover_stale(Path(args.target_root), args.expected_lock_sha256)
    print(json.dumps({'status': 'STALE_LOCK_ARCHIVED', 'path': str(archived), 'promotion': False}))
