# Noto Sans SC Typography Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将网站中文与文章标题统一为自托管 Noto Sans SC Variable，并把 `.eyebrow` 与 `.filter-index` 精确调整为用户选择的 13px。

**Architecture:** 从 `@fontsource-variable/noto-sans-sc@5.2.10` 确定性提取简体中文分片和基础 Latin 分片，把 98 个 WOFF2 与许可证、来源信息、SHA-256 清单一起发布在 `docs/fonts/noto-sans-sc/`。`docs/styles.css` 内嵌带 `unicode-range` 的本地 `@font-face`；静态测试验证供应链与 CSS 合同，Playwright 验证真实加载、字体失败回退和 390px 布局。

**Tech Stack:** HTML/CSS, Fontsource Noto Sans SC Variable 5.2.10, WOFF2, Python 3.11 standard library, pytest, Playwright, Node test runner, Ruff, GitHub Pages.

---

## File map

- Create: `scripts/vendor_noto_sans_sc.py` — 固定来源、验证 npm 完整性、筛选子集并生成资源。
- Create: `docs/fonts/noto-sans-sc/*.woff2` — 97 个简体中文分片和 1 个基础 Latin 分片。
- Create: `docs/fonts/noto-sans-sc/LICENSE.txt` — OFL-1.1 原文。
- Create: `docs/fonts/noto-sans-sc/FONT-METADATA.md` — 来源、版本、完整性和资源统计。
- Create: `docs/fonts/noto-sans-sc/SHA256SUMS` — 98 个 WOFF2 的逐文件 SHA-256。
- Modify: `docs/styles.css:1-17,88-91` — `@font-face`、字体变量、界面字重和 13px 标签。
- Modify: `tests/test_static_shell.py` — 字体资产、哈希、子集、无 CDN 和字号范围合同。
- Modify: `tests/e2e/test_site.py` — 浏览器字体加载、回退和移动端视觉边界。

### Task 1: Vendor a pinned and auditable Noto Sans SC asset set

**Files:**
- Create: `scripts/vendor_noto_sans_sc.py`
- Create: `docs/fonts/noto-sans-sc/FONT-METADATA.md`
- Create: `docs/fonts/noto-sans-sc/LICENSE.txt`
- Create: `docs/fonts/noto-sans-sc/SHA256SUMS`
- Create: `docs/fonts/noto-sans-sc/*.woff2`
- Modify: `docs/styles.css:1`
- Modify: `tests/test_static_shell.py`

- [ ] **Step 1: Write the failing static asset contract**

Add `import re` to `tests/test_static_shell.py`. Add below `DOCS`:

```python
FONT_DIR = DOCS / "fonts" / "noto-sans-sc"
FONT_PACKAGE = "@fontsource-variable/noto-sans-sc"
FONT_VERSION = "5.2.10"
FONT_TARBALL = (
    "https://registry.npmjs.org/@fontsource-variable/noto-sans-sc/"
    "-/noto-sans-sc-5.2.10.tgz"
)
FONT_INTEGRITY = (
    "sha512-zdk10i5HrDQTXI7ldD61zToX1fsgig8vDTsu7zB48SXOitWfuX0e5viZAwnkHuhw"
    "h096PU6X6i1AyAsbBCISpA=="
)
FONT_REF_PATTERN = re.compile(r"url\(\./fonts/noto-sans-sc/([^\)]+\.woff2)\)")
```

Append:

```python
def _font_manifest() -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in (FONT_DIR / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", maxsplit=1)
        entries[name] = digest
    return entries


def test_noto_sans_assets_are_pinned_local_complete_and_hash_verified() -> None:
    css = (DOCS / "styles.css").read_text(encoding="utf-8")
    metadata = (FONT_DIR / "FONT-METADATA.md").read_text(encoding="utf-8")
    license_text = (FONT_DIR / "LICENSE.txt").read_text(encoding="utf-8")
    references = set(FONT_REF_PATTERN.findall(css))
    assets = {path.name for path in FONT_DIR.glob("*.woff2")}
    manifest = _font_manifest()

    assert f"Package: `{FONT_PACKAGE}`" in metadata
    assert f"Version: `{FONT_VERSION}`" in metadata
    assert f"Tarball: `{FONT_TARBALL}`" in metadata
    assert f"npm integrity: `{FONT_INTEGRITY}`" in metadata
    assert "Upstream font version: `v40`" in metadata
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_text
    assert len(references) == 98
    assert references == assets == set(manifest)
    assert "noto-sans-sc-latin-wght-normal.woff2" in references
    assert all(
        token not in name for name in references
        for token in ("cyrillic", "latin-ext", "vietnamese")
    )
    assert css.count("font-family: 'Noto Sans SC Variable';") == 98
    assert css.count("font-display: swap;") >= 98
    assert css.count("font-weight: 100 900;") >= 98
    assert "fonts.googleapis.com" not in css
    assert "fonts.gstatic.com" not in css
    for name, expected_digest in manifest.items():
        payload = (FONT_DIR / name).read_bytes()
        assert payload.startswith(b"wOF2"), name
        assert hashlib.sha256(payload).hexdigest() == expected_digest, name
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_static_shell.py::test_noto_sans_assets_are_pinned_local_complete_and_hash_verified -v
```

