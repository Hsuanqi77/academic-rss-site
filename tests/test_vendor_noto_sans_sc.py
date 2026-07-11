from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import vendor_noto_sans_sc as vendor


def _replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, css: str) -> str:
    stylesheet = tmp_path / "styles.css"
    stylesheet.write_text(css, encoding="utf-8")
    monkeypatch.setattr(vendor, "STYLESHEET", stylesheet)
    vendor._replace_vendored_css("@font-face { font-display: swap; }")
    return stylesheet.read_text(encoding="utf-8")


def test_marker_replacement_accepts_stylesheet_without_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _replace(tmp_path, monkeypatch, ":root { color: black; }\n")

    assert result.count(vendor.BEGIN_MARKER) == 1
    assert result.count(vendor.END_MARKER) == 1
    assert result.endswith(":root { color: black; }\n")


def test_marker_replacement_accepts_exactly_one_ordered_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = (
        f"{vendor.BEGIN_MARKER}\nold face\n{vendor.END_MARKER}\n\n"
        ":root { color: black; }\n"
    )
    result = _replace(tmp_path, monkeypatch, existing)

    assert result.count(vendor.BEGIN_MARKER) == 1
    assert result.count(vendor.END_MARKER) == 1
    assert "old face" not in result


@pytest.mark.parametrize(
    "invalid_css",
    [
        pytest.param(f"{vendor.BEGIN_MARKER}\n:root {{}}\n", id="begin-only"),
        pytest.param(f"{vendor.END_MARKER}\n:root {{}}\n", id="end-only"),
        pytest.param(
            f"{vendor.BEGIN_MARKER}\n{vendor.END_MARKER}\n"
            f"{vendor.BEGIN_MARKER}\n{vendor.END_MARKER}\n",
            id="duplicate",
        ),
        pytest.param(
            f"{vendor.END_MARKER}\nold face\n{vendor.BEGIN_MARKER}\n",
            id="reversed",
        ),
    ],
)
def test_marker_replacement_rejects_malformed_marker_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_css: str,
) -> None:
    with pytest.raises(RuntimeError, match="vendored Noto Sans SC markers"):
        _replace(tmp_path, monkeypatch, invalid_css)


def test_archive_metadata_is_verified_and_returned() -> None:
    package_json = json.dumps(
        {
            "name": "@fontsource-variable/noto-sans-sc",
            "version": "5.2.10",
            "license": "OFL-1.1",
        }
    ).encode()
    metadata_json = json.dumps(
        {"id": "noto-sans-sc", "version": "v40", "license": {"type": "OFL-1.1"}}
    ).encode()

    assert vendor._verify_archive_metadata(package_json, metadata_json) == ("v40", "OFL-1.1")


@pytest.mark.parametrize(
    ("document", "field", "bad_value"),
    [
        pytest.param("package", "name", "wrong-package", id="package-name"),
        pytest.param("package", "version", "0.0.0", id="package-version"),
        pytest.param("package", "license", "MIT", id="package-license"),
        pytest.param("metadata", "id", "wrong-font", id="metadata-id"),
        pytest.param("metadata", "version", "v39", id="metadata-version"),
        pytest.param("metadata", "license", {"type": "MIT"}, id="metadata-license"),
    ],
)
def test_archive_metadata_rejects_unexpected_provenance(
    document: str, field: str, bad_value: object
) -> None:
    package = {
        "name": "@fontsource-variable/noto-sans-sc",
        "version": "5.2.10",
        "license": "OFL-1.1",
    }
    metadata = {"id": "noto-sans-sc", "version": "v40", "license": {"type": "OFL-1.1"}}
    target = package if document == "package" else metadata
    target[field] = bad_value

    with pytest.raises(RuntimeError, match="archive metadata"):
        vendor._verify_archive_metadata(json.dumps(package).encode(), json.dumps(metadata).encode())
