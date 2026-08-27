#!/usr/bin/env python3
"""Explicit privileged attestation publisher; no activation or runtime mutation."""
import argparse
import json
from pathlib import Path
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from follow60_status_trust_cache_v1 import install_status_attestation

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--target-root', required=True, type=Path)
    args = parser.parse_args()
    result = install_status_attestation(args.target_root)
    print(json.dumps(result, sort_keys=True))
