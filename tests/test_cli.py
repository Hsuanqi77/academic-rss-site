import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import paper_radar.cli as cli
from paper_radar.models import ClassificationSummary, RunSummary
from paper_radar.validation import ValidationError, ValidationReport


def _report() -> ValidationReport:
    return ValidationReport(
        article_count=3,
        journal_count=2,
        earliest_date="2026-07-01T00:00:00Z",
        latest_date="2026-07-03T00:00:00Z",
        schema_version=4,
    )


def _classification(
    articles_scanned: int = 0,
    articles_tagged: int = 0,
    tag_assignments: int = 0,
    active_tags: int = 0,
) -> ClassificationSummary:
    return ClassificationSummary(
        articles_scanned,
        articles_tagged,
        tag_assignments,
        active_tags,
    )


def _summary(status: str = "ok") -> RunSummary:
    return RunSummary(
        status,
        1 if status != "error" else 0,
        0,
        0,
        int(status != "ok"),
        ("feed",) if status != "error" else (),
        ("bad",) if status != "ok" else (),
        _classification(1, 1, 2, 2),
    )


def _catalog(*topics: object) -> SimpleNamespace:
    return SimpleNamespace(topics=topics)


def test_cli_requires_a_subcommand() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_default_paths_are_resolved_from_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    args = cli.build_parser().parse_args(["update"])

    assert args.feeds == tmp_path / "feeds.yml"
    assert args.topics == tmp_path / "topics.yml"
    assert args.database == tmp_path / "data" / "papers.db"
    assert args.published == tmp_path / "docs" / "data" / "papers.db"


def test_dotenv_is_loaded_from_current_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    loaded: list[Path] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "load_dotenv", lambda path: loaded.append(path))
    monkeypatch.setattr(
        cli,
        "_fetch",
        lambda args: RunSummary(
            "error", 0, 0, 0, 1, (), ("expected",), _classification()
        ),
    )

    assert cli.main(["fetch"]) == 1

    assert loaded == [tmp_path / ".env"]
    assert json.loads(capsys.readouterr().out)["publish_allowed"] is False


def test_fetch_emits_json_and_returns_zero_only_for_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "load_feeds", lambda path: ["feed"])
    catalog_loads: list[Path] = []
    update_calls: list[tuple[object, object, object]] = []
    monkeypatch.setattr(
        cli,
        "load_topic_catalog",
        lambda path: catalog_loads.append(path) or _catalog("topic"),
    )
    def update_database(*args: object, **kwargs: object) -> RunSummary:
        update_calls.append((args[0], args[1], args[2]))
        return _summary()

    monkeypatch.setattr(cli, "update_database", update_database)

    code = cli.main(["fetch", "--database", str(tmp_path / "working.db")])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "fetch"
    assert payload["result"]["status"] == "ok"
    assert payload["result"]["classification"] == {
        "active_tags": 2,
        "articles_scanned": 1,
        "articles_tagged": 1,
        "tag_assignments": 2,
    }
    assert len(catalog_loads) == 1
    assert update_calls == [(tmp_path / "working.db", ["feed"], ("topic",))]


def test_fetch_treats_partial_as_degraded_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "load_feeds", lambda path: [])
    monkeypatch.setattr(cli, "load_topic_catalog", lambda path: _catalog())
    monkeypatch.setattr(
        cli,
        "update_database",
        lambda *args, **kwargs: _summary("partial"),
    )

    assert cli.main(["fetch"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["status"] == "partial"


def test_fetch_returns_nonzero_for_error_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "load_feeds", lambda path: [])
    monkeypatch.setattr(cli, "load_topic_catalog", lambda path: _catalog())
    monkeypatch.setattr(
        cli,
        "update_database",
        lambda *args, **kwargs: _summary("error"),
    )

    assert cli.main(["fetch"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"]["status"] == "error"
    assert payload["publish_allowed"] is False


@pytest.mark.parametrize("status", ["ok", "partial"])
def test_update_orders_successful_fetch_validate_publish_and_reports_sizes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: str,
) -> None:
    calls: list[str] = []
    working = tmp_path / "working.db"
    published = tmp_path / "published.db"
    working.write_bytes(b"working")
    published.write_bytes(b"published")
    monkeypatch.setattr(cli, "load_feeds", lambda path: ["feed"])
    monkeypatch.setattr(cli, "load_topic_catalog", lambda path: _catalog("topic"))
    monkeypatch.setattr(
        cli,
        "update_database",
        lambda *args, **kwargs: (
            calls.append("fetch")
            or RunSummary(
                status,
                1,
                0,
                0,
                int(status == "partial"),
                ("feed",),
                (),
                _classification(1, 1, 2, 2),
            )
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

    assert (
        cli.main(
            [
                "update",
                "--database",
                str(working),
                "--published",
                str(published),
            ]
        )
        == 0
    )
    assert calls == ["fetch", "validate", "publish"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "update"
    assert payload["result"]["status"] == status
    assert payload["result"]["classification"] == {
        "active_tags": 2,
        "articles_scanned": 1,
        "articles_tagged": 1,
        "tag_assignments": 2,
    }
    assert payload["working_size_bytes"] == len(b"working")
    assert payload["published_size_bytes"] == len(b"published")
    assert payload["publish_allowed"] is True


def test_update_does_not_validate_or_publish_after_error_fetch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "load_feeds", lambda path: [])
    monkeypatch.setattr(cli, "load_topic_catalog", lambda path: _catalog())
    monkeypatch.setattr(
        cli,
        "update_database",
        lambda *args, **kwargs: _summary("error"),
    )
    monkeypatch.setattr(cli, "validate_database", lambda *args, **kwargs: pytest.fail())
    monkeypatch.setattr(cli, "publish_database", lambda *args, **kwargs: pytest.fail())

    assert cli.main(["update"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"]["status"] == "error"
    assert payload["publish_allowed"] is False


def test_update_does_not_publish_after_failed_validation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "load_feeds", lambda path: [])
    monkeypatch.setattr(cli, "load_topic_catalog", lambda path: _catalog())
    monkeypatch.setattr(
        cli,
        "update_database",
        lambda *args, **kwargs: _summary(),
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
    assert error["publish_allowed"] is False
    assert error["command"] == "update"
    assert error["result"]["status"] == "ok"


def test_publish_success_reports_sizes_and_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    working = tmp_path / "working.db"
    published = tmp_path / "published.db"
    working.write_bytes(b"working database")
    published.write_bytes(b"published database")
    monkeypatch.setattr(cli, "publish_database", lambda *args, **kwargs: _report())

    assert (
        cli.main(
            [
                "publish",
                "--database",
                str(working),
                "--published",
                str(published),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["validation"]["schema_version"] == 4
    assert payload["working_size_bytes"] == len(b"working database")
    assert payload["published_size_bytes"] == len(b"published database")
    assert payload["publish_allowed"] is True


def test_publish_failure_never_reports_publication_as_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    working = tmp_path / "working.db"
    working.write_bytes(b"working database")
    monkeypatch.setattr(
        cli,
        "publish_database",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValidationError("unsafe")),
    )

    assert cli.main(["publish", "--database", str(working)]) == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["publish_allowed"] is False
    assert payload["error"]["type"] == "ValidationError"


def test_configuration_error_is_json_and_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli, "load_feeds", lambda path: (_ for _ in ()).throw(cli.ConfigError("bad config"))
    )

    assert cli.main(["fetch"]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["message"] == "bad config"
