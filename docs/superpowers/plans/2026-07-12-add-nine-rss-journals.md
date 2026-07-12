# Add Nine Research RSS Journals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the production RSS catalog from 20 to 29 enabled journals while preserving all-item ingestion, deterministic Guide generation, and the existing 8-direction/56-tag classifier.

**Architecture:** Keep `feeds.yml` as the sole source of truth. Extend publisher validation and Guide labels for APS, Elsevier, AAAS, and Springer Nature; add the nine approved official feeds; regenerate the Guide and lock the 29-source contract in tests. No parser, database schema, classifier, schedule, or ACS workaround is added.

**Tech Stack:** Python 3.12, PyYAML, httpx, feedparser, pytest, static HTML/CSS, GitHub Actions, PowerShell, Node test runner.

---

## File map

- Modify `src/paper_radar/config.py`: validated publisher identifiers.
- Modify `src/paper_radar/guide.py`: deterministic publisher display labels and order.
- Modify `feeds.yml`: nine enabled RSS sources.
- Modify `README.md`: 29-source and publisher-count documentation.
- Modify `docs/index.html`: generated Guide region only.
- Modify `tests/test_config.py`: publisher and exact production-feed contracts.
- Modify `tests/test_guide.py`: publisher rendering order.
- Modify `tests/test_release_files.py`: 29-source generated-release contract and ACS exclusion.

### Task 1: Extend the publisher contract

**Files:**
- Modify: `src/paper_radar/config.py:15`
- Modify: `src/paper_radar/guide.py:12-17`
- Test: `tests/test_config.py`
- Test: `tests/test_guide.py`

- [ ] **Step 1: Write failing configuration tests for the complete publisher set**

Add this contract near the existing feed-validation tests in `tests/test_config.py`:

```python
@pytest.mark.parametrize(
    "publisher",
    ("nature", "aps", "aip", "ieee", "wiley", "elsevier", "aaas", "springer"),
)
def test_supported_publisher_codes_load(tmp_path: Path, publisher: str) -> None:
    path = tmp_path / "feeds.yml"
    path.write_text(
        "feeds:\n"
        f"  - id: {publisher}\n"
        f"    name: {publisher}\n"
        f"    publisher: {publisher}\n"
        f"    feed_url: https://example.com/{publisher}.rss\n",
        encoding="utf-8",
    )

    feeds = load_feeds(path)

    assert feeds[0].publisher == publisher
```

- [ ] **Step 2: Write a failing Guide test for exact publisher order and labels**

Add to `tests/test_guide.py`:

```python
def test_render_guide_uses_complete_publisher_order() -> None:
    publisher_feeds = tuple(
        FeedConfig(code, code, code, f"https://example.com/{code}.rss")
        for code in (
            "nature",
            "aps",
            "aip",
            "ieee",
            "wiley",
            "elsevier",
            "aaas",
            "springer",
        )
    )

    html = render_guide(publisher_feeds, _catalog())

    labels = (
        "Nature Portfolio",
        "American Physical Society",
        "AIP Publishing",
        "IEEE",
        "Wiley",
        "Elsevier",
        "AAAS",
        "Springer Nature",
    )
    assert [html.index(label) for label in labels] == sorted(
        html.index(label) for label in labels
    )
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py::test_supported_publisher_codes_load tests/test_guide.py::test_render_guide_uses_complete_publisher_order -q
```

Expected: failures for unknown `aps`, `elsevier`, `aaas`, and `springer` publishers or missing labels.

- [ ] **Step 4: Implement the minimal publisher definitions**

Replace `VALID_PUBLISHERS` in `src/paper_radar/config.py` with:

```python
VALID_PUBLISHERS = {
    "nature",
    "aps",
    "aip",
    "ieee",
    "wiley",
    "elsevier",
    "aaas",
    "springer",
}
```

Replace `PUBLISHER_LABELS` in `src/paper_radar/guide.py` with this insertion-ordered mapping:

```python
PUBLISHER_LABELS = {
    "nature": "Nature Portfolio",
    "aps": "American Physical Society",
    "aip": "AIP Publishing",
    "ieee": "IEEE",
    "wiley": "Wiley",
    "elsevier": "Elsevier",
    "aaas": "AAAS",
    "springer": "Springer Nature",
}
```

