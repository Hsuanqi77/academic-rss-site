from __future__ import annotations

import argparse
import sys
from pathlib import Path

from paper_radar.config import load_feeds, load_topic_catalog
from paper_radar.guide import render_file


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the configuration-backed Guide in docs/index.html."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift without modifying docs/index.html",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    feeds = load_feeds(root / "feeds.yml")
    catalog = load_topic_catalog(root / "topics.yml")
    index_path = root / "docs" / "index.html"
    in_sync = render_file(index_path, feeds=feeds, catalog=catalog, check=args.check)
    if args.check and not in_sync:
        print("docs/index.html Guide region is out of date", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