Expected: FAIL with `FileNotFoundError` for `docs/fonts/noto-sans-sc/FONT-METADATA.md`.

- [ ] **Step 3: Create the deterministic vendoring script**

Create `scripts/vendor_noto_sans_sc.py`:

```python
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import tarfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STYLES = ROOT / "docs" / "styles.css"
FONT_DIR = ROOT / "docs" / "fonts" / "noto-sans-sc"
PACKAGE = "@fontsource-variable/noto-sans-sc"
VERSION = "5.2.10"
TARBALL = (
    "https://registry.npmjs.org/@fontsource-variable/noto-sans-sc/"
    "-/noto-sans-sc-5.2.10.tgz"
)
INTEGRITY = (
    "sha512-zdk10i5HrDQTXI7ldD61zToX1fsgig8vDTsu7zB48SXOitWfuX0e5viZAwnkHuhw"
    "h096PU6X6i1AyAsbBCISpA=="
)
BEGIN = "/* BEGIN VENDORED NOTO SANS SC 5.2.10 */"
END = "/* END VENDORED NOTO SANS SC 5.2.10 */"
BLOCK_PATTERN = re.compile(r"/\*.*?\*/\s*@font-face\s*\{.*?\}\s*", re.DOTALL)
FILE_PATTERN = re.compile(r"noto-sans-sc-(?:\d+|latin)-wght-normal\.woff2")
SOURCE_PATTERN = re.compile(r"url\(\./files/([^\)]+\.woff2)\)")


def _member_bytes(archive: tarfile.TarFile, name: str) -> bytes:
    member = archive.getmember(name)
    stream = archive.extractfile(member)
    if stream is None:
        raise RuntimeError(f"Archive member is not a file: {name}")
    return stream.read()


def _download() -> bytes:
    request = urllib.request.Request(TARBALL, headers={"User-Agent": "paper-radar-font-vendor/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read()
    algorithm, expected = INTEGRITY.split("-", maxsplit=1)
    actual = base64.b64encode(hashlib.new(algorithm, payload).digest()).decode("ascii")
    if actual != expected:
        raise RuntimeError(f"npm integrity mismatch: expected {expected}, got {actual}")
    return payload


def _replace_font_faces(styles: str, font_faces: str) -> str:
    generated = f"{BEGIN}\n{font_faces.rstrip()}\n{END}\n\n"
    if BEGIN not in styles and END not in styles:
        return generated + styles
    if styles.count(BEGIN) != 1 or styles.count(END) != 1:
        raise RuntimeError("Vendored font markers are missing or duplicated")
    before, remainder = styles.split(BEGIN, maxsplit=1)
    _, after = remainder.split(END, maxsplit=1)
    return before + generated + after.lstrip("\r\n")


def main() -> None:
    payload = _download()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        upstream_css = _member_bytes(archive, "package/wght.css").decode("utf-8")
        license_text = _member_bytes(archive, "package/LICENSE").decode("utf-8")
        upstream_metadata = json.loads(
            _member_bytes(archive, "package/metadata.json").decode("utf-8")
        )
        kept_blocks: list[str] = []
        referenced: set[str] = set()
        for block in BLOCK_PATTERN.findall(upstream_css):
            source = SOURCE_PATTERN.search(block)
            if source is None or FILE_PATTERN.fullmatch(source.group(1)) is None:
                continue
            name = source.group(1)
            referenced.add(name)
            kept_blocks.append(block.replace("url(./files/", "url(./fonts/noto-sans-sc/"))
        if len(kept_blocks) != 98 or len(referenced) != 98:
            raise RuntimeError(
                f"Unexpected Fontsource layout: {len(kept_blocks)} faces, {len(referenced)} files"
            )

        FONT_DIR.mkdir(parents=True, exist_ok=True)
        resolved_font_dir = FONT_DIR.resolve()
        if ROOT.resolve() not in resolved_font_dir.parents:
            raise RuntimeError(f"Refusing to write outside repository: {resolved_font_dir}")
        for old_asset in FONT_DIR.glob("*.woff2"):
            old_asset.unlink()

        manifest: list[str] = []
        total_bytes = 0
        for name in sorted(referenced):
            data = _member_bytes(archive, f"package/files/{name}")
            if not data.startswith(b"wOF2"):
                raise RuntimeError(f"Invalid WOFF2 signature: {name}")
            (FONT_DIR / name).write_bytes(data)
            total_bytes += len(data)
            manifest.append(f"{hashlib.sha256(data).hexdigest()}  {name}")

    (FONT_DIR / "LICENSE.txt").write_text(license_text, encoding="utf-8", newline="\n")
    (FONT_DIR / "SHA256SUMS").write_text(
        "\n".join(manifest) + "\n", encoding="utf-8", newline="\n"
    )
    metadata = (
        "# Noto Sans SC vendored font metadata\n\n"
        f"- Package: `{PACKAGE}`\n"
        f"- Version: `{VERSION}`\n"
        f"- Tarball: `{TARBALL}`\n"
        f"- npm integrity: `{INTEGRITY}`\n"
        f"- Upstream font version: `{upstream_metadata['version']}`\n"
        f"- License: `{upstream_metadata['license']['type']}`\n"
        f"- Vendored WOFF2 files: `{len(manifest)}`\n"
        f"- Vendored WOFF2 bytes: `{total_bytes}`\n"
        "- Included subsets: Fontsource numbered simplified-Chinese shards and basic Latin.\n"
        "- Excluded subsets: Cyrillic, Latin Extended, and Vietnamese.\n"
    )
    (FONT_DIR / "FONT-METADATA.md").write_text(metadata, encoding="utf-8", newline="\n")
    local_css = "\n".join(block.rstrip() for block in kept_blocks)
    styles = STYLES.read_text(encoding="utf-8")
    STYLES.write_text(
        _replace_font_faces(styles, local_css), encoding="utf-8", newline="\n"
    )
    print(f"Vendored {len(manifest)} WOFF2 files ({total_bytes} bytes) from {PACKAGE}@{VERSION}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Generate the pinned assets**

Run with approved network access:

```powershell
.\.venv\Scripts\python.exe scripts\vendor_noto_sans_sc.py
```

Expected: `Vendored 98 WOFF2 files (4489160 bytes) from @fontsource-variable/noto-sans-sc@5.2.10`.

- [ ] **Step 5: Verify GREEN and reproducibility**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_static_shell.py::test_noto_sans_assets_are_pinned_local_complete_and_hash_verified -v
.\.venv\Scripts\python.exe scripts\vendor_noto_sans_sc.py
git diff --check
```