- [ ] **Step 5: Run focused and adjacent tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_guide.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit the publisher contract**

```powershell
git add src/paper_radar/config.py src/paper_radar/guide.py tests/test_config.py tests/test_guide.py
git commit -m "feat: support expanded RSS publishers"
```

### Task 2: Add the nine production feeds and release contract

**Files:**
- Modify: `feeds.yml`
- Modify: `README.md:5,87-95`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write the exact nine-feed production contract**

Add this constant and test to `tests/test_config.py`:

```python
EXPECTED_NEW_PRODUCTION_FEEDS = {
    "physical-review-applied": (
        "Physical Review Applied",
        "aps",
        "https://feeds.aps.org/rss/recent/prapplied.xml",
    ),
    "nature-electronics": (
        "Nature Electronics",
        "nature",
        "https://www.nature.com/natelectron.rss",
    ),
    "advanced-electronic-materials": (
        "Advanced Electronic Materials",
        "wiley",
        "https://advanced.onlinelibrary.wiley.com/action/showFeed?type=etoc&feed=rss&jc=2199160X",
    ),
    "journal-applied-physics": (
        "Journal of Applied Physics",
        "aip",
        "https://pubs.aip.org/rss/site_1000029/1000017.xml",
    ),
    "apl-materials": (
        "APL Materials",
        "aip",
        "https://pubs.aip.org/rss/site_1000013/1000009.xml",
    ),
    "npj-computational-materials": (
        "npj Computational Materials",
        "nature",
        "https://www.nature.com/npjcompumats.rss",
    ),
    "acta-materialia": (
        "Acta Materialia",
        "elsevier",
        "https://rss.sciencedirect.com/publication/science/13596454",
    ),
    "science-advances": (
        "Science Advances",
        "aaas",
        "https://feeds.science.org/rss/science-advances.xml",
    ),
    "nano-micro-letters": (
        "Nano-Micro Letters",
        "springer",
        "https://link.springer.com/search.rss?facet-journal-id=40820",
    ),
}


def test_seed_configuration_contains_approved_new_feeds() -> None:
    feeds = {feed.id: feed for feed in load_feeds(PROJECT_ROOT / "feeds.yml")}

    assert len(feeds) == 29
    for feed_id, expected in EXPECTED_NEW_PRODUCTION_FEEDS.items():
        feed = feeds[feed_id]
        assert (feed.name, feed.publisher, feed.feed_url) == expected
        assert feed.enabled is True
    assert all(feed.name != "ACS Applied Materials & Interfaces" for feed in feeds.values())
```

Replace the existing ordered-ID fixture with this complete publisher-group order:

```python
expected_ids = [
    "nature-communications",
    "nature-biotechnology",
    "nature-methods",
    "nature",
    "nature-cancer",
    "nature-machine-intelligence",
    "nature-computational-science",
    "nature-reviews-molecular-cell-biology",
    "nature-reviews-genetics",
    "nature-reviews-cancer",
    "microsystems-nanoengineering",
    "nature-electronics",
    "npj-computational-materials",
    "physical-review-applied",
    "applied-physics-letters",
    "journal-applied-physics",
    "apl-materials",
    "ieee-transactions-ultrasonics",
    "ieee-transactions-mtt",
    "ieee-microwave-wireless-technology-letters",
    "ieee-transactions-electron-devices",
    "ieee-electron-device-letters",
    "journal-microelectromechanical-systems",
    "advanced-materials",
    "advanced-functional-materials",
    "advanced-electronic-materials",
    "acta-materialia",
    "science-advances",
    "nano-micro-letters",
]
```

