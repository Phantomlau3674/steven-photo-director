#!/usr/bin/env python3
"""Rebuild contact sheets from a scanner manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scan_photos import build_contact_sheets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build duplicate and risk contact sheets from manifest.json.")
    parser.add_argument("manifest", help="Path to manifest.json from scan_photos.py.")
    parser.add_argument("--output", default=None, help="Output folder. Defaults to the manifest folder.")
    parser.add_argument("--max-contact-groups", type=int, default=200)
    parser.add_argument("--risk-sheet-size", type=int, default=24)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    output_dir = Path(args.output).expanduser().resolve() if args.output else manifest_path.parent
    written = build_contact_sheets(
        manifest["items"],
        output_dir,
        max_contact_groups=args.max_contact_groups,
        risk_sheet_size=args.risk_sheet_size,
    )
    print(json.dumps({"contact_sheets": written}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