Expected: test PASS; the second vendoring run keeps one marker pair and identical hashes; `git diff --check` exits 0.

- [ ] **Step 6: Commit the font assets**

```powershell
git add scripts/vendor_noto_sans_sc.py tests/test_static_shell.py docs/styles.css docs/fonts/noto-sans-sc
git commit -m "feat: vendor Noto Sans SC assets"
```

### Task 2: Apply the approved typography and test it in a real browser

**Files:**
- Modify: `docs/styles.css`
- Modify: `tests/test_static_shell.py`
- Modify: `tests/e2e/test_site.py`

- [ ] **Step 1: Write the failing CSS contract**

Append to `tests/test_static_shell.py`:

```python
def test_typography_uses_noto_sans_and_limits_13px_to_approved_labels() -> None:
    css = (DOCS / "styles.css").read_text(encoding="utf-8")
    assert (
        '--ui: "Noto Sans SC Variable", "Microsoft YaHei UI", '
        '"PingFang SC", sans-serif;'
    ) in css
    assert (
        '--editorial: "Noto Sans SC Variable", "Microsoft YaHei UI", '
        '"PingFang SC", sans-serif;'
    ) in css
    assert re.search(
        r"\.eyebrow,\s*\.filter-index\s*\{[^}]*"
        r"font:\s*700 13px/1\.2 ui-monospace, Consolas, monospace;",
        css,
        re.DOTALL,
    )
    assert re.search(r"\.edition\s*\{[^}]*font:\s*11px/1\.5", css, re.DOTALL)
    assert re.search(r"\.search-label\s*\{[^}]*font:\s*700 11px/1", css, re.DOTALL)
    assert re.search(r"footer\s*\{[^}]*font:\s*10px/1\.4", css, re.DOTALL)
    assert re.search(r"\.badge\s*\{[^}]*font:\s*700 11px/1", css, re.DOTALL)
    assert re.search(r"\.chip\s*\{[^}]*font-size:\s*11px", css, re.DOTALL)
```