- [ ] **Step 2: Run the production-feed test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py::test_seed_configuration_contains_approved_new_feeds -q
```

Expected: current feed count is 20 and the new IDs are missing.

- [ ] **Step 3: Add the nine feeds in deterministic publisher order**

Edit `feeds.yml` so publisher blocks follow the approved Guide order. Insert these exact mappings in the relevant blocks:

```yaml
  - id: nature-electronics
    name: Nature Electronics
    publisher: nature
    feed_url: https://www.nature.com/natelectron.rss
  - id: npj-computational-materials
    name: npj Computational Materials
    publisher: nature
    feed_url: https://www.nature.com/npjcompumats.rss
  - id: physical-review-applied
    name: Physical Review Applied
    publisher: aps
    feed_url: https://feeds.aps.org/rss/recent/prapplied.xml
  - id: journal-applied-physics
    name: Journal of Applied Physics
    publisher: aip
    feed_url: https://pubs.aip.org/rss/site_1000029/1000017.xml
  - id: apl-materials
    name: APL Materials
    publisher: aip
    feed_url: https://pubs.aip.org/rss/site_1000013/1000009.xml
  - id: advanced-electronic-materials
    name: Advanced Electronic Materials
    publisher: wiley
    feed_url: "https://advanced.onlinelibrary.wiley.com/action/showFeed?type=etoc&feed=rss&jc=2199160X"
  - id: acta-materialia
    name: Acta Materialia
    publisher: elsevier
    feed_url: https://rss.sciencedirect.com/publication/science/13596454
  - id: science-advances
    name: Science Advances
    publisher: aaas
    feed_url: https://feeds.science.org/rss/science-advances.xml
  - id: nano-micro-letters
    name: Nano-Micro Letters
    publisher: springer
    feed_url: "https://link.springer.com/search.rss?facet-journal-id=40820"
```

- [ ] **Step 4: Update README source counts and publisher list**

Replace the 20-source summary with text that states:

```markdown
当前配置收录 29 本期刊：Nature Portfolio 13 本、American Physical Society 1 本、AIP Publishing 3 本、IEEE 6 本、Wiley 3 本、Elsevier 1 本、AAAS 1 本和 Springer Nature 1 本。完整名称和官方 RSS 地址以 [`feeds.yml`](feeds.yml) 为准。
```

Update the supported-publisher bullet to list all eight publisher codes. Preserve the all-items-first classification explanation and the ACS exclusion from the design; do not add ACS to README's active source list.

- [ ] **Step 5: Run configuration tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py -q
```

Expected: all configuration tests pass.

- [ ] **Step 6: Commit configuration and documentation**

```powershell
git add feeds.yml README.md tests/test_config.py
git commit -m "feat: add nine research RSS sources"
```

### Task 3: Regenerate and verify the 29-source Guide

**Files:**
- Modify: `docs/index.html`
- Modify: `tests/test_release_files.py:153-170`

- [ ] **Step 1: Write the failing generated-release contract**

Change the count assertion in `tests/test_release_files.py` and add explicit feed-link and ACS exclusions:

```python
assert sum(feed.enabled for feed in feeds) == 29
assert "29 SOURCES" in index
assert index.count('class="guide-feed-url"') == 29
assert "ACS Applied Materials &amp; Interfaces" not in index
assert "ACS Applied Materials & Interfaces" not in index
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_release_files.py::test_production_guide_is_generated_from_release_configuration -q
```

Expected: FAIL because `docs/index.html` still contains the 20-source generated Guide.

- [ ] **Step 2: Generate the Guide from release configuration**

```powershell
.\.venv\Scripts\python.exe scripts/render_site_guide.py
```

Expected: the marker region changes and no content outside `<!-- GUIDE:START -->` / `<!-- GUIDE:END -->` changes.

- [ ] **Step 3: Verify generated counts and exclusions**

```powershell
rg -n "29 SOURCES|American Physical Society|Elsevier|AAAS|Springer Nature|ACS Applied" docs/index.html
```

Expected: `29 SOURCES` and all four new publisher labels are present; ACS Applied Materials & Interfaces is absent.

- [ ] **Step 4: Run renderer and static-site tests**

```powershell
.\.venv\Scripts\python.exe scripts/render_site_guide.py --check
.\.venv\Scripts\python.exe -m pytest tests/test_guide.py tests/test_release_files.py tests/test_static_shell.py tests/test_static_shell_behavior.py -q
```

