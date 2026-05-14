#!/usr/bin/env python3
"""Emit synthetic Cursor read_file-style windows from plain tail_only.py lines."""
from __future__ import annotations

import argparse
from pathlib import Path


def emit_window(*, first_file_line: int, lines: list[str]) -> str:
    out: list[str] = []
    out.append(f"... {first_file_line - 1} lines not shown ...")
    for i, content in enumerate(lines):
        lineno = first_file_line + i
        out.append(f"{lineno:>6}|{content}")
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("plain_tail", type=Path, help="Plain tail_only.py (no lineno prefixes)")
    ap.add_argument("--first-line", type=int, default=15663, help="1-based line of first row in plain_tail")
    ap.add_argument("--window-lines", type=int, default=500)
    ap.add_argument("--out-dir", type=Path, default=Path("."))
    args = ap.parse_args()

    plain = args.plain_tail.read_text(encoding="utf-8").splitlines()
    n = len(plain)
    wlines = int(args.window_lines)
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    first = int(args.first_line)
    pos = 0
    w = 1
    while pos < n:
        chunk = plain[pos : pos + wlines]
        if not chunk:
            break
        first_line = first + pos
        text = emit_window(first_file_line=first_line, lines=chunk)
        (out_dir / f"tail_raw_{w:02d}.txt").write_text(text, encoding="utf-8")
        pos += len(chunk)
        w += 1


if __name__ == "__main__":
    main()