- [ ] **Step 2: Write failing browser load and fallback tests**

Append to `tests/e2e/test_site.py`:

```python
def test_local_noto_font_and_approved_13px_labels_render_in_browser(
    page: Page,
    site_url: str,
) -> None:
    font_requests: list[str] = []
    page.on(
        "request",
        lambda request: font_requests.append(request.url)
        if "/fonts/noto-sans-sc/" in request.url
        else None,
    )
    page.goto(site_url)
    _wait_ready(page)
    loaded = page.evaluate(
        """async () => (await document.fonts.load(
            '400 16px "Noto Sans SC Variable"', '最新论文'
        )).length > 0"""
    )
    assert loaded is True
    assert font_requests
    assert all(url.startswith(site_url) for url in font_requests)
    assert "Noto Sans SC Variable" in page.locator("body").evaluate(
        "element => getComputedStyle(element).fontFamily"
    )
    assert "Noto Sans SC Variable" in page.locator(".article-card h2").first.evaluate(
        "element => getComputedStyle(element).fontFamily"
    )
    expect(page.locator(".eyebrow").first).to_have_css("font-size", "13px")
    expect(page.locator(".filter-index").first).to_have_css("font-size", "13px")
    expect(page.locator(".edition")).to_have_css("font-size", "11px")
    expect(page.locator(".search-label")).to_have_css("font-size", "11px")
    expect(page.locator(".brand-mark")).to_have_css("font-family", "Georgia, serif")


def test_site_remains_functional_when_local_font_downloads_fail(
    browser_context: Any,
    site_url: str,
) -> None:
    intercepted: list[str] = []
    fallback_page = browser_context.new_page()

    def abort_font(route: Any) -> None:
        intercepted.append(route.request.url)
        route.abort()

    fallback_page.route("**/fonts/noto-sans-sc/*.woff2", abort_font)
    try:
        fallback_page.goto(site_url)
        _wait_ready(fallback_page)
        expect(fallback_page.locator(".article-card")).to_have_count(20)
        expect(fallback_page.locator("#search")).to_be_editable()
        assert intercepted
        assert fallback_page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ) is True
    finally:
        fallback_page.close()
```

- [ ] **Step 3: Run the contracts and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_static_shell.py::test_typography_uses_noto_sans_and_limits_13px_to_approved_labels tests/e2e/test_site.py::test_local_noto_font_and_approved_13px_labels_render_in_browser -v
```

Expected: the static test fails because `--ui` starts with Microsoft YaHei; the browser test fails because the calculated family does not contain `Noto Sans SC Variable`.

- [ ] **Step 4: Apply the exact approved CSS**

Change the two variables to:

```css
--ui: "Noto Sans SC Variable", "Microsoft YaHei UI", "PingFang SC", sans-serif;
--editorial: "Noto Sans SC Variable", "Microsoft YaHei UI", "PingFang SC", sans-serif;
```

Change only the approved technical-label size:

```css
.eyebrow, .filter-index {
  margin: 0 0 5px; color: var(--copper-dark); font: 700 13px/1.2 ui-monospace, Consolas, monospace;
  letter-spacing: .16em; text-transform: uppercase;
}
```

Add 500 weight to interface text that currently lacks an explicit weight:

```css
.site-header nav a { font-weight: 500; }
.filters input, .filters select, .search-row input, .search-row select { font-weight: 500; }
.secondary, .filter-toggle, .icon-button { font-weight: 500; }
.chip { font-weight: 500; }
.pagination button { font-weight: 500; }
```

Merge these declarations into the existing selector blocks. Do not create duplicate selectors. Keep `.brand-mark`, `.eyebrow`, `.filter-index`, `.edition`, and `footer` on Georgia/monospace. Do not change `.edition`, `.search-label`, `.brand small`, `.badge`, `.chip`, or footer font sizes.

- [ ] **Step 5: Run focused tests and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_static_shell.py tests/e2e/test_site.py -v
```

Expected: all tests PASS, including local font loading, 13px labels, excluded sizes, font failure fallback, and 390px overflow.

- [ ] **Step 6: Commit the approved typography**

```powershell
git add docs/styles.css tests/test_static_shell.py tests/e2e/test_site.py
git commit -m "feat: apply Noto Sans SC typography"
```