Expected: renderer exits 0 and all tests pass. The production Guide contains 29 feed URLs, 16 native disclosure groups (8 publishers + 8 directions), and 56 precise tag labels.

- [ ] **Step 5: Confirm the generated-only diff**

```powershell
git diff -- docs/index.html
git diff --check
```

Expected: only the Guide marker region differs; no whitespace errors.

- [ ] **Step 6: Commit the generated Guide and its release contract**

```powershell
git add docs/index.html tests/test_release_files.py
git commit -m "docs: publish the 29-source RSS Guide"
```

### Task 4: Run live-feed smoke tests and complete QA

**Files:**
- No tracked file changes expected.

- [ ] **Step 1: Smoke-test all nine official endpoints**

Request every approved URL with the production User-Agent:

```powershell
$urls = @(
  "https://feeds.aps.org/rss/recent/prapplied.xml",
  "https://www.nature.com/natelectron.rss",
  "https://advanced.onlinelibrary.wiley.com/action/showFeed?type=etoc&feed=rss&jc=2199160X",
  "https://pubs.aip.org/rss/site_1000029/1000017.xml",
  "https://pubs.aip.org/rss/site_1000013/1000009.xml",
  "https://www.nature.com/npjcompumats.rss",
  "https://rss.sciencedirect.com/publication/science/13596454",
  "https://feeds.science.org/rss/science-advances.xml",
  "https://link.springer.com/search.rss?facet-journal-id=40820"
)
foreach ($url in $urls) {
  curl.exe --silent --show-error --location --max-time 45 `
    --user-agent "paper-radar/0.1 (+personal academic RSS reader)" `
    --output NUL `
    --write-out "%{http_code}|%{content_type}|%{size_download}|%{url_effective}`n" `
    $url
  if ($LASTEXITCODE -ne 0) { throw "Feed smoke test failed: $url" }
}

$nanoMicroBody = curl.exe --silent --show-error --location --max-time 45 `
  --user-agent "paper-radar/0.1 (+personal academic RSS reader)" `
  "https://link.springer.com/search.rss?facet-journal-id=40820"
if ($LASTEXITCODE -ne 0) { throw "Nano-Micro Letters RSS request failed" }
[xml]$nanoMicroXml = [string]::Join([Environment]::NewLine, $nanoMicroBody)
if (@($nanoMicroXml.rss.channel.item).Count -ne 20) {
  throw "Nano-Micro Letters RSS did not contain exactly 20 recent items"
}
```

Expected: HTTP 200 for every endpoint. The Nano-Micro Letters URL resolves to a valid RSS document with 20 items. Advanced Electronic Materials may be retried once if Wiley is transient; a persistent failure must be reported but does not justify replacing RSS with webpage scraping.

- [ ] **Step 2: Run all Python tests**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: all tests pass with zero failures.

- [ ] **Step 3: Run Node, Ruff, renderer, and workflow checks**

```powershell
node --test tests/web/*.test.mjs
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe scripts/render_site_guide.py --check
.\.venv\Scripts\python.exe -m pytest tests/test_daily_workflow.py tests/test_guide_sync_workflow.py -q
git diff --check
git status --short
```

Expected: 19 Node tests pass; Ruff, renderer, workflow tests, and diff check pass; worktree is clean.

- [ ] **Step 4: Review the complete change against the design**

Inspect:

```powershell
git diff --stat 6919891..HEAD
git diff 6919891..HEAD -- feeds.yml src/paper_radar/config.py src/paper_radar/guide.py README.md tests/test_config.py tests/test_guide.py tests/test_release_files.py
```

Expected: only approved publisher/config/Guide/docs/test changes; no classifier, database schema, scheduling, ACS, or scraping changes.

- [ ] **Step 5: Prepare deployment handoff**

Report exact test counts, endpoint results, commits, and known Wiley risk. Request explicit approval before pushing `main`.

After approval:

```powershell
git push origin main:main
```

Then manually dispatch `Daily RSS Update`, verify the bot commit changes only `docs/data/papers.db`, wait for Pages success, and confirm the live Guide shows 29 RSS links, eight publisher groups, eight research directions, and 56 precise tags.
