from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from paper_radar.config import ConfigError, load_feeds, load_topics
from paper_radar.models import RunSummary
from paper_radar.pipeline import update_database
from paper_radar.validation import ValidationError, publish_database, validate_database


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKING = PROJECT_ROOT / "data" / "papers.db"
DEFAULT_PUBLISHED = PROJECT_ROOT / "docs" / "data" / "papers.db"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper-radar")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("fetch", "validate", "publish", "update"):
        command = subparsers.add_parser(name)
        command.add_argument("--feeds", type=Path, default=PROJECT_ROOT / "feeds.yml")
        command.add_argument("--topics", type=Path, default=PROJECT_ROOT / "topics.yml")
        command.add_argument("--database", type=Path, default=DEFAULT_WORKING)
        command.add_argument("--published", type=Path, default=DEFAULT_PUBLISHED)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    error_context: dict[str, Any] = {"command": args.command}
    try:
        load_dotenv(PROJECT_ROOT / ".env")
        if args.command == "fetch":
            summary = _fetch(args)
            payload = {"command": "fetch", "result": asdict(summary)}
            if summary.status == "error":
                payload["publish_allowed"] = False
            _emit(payload)
            return 1 if summary.status == "error" else 0

        if args.command == "validate":
            report = validate_database(args.database, previous_path=args.published)
            _emit({"command": "validate", "result": asdict(report)})
            return 0

        if args.command == "publish":
            working_size = _database_size(args.database, label="working")
            report = publish_database(args.database, args.published)
            _emit(
                {
                    "command": "publish",
                    "result": asdict(report),
                    "validation": asdict(report),
                    "published": str(args.published),
                    "working_size_bytes": working_size,
                    "published_size_bytes": _database_size(args.published, label="published"),
                    "publish_allowed": True,
                }
            )
            return 0

        summary = _fetch(args)
        error_context["result"] = asdict(summary)
        if summary.status == "error":
            _emit(
                {
                    "command": "update",
                    "result": asdict(summary),
                    "publish_allowed": False,
                }
            )
            return 1
        validation_report = validate_database(args.database, previous_path=args.published)
        working_size = _database_size(args.database, label="working")
        published_report = publish_database(args.database, args.published)
        _emit(
            {
                "command": "update",
                "result": asdict(summary),
                "validation": asdict(validation_report),
                "publication": asdict(published_report),
                "published": str(args.published),
                "working_size_bytes": working_size,
                "published_size_bytes": _database_size(args.published, label="published"),
                "publish_allowed": True,
            }
        )
        return 0
    except (ConfigError, ValidationError) as exc:
        _emit_error(exc, context=error_context)
        return 1
    except Exception as exc:
        _emit_error(exc, context=error_context)
        return 1


def _fetch(args: argparse.Namespace) -> RunSummary:
    return update_database(
        args.database,
        load_feeds(args.feeds),
        load_topics(args.topics),
        unpaywall_email=os.getenv("UNPAYWALL_EMAIL") or None,
    )


def _emit(payload: dict[str, Any], *, stream: Any = None) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        file=sys.stdout if stream is None else stream,
    )


def _emit_error(error: Exception, *, context: dict[str, Any] | None = None) -> None:
    payload = dict(context or {})
    payload.update(
        {
            "publish_allowed": False,
            "error": {
                "type": type(error).__name__,
                "message": str(error),
            },
        }
    )
    _emit(payload, stream=sys.stderr)


def _database_size(path: Path, *, label: str) -> int:
    try:
        return path.stat().st_size
    except OSError as exc:
        raise ValidationError(f"could not inspect {label} database size {path}: {exc}") from exc