### Task 3: Run complete regression and visual QA

**Files:**
- Verify only: repository-wide tests and generated assets

- [ ] **Step 1: Run all Python and Playwright tests**

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

Expected: all tests PASS. Normal acceptance must use Edge or Chromium and must not set `PAPER_RADAR_ALLOW_BROWSER_SKIP=1`.

- [ ] **Step 2: Run Node and Ruff gates**

```powershell
npm run test:web
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

Expected: Node reports zero failures; Ruff and `git diff --check` exit 0.

- [ ] **Step 3: Recheck all 98 asset hashes**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_static_shell.py::test_noto_sans_assets_are_pinned_local_complete_and_hash_verified -v
```

Expected: PASS.

- [ ] **Step 4: Review desktop and mobile rendering**

Run:

```powershell
.\.venv\Scripts\python.exe -m http.server 8000 --directory docs
```

Open `http://localhost:8000` in Edge at 1280×900 and 390×844. Verify:

1. Chinese UI, headings, and article titles render as Noto Sans SC rather than SimSun.
2. `REFINE / 01`, filter indexes, `LIVE INDEX / 2026`, `SYSTEM / 02`, and `NOTES / 03` render at 13px.
3. `PR` remains Georgia; technical labels remain monospace.
4. Edition text, search label, badge, chips, and footer retain their original sizes.
5. No overlaps or horizontal scrollbar appear at 390px.

- [ ] **Step 5: Confirm repository state**

```powershell
git status --short
git log -3 --oneline
```

Expected: no unstaged implementation changes; recent commits include the font-assets and typography commits.

### Task 4: Publish and verify GitHub Pages

**Files:**
- External state: `Hsuanqi77/academic-rss-site` main branch and GitHub Pages

- [ ] **Step 1: Push the verified commits**

```powershell
git push origin main
```

Expected: remote `main` advances without rejected updates.

- [ ] **Step 2: Wait for Pages**

```powershell
$gh = 'C:\Program Files\GitHub CLI\gh.exe'
$runId = & $gh run list --repo Hsuanqi77/academic-rss-site --workflow pages-build-deployment --limit 1 --json databaseId --jq '.[0].databaseId'
if (-not $runId) { throw 'No GitHub Pages workflow run was found' }
& $gh run watch $runId --repo Hsuanqi77/academic-rss-site --exit-status
```

Expected: the latest Pages deployment finishes with `success`.

- [ ] **Step 3: Verify production CSS and all referenced fonts**

```powershell
$site = 'https://hsuanqi77.github.io/academic-rss-site'
$css = (Invoke-WebRequest -UseBasicParsing "$site/styles.css").Content
if ($css -notmatch 'Noto Sans SC Variable') { throw 'Production CSS is missing Noto Sans SC' }
if ($css -notmatch 'font: 700 13px/1.2 ui-monospace') { throw 'Production CSS is missing 13px labels' }
if ($css -match 'fonts\.googleapis\.com|fonts\.gstatic\.com') { throw 'Remote font dependency found' }
$matches = [regex]::Matches($css, 'url\(\./fonts/noto-sans-sc/([^\)]+\.woff2)\)')
$fontNames = $matches | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
if ($fontNames.Count -ne 98) { throw "Expected 98 font assets, got $($fontNames.Count)" }
foreach ($name in $fontNames) {
  $response = Invoke-WebRequest -UseBasicParsing -Method Head "$site/fonts/noto-sans-sc/$name"
  if ($response.StatusCode -ne 200) { throw "$name returned $($response.StatusCode)" }
}
"Verified production CSS and $($fontNames.Count) local WOFF2 assets"
```

Expected: `Verified production CSS and 98 local WOFF2 assets`.

- [ ] **Step 4: Complete production visual acceptance**

Open <https://hsuanqi77.github.io/academic-rss-site/> in Edge, hard refresh once, and check at 1280×900 and 390×844:

1. Chinese UI, headings, and article titles render as Noto Sans SC rather than SimSun.
2. `REFINE / 01`, all filter indexes, `LIVE INDEX / 2026`, `SYSTEM / 02`, and `NOTES / 03` render at 13px.
3. `PR` remains Georgia; the technical labels remain monospace.
4. Edition text, search label, badge, chips, and footer retain their original sizes.
5. No label overlaps a heading or control, and no horizontal scrollbar appears at 390px.
6. In DevTools Network, every `font` request is same-origin under `/academic-rss-site/fonts/noto-sans-sc/` and returns HTTP 200.
