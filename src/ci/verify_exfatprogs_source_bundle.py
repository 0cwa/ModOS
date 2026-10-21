#!/usr/bin/env python3
"""CLI wrapper for the shared strict exfat corresponding-source validator."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from exfatprogs_source_bundle import validate_bundle_file


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("sha256")
    args = parser.parse_args()
    validate_bundle_file(args.bundle, args.sha256)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
