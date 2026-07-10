import json
from pathlib import Path

import pytest

import paper_radar.cli as cli
from paper_radar.models import RunSummary
from paper_radar.validation import ValidationError, ValidationReport


def _report() -> ValidationReport:
    return ValidationReport(
        article_count=3,
        journal_count=2,
        earliest_date="2026-07-01T00:00:00Z",
        latest_date="2026-07-03T00:00:00Z",
        schema_version=3,
    )


def test_cli_requires_a_subcommand() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_default_paths_are_anchored_to_project_not_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    args = cli.build_parser().parse_args(["update"])

    assert args.feeds == cli.PROJECT_ROOT / "feeds.yml"
    assert args.topics == cli.PROJECT_ROOT / "topics.yml"
    assert args.database == cli.PROJECT_ROOT / "data" / "papers.db"
    assert args.published == cli.PROJECT_ROOT / "docs" / "data" / "papers.db"


def test_fetch_emits_json_and_returns_zero_only_for_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "load_feeds", lambda path: ["feed"])
    monkeypatch.setattr(cli, "load_topics", lambda path: ["topic"])
    monkeypatch.setattr(
        cli,
        "update_database",
        lambda *args, **kwargs: RunSummary("ok", 1, 0, 0, 0, ("feed",), ()),
    )

    code = cli.main(["fetch", "--database", str(tmp_path / "working.db")])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "fetch"
    assert payload["result"]["status"] == "ok"


@pytest.mark.parametrize("status", ["partial", "error"])
def test_fetch_returns_nonzero_for_incomplete_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], status: str
) -> None:
    monkeypatch.setattr(cli, "load_feeds", lambda path: [])
    monkeypatch.setattr(cli, "load_topics", lambda path: [])
    monkeypatch.setattr(
        cli,
        "update_database",
        lambda *args, **kwargs: RunSummary(status, 0, 0, 0, 1, (), ("bad",)),
    )

    assert cli.main(["fetch"]) == 1
    assert json.loads(capsys.readouterr().out)["result"]["status"] == status


def test_update_orders_fetch_validate_publish(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "load_feeds", lambda path: ["feed"])
    monkeypatch.setattr(cli, "load_topics", lambda path: ["topic"])
    monkeypatch.setattr(
        cli,
        "update_database",
        lambda *args, **kwargs: (
            calls.append("fetch") or RunSummary("ok", 1, 0, 0, 0, ("feed",), ())
        ),
    )
    monkeypatch.setattr(
        cli,
        "validate_database",
        lambda *args, **kwargs: calls.append("validate") or _report(),
    )
    monkeypatch.setattr(
        cli,
        "publish_database",
        lambda *args, **kwargs: calls.append("publish") or _report(),
    )

    assert cli.main(["update"]) == 0
    assert calls == ["fetch", "validate", "publish"]
    assert json.loads(capsys.readouterr().out)["command"] == "update"


def test_update_does_not_validate_or_publish_after_failed_fetch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "load_feeds", lambda path: [])
    monkeypatch.setattr(cli, "load_topics", lambda path: [])
    monkeypatch.setattr(
        cli,
        "update_database",
        lambda *args, **kwargs: RunSummary("partial", 0, 0, 0, 1, (), ("bad",)),
    )
    monkeypatch.setattr(cli, "validate_database", lambda *args, **kwargs: pytest.fail())
    monkeypatch.setattr(cli, "publish_database", lambda *args, **kwargs: pytest.fail())

    assert cli.main(["update"]) == 1
    assert json.loads(capsys.readouterr().out)["result"]["status"] == "partial"


def test_update_does_not_publish_after_failed_validation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "load_feeds", lambda path: [])
    monkeypatch.setattr(cli, "load_topics", lambda path: [])
    monkeypatch.setattr(
        cli,
        "update_database",
        lambda *args, **kwargs: RunSummary("ok", 1, 0, 0, 0, (), ()),
    )
    monkeypatch.setattr(
        cli,
        "validate_database",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValidationError("invalid")),
    )
    monkeypatch.setattr(cli, "publish_database", lambda *args, **kwargs: pytest.fail())

    assert cli.main(["update"]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["type"] == "ValidationError"


def test_configuration_error_is_json_and_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli, "load_feeds", lambda path: (_ for _ in ()).throw(cli.ConfigError("bad config"))
    )

    assert cli.main(["fetch"]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["message"] == "bad config"
