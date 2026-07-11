"""Vendor the pinned Noto Sans SC variable font assets used by the static site."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import tarfile
import urllib.request
from pathlib import Path


PACKAGE = "@fontsource-variable/noto-sans-sc"
VERSION = "5.2.10"
TARBALL = (
    "https://registry.npmjs.org/@fontsource-variable/noto-sans-sc/-/"
    "noto-sans-sc-5.2.10.tgz"
)
INTEGRITY = (
    "sha512-zdk10i5HrDQTXI7ldD61zToX1fsgig8vDTsu7zB48SXOitWfuX0e5viZAwnkHuhwh"
    "096PU6X6i1AyAsbBCISpA=="
)
EXPECTED_UPSTREAM_VERSION = "v40"
EXPECTED_LICENSE = "OFL-1.1"
EXPECTED_FONT_COUNT = 98
EXPECTED_TOTAL_BYTES = 4_489_160
FONT_NAME = re.compile(r"noto-sans-sc-(?:\d+|latin)-wght-normal\.woff2")
FACE = re.compile(r"@font-face\s*\{.*?\}", re.DOTALL)
SOURCE_FONT = re.compile(r"\./files/(noto-sans-sc-[^)'\"]+\.woff2)")
BEGIN_MARKER = f"/* BEGIN VENDORED NOTO SANS SC {VERSION} */"
END_MARKER = f"/* END VENDORED NOTO SANS SC {VERSION} */"

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"
FONT_DIR = DOCS_DIR / "fonts" / "noto-sans-sc"
STYLESHEET = DOCS_DIR / "styles.css"


def _download() -> bytes:
    request = urllib.request.Request(TARBALL, headers={"User-Agent": "paper-radar-vendor/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read()
    algorithm, encoded_digest = INTEGRITY.split("-", 1)
    if algorithm != "sha512":
        raise RuntimeError(f"Unsupported integrity algorithm: {algorithm}")
    actual_digest = hashlib.sha512(payload).digest()
    if actual_digest != base64.b64decode(encoded_digest, validate=True):
        raise RuntimeError("Downloaded tarball does not match pinned npm integrity")
    return payload


def _read_member(archive: tarfile.TarFile, name: str) -> bytes:
    try:
        member = archive.getmember(name)
    except KeyError as error:
        raise RuntimeError(f"Pinned package is missing {name}") from error
    if not member.isfile():
        raise RuntimeError(f"Pinned package member is not a regular file: {name}")
    stream = archive.extractfile(member)
    if stream is None:
        raise RuntimeError(f"Pinned package member is not a regular file: {name}")
    return stream.read()


def _select_faces(css: str) -> tuple[str, list[str]]:
    selected: list[tuple[str, str]] = []
    for face in FACE.findall(css):
        match = SOURCE_FONT.search(face)
        if match and FONT_NAME.fullmatch(match.group(1)):
            filename = match.group(1)
            normalized = face.replace("./files/", "./fonts/noto-sans-sc/")
            normalized = normalized.replace(
                "font-family: 'Noto Sans SC Variable';",
                'font-family: "Noto Sans SC Variable";',
            )
            selected.append((filename, normalized))

    selected.sort(key=lambda item: item[0])
    filenames = [filename for filename, _ in selected]
    if len(selected) != EXPECTED_FONT_COUNT or len(set(filenames)) != EXPECTED_FONT_COUNT:
        raise RuntimeError(
            f"Expected {EXPECTED_FONT_COUNT} unique font faces, found {len(set(filenames))}"
        )
    return "\n\n".join(face for _, face in selected), filenames


def _verify_archive_metadata(package_json: bytes, metadata_json: bytes) -> tuple[str, str]:
    try:
        package = json.loads(package_json)
        metadata = json.loads(metadata_json)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Invalid archive metadata JSON") from error

    expected_package = {
        "name": PACKAGE,
        "version": VERSION,
        "license": EXPECTED_LICENSE,
    }
    if any(package.get(field) != value for field, value in expected_package.items()):
        raise RuntimeError("Unexpected package.json archive metadata")

    license_data = metadata.get("license")
    license_type = license_data.get("type") if isinstance(license_data, dict) else None
    if (
        metadata.get("id") != "noto-sans-sc"
        or metadata.get("version") != EXPECTED_UPSTREAM_VERSION
        or license_type != EXPECTED_LICENSE
    ):
        raise RuntimeError("Unexpected metadata.json archive metadata")
    return metadata["version"], license_type


def _safe_font_dir() -> Path:
    repo_root = REPO_ROOT.resolve()
    target = FONT_DIR.resolve()
    try:
        target.relative_to(repo_root)
    except ValueError as error:
        raise RuntimeError(f"Refusing to write font assets outside repository: {target}") from error
    return target


def _safe_font_target(font_dir: Path, filename: str) -> Path:
    target = (font_dir / filename).resolve()
    try:
        target.relative_to(REPO_ROOT.resolve())
    except ValueError as error:
        raise RuntimeError(f"Refusing to write font asset outside repository: {target}") from error
    return target


def _replace_vendored_css(vendored_css: str) -> None:
    existing = STYLESHEET.read_text(encoding="utf-8")
    begin_count = existing.count(BEGIN_MARKER)
    end_count = existing.count(END_MARKER)
    if begin_count or end_count:
        if (
            begin_count != 1
            or end_count != 1
            or existing.index(BEGIN_MARKER) > existing.index(END_MARKER)
        ):
            raise RuntimeError("Invalid vendored Noto Sans SC markers in stylesheet")
    marker_block = re.compile(
        rf"{re.escape(BEGIN_MARKER)}.*?{re.escape(END_MARKER)}\s*", re.DOTALL
    )
    without_old_block = marker_block.sub("", existing)
    block = f"{BEGIN_MARKER}\n{vendored_css}\n{END_MARKER}\n\n"
    STYLESHEET.write_text(block + without_old_block, encoding="utf-8", newline="\n")


def _metadata(total_bytes: int, upstream_version: str, license_type: str) -> str:
    return f"""# Vendored Noto Sans SC

- Package: `{PACKAGE}`
- Version: `{VERSION}`
- Tarball: `{TARBALL}`
- Integrity: `{INTEGRITY}`
- Upstream font version: `{upstream_version}`
- License: `{license_type}`
- WOFF2 files: `{EXPECTED_FONT_COUNT}`
- Total WOFF2 bytes: `{total_bytes}`
- Included subsets: numbered Simplified Chinese subsets and `latin`
- Excluded subsets: `cyrillic`, `latin-ext`, and `vietnamese`

Generated deterministically by `scripts/vendor_noto_sans_sc.py`.
"""


def main() -> None:
    payload = _download()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        upstream_version, license_type = _verify_archive_metadata(
            _read_member(archive, "package/package.json"),
            _read_member(archive, "package/metadata.json"),
        )
        source_css = _read_member(archive, "package/wght.css").decode("utf-8")
        license_text = _read_member(archive, "package/LICENSE").decode("utf-8")
        vendored_css, filenames = _select_faces(source_css)
        font_payloads = {
            filename: _read_member(archive, f"package/files/{filename}")
            for filename in filenames
        }

    total_bytes = sum(len(font_payload) for font_payload in font_payloads.values())
    if total_bytes != EXPECTED_TOTAL_BYTES:
        raise RuntimeError(f"Expected {EXPECTED_TOTAL_BYTES} WOFF2 bytes, found {total_bytes}")
    for filename, font_payload in font_payloads.items():
        if not font_payload.startswith(b"wOF2"):
            raise RuntimeError(f"Invalid WOFF2 signature: {filename}")

    font_dir = _safe_font_dir()
    font_dir.mkdir(parents=True, exist_ok=True)
    for old_font in font_dir.glob("*.woff2"):
        old_font.unlink()
    for filename, font_payload in font_payloads.items():
        _safe_font_target(font_dir, filename).write_bytes(font_payload)

    checksums = "".join(
        f"{hashlib.sha256(font_payloads[name]).hexdigest()}  {name}\n" for name in filenames
    )
    (font_dir / "LICENSE.txt").write_text(license_text, encoding="utf-8", newline="\n")
    (font_dir / "SHA256SUMS").write_text(checksums, encoding="ascii", newline="\n")
    (font_dir / "FONT-METADATA.md").write_text(
        _metadata(total_bytes, upstream_version, license_type), encoding="utf-8", newline="\n"
    )
    _replace_vendored_css(vendored_css)


if __name__ == "__main__":
    main()
