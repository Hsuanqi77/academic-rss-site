# Personal Academic RSS Site MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a manually updated, public GitHub Pages site that aggregates the approved Nature, AIP, IEEE, and Wiley journal RSS feeds into SQLite and provides responsive multi-filter browsing.

**Architecture:** A Python package loads declarative feed/topic configuration, parses RSS 1.0/RSS 2.0/Atom, normalizes and enriches records, and writes a validated working SQLite database. A publish gate safely copies that database into a no-build static site under `docs/`, where vendored `sql.js` performs read-only queries in the browser. Scheduled GitHub Actions are deliberately excluded from this MVP and will be a follow-up plan after the manual workflow is stable.

**Tech Stack:** Python 3.11+, `feedparser`, `httpx`, PyYAML, `python-dotenv`, SQLite, pytest, respx, plain HTML/CSS/ES modules, sql.js 1.10.2, Node's built-in test runner, Python Playwright, GitHub Pages.

---

## Scope boundary

This plan delivers one working, testable subsystem: the complete manually updated MVP, including public Pages deployment. It does not add scheduled GitHub Actions, AI translation, accounts, cloud bookmarks, or publisher full-text scraping. Those remain separate future projects.

## File responsibility map

| Path | Responsibility |
| --- | --- |
| `feeds.yml` | Approved journal registry and official RSS URLs |
| `topics.yml` | User-editable keyword topic rules |
| `pyproject.toml` | Python package, runtime dependencies, development dependencies, CLI entry point |
| `src/paper_radar/config.py` | Load and validate YAML configuration |
| `src/paper_radar/models.py` | Shared immutable data contracts |
| `src/paper_radar/feeds.py` | Conditional HTTP retrieval and RSS/Atom parsing |
| `src/paper_radar/http_client.py` | Per-domain request pacing for publisher and metadata services |
| `src/paper_radar/normalize.py` | Field cleanup, dates, article types, stable IDs |
| `src/paper_radar/schema.sql` | SQLite schema version 1 |
| `src/paper_radar/database.py` | Connections, migrations, repositories, run logging |
| `src/paper_radar/enrich.py` | Crossref and optional Unpaywall metadata enrichment |
| `src/paper_radar/classify.py` | Rule-driven topic assignment |
| `src/paper_radar/pipeline.py` | Per-feed failure isolation and update orchestration |
| `src/paper_radar/validation.py` | Integrity checks and safe publication gate |
| `src/paper_radar/cli.py` | `fetch`, `validate`, `publish`, and `update` commands |
| `scripts/update.ps1` | Beginner-friendly Windows entry point |
| `docs/index.html` | Static application shell and accessible controls |
| `docs/styles.css` | Desktop sidebar, mobile drawer, article cards, status states |
| `docs/js/db.js` | sql.js loading, filter option queries, article query builder |
| `docs/js/state.js` | URL query-string serialization and restoration |
| `docs/js/app.js` | Event binding, rendering, pagination, filter drawer |
| `tests/fixtures/` | Deterministic RSS samples and site database fixtures |
| `tests/e2e/` | Browser tests against a generated fixture site |
| `README.md` | Setup, manual update, local preview, GitHub publishing, adding journals |

### Task 1: Bootstrap the Python package and validate configuration

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `feeds.yml`
- Create: `topics.yml`
- Create: `src/paper_radar/__init__.py`
- Create: `src/paper_radar/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Create the package metadata and development environment contract**

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "paper-radar"
version = "0.1.0"
description = "Personal multi-publisher academic RSS tracker"
requires-python = ">=3.11"
dependencies = [
  "feedparser>=6.0,<7",
  "httpx>=0.27,<1",
  "PyYAML>=6,<7",
  "python-dotenv>=1,<2",
]

[project.optional-dependencies]
dev = [
  "pytest>=8,<9",
  "pytest-playwright>=0.5,<1",
  "respx>=0.21,<1",
  "ruff>=0.6,<1",
]

[project.scripts]
paper-radar = "paper_radar.cli:main"

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.package-data]
paper_radar = ["schema.sql"]

[tool.pytest.ini_options]
addopts = "-q"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"
```

```dotenv
# .env.example
# Optional. Unpaywall requires a contact email but no API key.
UNPAYWALL_EMAIL=
```

```python
# src/paper_radar/__init__.py
"""Paper Radar package."""

__version__ = "0.1.0"
```

- [ ] **Step 2: Create the virtual environment and install dependencies**

Run:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
node --version
npm --version
```

Expected: Python installation finishes successfully, Node reports version 20 or newer, and npm reports its version. If `node` is unavailable, install the current Node.js LTS release with `winget install --id OpenJS.NodeJS.LTS --exact`, reopen PowerShell, and repeat the two version checks. `paper-radar --help` becomes available through `.\.venv\Scripts\paper-radar.exe` after the CLI task is implemented.

- [ ] **Step 3: Write failing configuration tests**

```python
# tests/test_config.py
from pathlib import Path

import pytest

from paper_radar.config import ConfigError, load_feeds, load_topics


def test_load_feeds_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "feeds.yml"
    path.write_text(
        """
feeds:
  - id: apl
    name: Applied Physics Letters
    publisher: aip
    feed_url: https://example.org/apl.xml
  - id: apl
    name: Duplicate
    publisher: aip
    feed_url: https://example.org/duplicate.xml
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="duplicate feed id: apl"):
        load_feeds(path)


def test_load_feeds_accepts_https_and_known_publishers(tmp_path: Path) -> None:
    path = tmp_path / "feeds.yml"
    path.write_text(
        """
feeds:
  - id: ieee-ultrasonics
    name: IEEE Transactions on Ultrasonics
    publisher: ieee
    feed_url: https://ieeexplore.ieee.org/rss/TOC11073821.XML
    enabled: true
""",
        encoding="utf-8",
    )
    feed = load_feeds(path)[0]
    assert feed.id == "ieee-ultrasonics"
    assert feed.publisher == "ieee"
    assert feed.enabled is True


def test_load_topics_requires_keywords(tmp_path: Path) -> None:
    path = tmp_path / "topics.yml"
    path.write_text("topics:\n  - id: saw\n    label: SAW\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="topic saw must define keywords"):
        load_topics(path)
```

- [ ] **Step 4: Run the tests and confirm the expected failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_config.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'paper_radar.config'`.

- [ ] **Step 5: Implement strict configuration loading**

```python
# src/paper_radar/config.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

VALID_PUBLISHERS = {"nature", "aip", "ieee", "wiley"}


class ConfigError(ValueError):
    """Raised when a user-editable configuration file is invalid."""


@dataclass(frozen=True, slots=True)
class FeedConfig:
    id: str
    name: str
    publisher: str
    feed_url: str
    enabled: bool = True
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TopicConfig:
    id: str
    label: str
    keywords: tuple[str, ...]


def _read_mapping(path: Path) -> dict:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")
    return value


def load_feeds(path: Path) -> list[FeedConfig]:
    rows = _read_mapping(path).get("feeds")
    if not isinstance(rows, list) or not rows:
        raise ConfigError("feeds.yml must contain a non-empty feeds list")
    result: list[FeedConfig] = []
    ids: set[str] = set()
    urls: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ConfigError("each feed must be a mapping")
        feed_id = str(row.get("id", "")).strip()
        name = str(row.get("name", "")).strip()
        publisher = str(row.get("publisher", "")).strip().lower()
        feed_url = str(row.get("feed_url", "")).strip()
        if not feed_id or not name or not feed_url:
            raise ConfigError("feed id, name, and feed_url are required")
        if feed_id in ids:
            raise ConfigError(f"duplicate feed id: {feed_id}")
        if feed_url in urls:
            raise ConfigError(f"duplicate feed url: {feed_url}")
        if publisher not in VALID_PUBLISHERS:
            raise ConfigError(f"unknown publisher for {feed_id}: {publisher}")
        if urlparse(feed_url).scheme != "https":
            raise ConfigError(f"feed {feed_id} must use https")
        ids.add(feed_id)
        urls.add(feed_url)
        result.append(
            FeedConfig(
                id=feed_id,
                name=name,
                publisher=publisher,
                feed_url=feed_url,
                enabled=bool(row.get("enabled", True)),
                aliases=tuple(str(item).strip() for item in row.get("aliases", [])),
            )
        )
    return result


def load_topics(path: Path) -> list[TopicConfig]:
    rows = _read_mapping(path).get("topics")
    if not isinstance(rows, list) or not rows:
        raise ConfigError("topics.yml must contain a non-empty topics list")
    result: list[TopicConfig] = []
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ConfigError("each topic must be a mapping")
        topic_id = str(row.get("id", "")).strip()
        label = str(row.get("label", "")).strip()
        keywords = tuple(str(item).strip() for item in row.get("keywords", []) if str(item).strip())
        if not topic_id or not label:
            raise ConfigError("topic id and label are required")
        if topic_id in ids:
            raise ConfigError(f"duplicate topic id: {topic_id}")
        if not keywords:
            raise ConfigError(f"topic {topic_id} must define keywords")
        ids.add(topic_id)
        result.append(TopicConfig(topic_id, label, keywords))
    return result
```

- [ ] **Step 6: Add the complete approved feed registry and initial topics**

```yaml
# feeds.yml
feeds:
  - {id: nature-communications, name: Nature Communications, publisher: nature, feed_url: "https://www.nature.com/ncomms.rss"}
  - {id: nature-biotechnology, name: Nature Biotechnology, publisher: nature, feed_url: "https://www.nature.com/nbt.rss"}
  - {id: nature-methods, name: Nature Methods, publisher: nature, feed_url: "https://www.nature.com/nmeth.rss"}
  - {id: nature, name: Nature, publisher: nature, feed_url: "https://www.nature.com/nature.rss"}
  - {id: nature-cancer, name: Nature Cancer, publisher: nature, feed_url: "https://www.nature.com/natcancer.rss"}
  - {id: nature-machine-intelligence, name: Nature Machine Intelligence, publisher: nature, feed_url: "https://www.nature.com/natmachintell.rss"}
  - {id: nature-computational-science, name: Nature Computational Science, publisher: nature, feed_url: "https://www.nature.com/natcomputsci.rss"}
  - {id: nature-reviews-molecular-cell-biology, name: Nature Reviews Molecular Cell Biology, publisher: nature, feed_url: "https://www.nature.com/nrm.rss"}
  - {id: nature-reviews-genetics, name: Nature Reviews Genetics, publisher: nature, feed_url: "https://www.nature.com/nrg.rss"}
  - {id: nature-reviews-cancer, name: Nature Reviews Cancer, publisher: nature, feed_url: "https://www.nature.com/nrc.rss"}
  - {id: microsystems-nanoengineering, name: Microsystems & Nanoengineering, publisher: nature, feed_url: "https://www.nature.com/micronano.rss"}
  - {id: applied-physics-letters, name: Applied Physics Letters, publisher: aip, feed_url: "https://pubs.aip.org/rss/site_1000017/1000011.xml"}
  - {id: ieee-transactions-ultrasonics, name: IEEE Transactions on Ultrasonics, publisher: ieee, feed_url: "https://ieeexplore.ieee.org/rss/TOC11073821.XML"}
  - {id: ieee-transactions-mtt, name: IEEE Transactions on Microwave Theory and Techniques, publisher: ieee, feed_url: "https://ieeexplore.ieee.org/rss/TOC22.XML"}
  - {id: ieee-microwave-wireless-technology-letters, name: IEEE Microwave and Wireless Technology Letters, publisher: ieee, feed_url: "https://ieeexplore.ieee.org/rss/TOC9944983.XML"}
  - {id: ieee-transactions-electron-devices, name: IEEE Transactions on Electron Devices, publisher: ieee, feed_url: "https://ieeexplore.ieee.org/rss/TOC16.XML"}
  - {id: ieee-electron-device-letters, name: IEEE Electron Device Letters, publisher: ieee, feed_url: "https://ieeexplore.ieee.org/rss/TOC55.XML"}
  - {id: journal-microelectromechanical-systems, name: Journal of Microelectromechanical Systems, publisher: ieee, feed_url: "https://ieeexplore.ieee.org/rss/TOC84.XML"}
  - {id: advanced-materials, name: Advanced Materials, publisher: wiley, feed_url: "https://advanced.onlinelibrary.wiley.com/action/showFeed?type=etoc&feed=rss&jc=15214095"}
  - {id: advanced-functional-materials, name: Advanced Functional Materials, publisher: wiley, feed_url: "https://advanced.onlinelibrary.wiley.com/action/showFeed?type=etoc&feed=rss&jc=16163028"}
```

```yaml
# topics.yml
topics:
  - {id: baw, label: BAW, keywords: ["bulk acoustic wave", "BAW"]}
  - {id: saw, label: SAW, keywords: ["surface acoustic wave", "SAW"]}
  - {id: fbar, label: FBAR, keywords: ["film bulk acoustic resonator", "FBAR"]}
  - {id: mems, label: MEMS, keywords: ["microelectromechanical", "MEMS"]}
  - {id: aln, label: AlN, keywords: ["aluminum nitride", "aluminium nitride", "AlN"]}
  - {id: alscn, label: AlScN, keywords: ["aluminum scandium nitride", "aluminium scandium nitride", "AlScN"]}
  - {id: piezoelectric, label: Piezoelectric, keywords: ["piezoelectric", "piezoelectricity"]}
  - {id: ultrasound, label: Ultrasound, keywords: ["ultrasound", "ultrasonic"]}
  - {id: acoustic-resonator, label: Acoustic resonator, keywords: ["acoustic resonator", "acoustic filter"]}
  - {id: microwave, label: Microwave, keywords: ["microwave", "millimeter wave", "millimetre wave"]}
  - {id: rf, label: RF, keywords: ["radio frequency", "radio-frequency", "RF"]}
  - {id: ferroelectric, label: Ferroelectric, keywords: ["ferroelectric", "ferroelectricity"]}
  - {id: semiconductor, label: Semiconductor, keywords: ["semiconductor", "semiconducting"]}
  - {id: electron-device, label: Electron device, keywords: ["electron device", "transistor", "diode"]}
```

- [ ] **Step 7: Run the tests and commit the configuration layer**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_config.py -v
.\.venv\Scripts\python.exe -m ruff check src tests
git add pyproject.toml .env.example feeds.yml topics.yml src/paper_radar tests/test_config.py
git commit -m "feat: add validated feed and topic configuration"
```

Expected: three tests pass, Ruff reports no errors, and the commit is created.

### Task 2: Create the versioned SQLite schema

**Files:**
- Create: `src/paper_radar/schema.sql`
- Create: `src/paper_radar/database.py`
- Test: `tests/test_database_schema.py`

- [ ] **Step 1: Write the failing schema test**

```python
# tests/test_database_schema.py
from pathlib import Path

from paper_radar.database import connect_database, initialize_database


def test_initialize_database_creates_version_one_schema(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "papers.db")
    initialize_database(connection)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert tables == {"journals", "articles", "tags", "article_tags", "runs_log"}
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_database_schema.py -v
```

Expected: import fails because `paper_radar.database` does not exist.

- [ ] **Step 3: Add the complete schema**

```sql
-- src/paper_radar/schema.sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS journals (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    publisher TEXT NOT NULL CHECK (publisher IN ('nature', 'aip', 'ieee', 'wiley')),
    feed_url TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    etag TEXT,
    last_modified TEXT,
    last_checked_at TEXT,
    last_success_at TEXT,
    last_status TEXT NOT NULL DEFAULT 'never',
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS articles (
    uid TEXT PRIMARY KEY,
    doi TEXT,
    journal_id TEXT NOT NULL REFERENCES journals(id),
    title TEXT NOT NULL,
    abstract TEXT,
    authors_json TEXT NOT NULL DEFAULT '[]',
    published_at TEXT,
    article_type TEXT NOT NULL DEFAULT 'other'
        CHECK (article_type IN ('research', 'review', 'editorial', 'correction', 'other')),
    article_url TEXT NOT NULL,
    normalized_url TEXT,
    oa_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (oa_status IN ('open', 'closed', 'unknown')),
    source_feed_url TEXT NOT NULL,
    metadata_status TEXT NOT NULL DEFAULT 'rss_only'
        CHECK (metadata_status IN ('rss_only', 'enriched', 'partial')),
    first_seen_at TEXT NOT NULL,
    last_updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_doi
    ON articles(doi) WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_normalized_url
    ON articles(normalized_url) WHERE normalized_url IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_journal_id ON articles(journal_id);
CREATE INDEX IF NOT EXISTS idx_articles_type ON articles(article_type);
CREATE INDEX IF NOT EXISTS idx_articles_oa ON articles(oa_status);

CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS article_tags (
    article_uid TEXT NOT NULL REFERENCES articles(uid) ON DELETE CASCADE,
    tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (article_uid, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_article_tags_tag ON article_tags(tag_id, article_uid);

CREATE TABLE IF NOT EXISTS runs_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'ok', 'partial', 'error')),
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT ''
);

PRAGMA user_version = 1;
```

- [ ] **Step 4: Implement connection and schema initialization**

```python
# src/paper_radar/database.py
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, 1):
        raise RuntimeError(f"unsupported database version: {version}")
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    connection.commit()
```

- [ ] **Step 5: Run the schema test and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_database_schema.py -v
git add src/paper_radar/schema.sql src/paper_radar/database.py tests/test_database_schema.py
git commit -m "feat: add versioned SQLite schema"
```

Expected: the schema test passes and the commit is created.

### Task 3: Parse RSS 1.0, RSS 2.0, and Atom feeds

**Files:**
- Create: `src/paper_radar/models.py`
- Create: `src/paper_radar/feeds.py`
- Create: `tests/fixtures/rss1.xml`
- Create: `tests/fixtures/rss2.xml`
- Create: `tests/fixtures/atom.xml`
- Test: `tests/test_feeds.py`

- [ ] **Step 1: Add deterministic feed fixtures**

```xml
<!-- tests/fixtures/rss1.xml -->
<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">
  <item rdf:about="https://example.org/nature-paper">
    <title>Nature fixture paper</title><link>https://example.org/nature-paper</link>
    <dc:date>2026-07-01</dc:date><prism:doi>10.1000/nature</prism:doi>
  </item>
</rdf:RDF>
```

```xml
<!-- tests/fixtures/rss2.xml -->
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>IEEE fixture</title><item>
  <title>IEEE fixture paper</title><link>https://example.org/ieee-paper</link>
  <guid>doi:10.1000/ieee</guid><pubDate>Thu, 02 Jul 2026 08:00:00 GMT</pubDate>
  <description>Ultrasonic fixture abstract.</description><author>Ada Example</author>
</item></channel></rss>
```

```xml
<!-- tests/fixtures/atom.xml -->
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Wiley fixture</title><entry>
  <title>Wiley fixture paper</title><id>https://doi.org/10.1000/wiley</id>
  <link rel="alternate" href="https://example.org/wiley-paper" />
  <updated>2026-07-03T09:00:00Z</updated><summary>Materials fixture abstract.</summary>
  <author><name>Grace Example</name></author>
</entry></feed>
```

- [ ] **Step 2: Write failing parser and conditional request tests**

```python
# tests/test_feeds.py
from pathlib import Path

import httpx
import pytest
import respx

from paper_radar.config import FeedConfig
from paper_radar.feeds import fetch_feed, parse_feed_bytes

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("filename", "expected_doi"),
    [("rss1.xml", "10.1000/nature"), ("rss2.xml", "10.1000/ieee"), ("atom.xml", "10.1000/wiley")],
)
def test_parse_supported_feed_formats(filename: str, expected_doi: str) -> None:
    feed = FeedConfig("fixture", "Fixture", "nature", "https://example.org/feed.xml")
    items = parse_feed_bytes((FIXTURES / filename).read_bytes(), feed)
    assert len(items) == 1
    assert items[0].doi == expected_doi
    assert items[0].title.endswith("fixture paper")


@respx.mock
def test_fetch_feed_sends_conditional_headers() -> None:
    route = respx.get("https://example.org/feed.xml").mock(
        return_value=httpx.Response(304, headers={"etag": '"same"'})
    )
    feed = FeedConfig("fixture", "Fixture", "nature", "https://example.org/feed.xml")
    with httpx.Client() as client:
        result = fetch_feed(client, feed, etag='"same"', last_modified="yesterday")
    assert result.not_modified is True
    assert route.calls[0].request.headers["if-none-match"] == '"same"'
    assert route.calls[0].request.headers["if-modified-since"] == "yesterday"
```

- [ ] **Step 3: Run the tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_feeds.py -v
```

Expected: import fails because `paper_radar.feeds` and `paper_radar.models` do not exist.

- [ ] **Step 4: Define raw feed contracts**

```python
# src/paper_radar/models.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RawFeedItem:
    feed_id: str
    feed_url: str
    title: str
    link: str
    published: str | None
    doi: str | None
    authors: tuple[str, ...]
    summary: str | None
    raw_type: str | None


@dataclass(frozen=True, slots=True)
class FeedFetchResult:
    content: bytes | None
    etag: str | None
    last_modified: str | None
    not_modified: bool
```

- [ ] **Step 5: Implement HTTP retrieval and format-neutral parsing**

```python
# src/paper_radar/feeds.py
from __future__ import annotations

import re

import feedparser
import httpx

from paper_radar.config import FeedConfig
from paper_radar.models import FeedFetchResult, RawFeedItem

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[^\s<>\"']+", re.IGNORECASE)
USER_AGENT = "paper-radar/0.1 (+personal academic RSS reader)"


class FeedParseError(ValueError):
    pass


def fetch_feed(
    client: httpx.Client,
    feed: FeedConfig,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FeedFetchResult:
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    response = client.get(feed.feed_url, headers=headers, timeout=25.0, follow_redirects=True)
    if response.status_code == 304:
        return FeedFetchResult(None, response.headers.get("etag", etag), last_modified, True)
    response.raise_for_status()
    return FeedFetchResult(
        response.content,
        response.headers.get("etag"),
        response.headers.get("last-modified"),
        False,
    )


def _extract_doi(entry: dict) -> str | None:
    candidates = [
        entry.get("prism_doi"),
        entry.get("dc_identifier"),
        entry.get("id"),
        entry.get("guid"),
        entry.get("summary"),
        entry.get("link"),
    ]
    for candidate in candidates:
        match = DOI_PATTERN.search(str(candidate or ""))
        if match:
            return match.group(0).rstrip(".,;)")
    return None


def parse_feed_bytes(content: bytes, feed: FeedConfig) -> list[RawFeedItem]:
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise FeedParseError(str(parsed.bozo_exception))
    items: list[RawFeedItem] = []
    for entry in parsed.entries:
        title = str(entry.get("title", "")).strip()
        link = str(entry.get("link", "")).strip()
        if not title or not link:
            continue
        authors = tuple(
            str(author.get("name", "")).strip()
            for author in entry.get("authors", [])
            if str(author.get("name", "")).strip()
        )
        if not authors and entry.get("author"):
            authors = (str(entry.author).strip(),)
        items.append(
            RawFeedItem(
                feed_id=feed.id,
                feed_url=feed.feed_url,
                title=title,
                link=link,
                published=entry.get("published") or entry.get("updated") or entry.get("dc_date"),
                doi=_extract_doi(entry),
                authors=authors,
                summary=entry.get("summary") or entry.get("description"),
                raw_type=entry.get("prism_section") or entry.get("type"),
            )
        )
    return items
```

- [ ] **Step 6: Run parser tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_feeds.py -v
git add src/paper_radar/models.py src/paper_radar/feeds.py tests/fixtures tests/test_feeds.py
git commit -m "feat: parse RSS and Atom feeds"
```

Expected: all parser and conditional request tests pass.

### Task 4: Normalize metadata and generate stable article IDs

**Files:**
- Modify: `src/paper_radar/models.py`
- Create: `src/paper_radar/normalize.py`
- Test: `tests/test_normalize.py`

- [ ] **Step 1: Write failing normalization tests**

```python
# tests/test_normalize.py
from paper_radar.config import FeedConfig
from paper_radar.models import RawFeedItem
from paper_radar.normalize import normalize_doi, normalize_item, normalize_url


def test_normalize_doi_removes_resolver_and_punctuation() -> None:
    assert normalize_doi("https://doi.org/10.1000/ABC.") == "10.1000/abc"


def test_normalize_url_removes_tracking_and_trailing_slash() -> None:
    assert normalize_url("https://example.org/paper/?utm_source=rss&x=1") == "https://example.org/paper?x=1"


def test_uid_prefers_doi_and_type_is_normalized() -> None:
    feed = FeedConfig("apl", "Applied Physics Letters", "aip", "https://example.org/feed")
    raw = RawFeedItem(
        "apl", feed.feed_url, "  A&nbsp;paper  ", "https://example.org/paper", "2026-07-01",
        "doi:10.1000/ABC", ("Ada",), "An abstract", "Research Article",
    )
    article = normalize_item(raw, feed)
    assert article.uid == "doi:10.1000/abc"
    assert article.title == "A paper"
    assert article.article_type == "research"
    assert article.published_at == "2026-07-01T00:00:00+00:00"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_normalize.py -v
```

Expected: import fails because `paper_radar.normalize` and `ArticleRecord` do not exist.

- [ ] **Step 3: Extend the shared models with the normalized record**

Append to `src/paper_radar/models.py`:

```python
@dataclass(frozen=True, slots=True)
class ArticleRecord:
    uid: str
    doi: str | None
    journal_id: str
    title: str
    abstract: str | None
    authors: tuple[str, ...]
    published_at: str | None
    article_type: str
    article_url: str
    normalized_url: str | None
    oa_status: str
    source_feed_url: str
    metadata_status: str
```

- [ ] **Step 4: Implement normalization and stable ID generation**

```python
# src/paper_radar/normalize.py
from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from paper_radar.config import FeedConfig
from paper_radar.models import ArticleRecord, RawFeedItem

SPACE_PATTERN = re.compile(r"\s+")
TRACKING_KEYS = {"fbclid", "gclid", "spm"}


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = SPACE_PATTERN.sub(" ", html.unescape(value)).strip()
    return cleaned or None


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    doi = value.strip()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    return doi.rstrip(".,;)").lower() or None


def normalize_url(value: str | None) -> str | None:
    if not value:
        return None
    parts = urlsplit(value.strip())
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def normalize_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def normalize_article_type(value: str | None) -> str:
    lowered = (value or "").lower()
    if "review" in lowered:
        return "review"
    if "editorial" in lowered or "comment" in lowered or "perspective" in lowered:
        return "editorial"
    if "correction" in lowered or "erratum" in lowered or "corrigendum" in lowered:
        return "correction"
    if "article" in lowered or "letter" in lowered or "research" in lowered:
        return "research"
    return "other"


def make_uid(doi: str | None, normalized_url: str | None, journal_id: str, title: str, published: str | None) -> str:
    if doi:
        return f"doi:{doi}"
    if normalized_url:
        digest = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:24]
        return f"url:{digest}"
    payload = "|".join((journal_id, title.casefold(), published or ""))
    return f"hash:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def normalize_item(item: RawFeedItem, feed: FeedConfig) -> ArticleRecord:
    title = clean_text(item.title) or "Untitled"
    doi = normalize_doi(item.doi)
    article_url = item.link.strip()
    normalized_url = normalize_url(article_url)
    published_at = normalize_date(item.published)
    return ArticleRecord(
        uid=make_uid(doi, normalized_url, feed.id, title, published_at),
        doi=doi,
        journal_id=feed.id,
        title=title,
        abstract=clean_text(item.summary),
        authors=tuple(clean_text(author) for author in item.authors if clean_text(author)),
        published_at=published_at,
        article_type=normalize_article_type(item.raw_type),
        article_url=article_url,
        normalized_url=normalized_url,
        oa_status="unknown",
        source_feed_url=feed.feed_url,
        metadata_status="rss_only",
    )
```

- [ ] **Step 5: Run normalization tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_normalize.py -v
git add src/paper_radar/models.py src/paper_radar/normalize.py tests/test_normalize.py
git commit -m "feat: normalize article metadata and IDs"
```

Expected: all normalization tests pass.

### Task 5: Persist journals, articles, tags, and run summaries

**Files:**
- Modify: `src/paper_radar/database.py`
- Test: `tests/test_database_repository.py`

- [ ] **Step 1: Write failing repository tests**

```python
# tests/test_database_repository.py
from pathlib import Path

from paper_radar.config import FeedConfig, TopicConfig
from paper_radar.database import (
    connect_database,
    initialize_database,
    register_journal,
    replace_article_tags,
    upsert_article,
)
from paper_radar.models import ArticleRecord


def make_article(title: str = "First title") -> ArticleRecord:
    return ArticleRecord(
        uid="doi:10.1000/test",
        doi="10.1000/test",
        journal_id="apl",
        title=title,
        abstract="Abstract",
        authors=("Ada",),
        published_at="2026-07-01T00:00:00+00:00",
        article_type="research",
        article_url="https://example.org/test",
        normalized_url="https://example.org/test",
        oa_status="unknown",
        source_feed_url="https://example.org/feed",
        metadata_status="rss_only",
    )


def test_upsert_updates_without_creating_duplicates(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "papers.db")
    initialize_database(connection)
    register_journal(connection, FeedConfig("apl", "APL", "aip", "https://example.org/feed"))
    assert upsert_article(connection, make_article()) == "inserted"
    assert upsert_article(connection, make_article("Corrected title")) == "updated"
    assert connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1
    assert connection.execute("SELECT title FROM articles").fetchone()[0] == "Corrected title"


def test_replace_article_tags_is_idempotent(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "papers.db")
    initialize_database(connection)
    register_journal(connection, FeedConfig("apl", "APL", "aip", "https://example.org/feed"))
    upsert_article(connection, make_article())
    topics = [TopicConfig("saw", "SAW", ("surface acoustic wave",))]
    replace_article_tags(connection, "doi:10.1000/test", topics)
    replace_article_tags(connection, "doi:10.1000/test", topics)
    assert connection.execute("SELECT COUNT(*) FROM article_tags").fetchone()[0] == 1
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_database_repository.py -v
```

Expected: import fails for the undefined repository functions.

- [ ] **Step 3: Add journal state and article repository functions**

Append to `src/paper_radar/database.py`:

```python
import json
from datetime import datetime, timezone

from paper_radar.config import FeedConfig, TopicConfig
from paper_radar.models import ArticleRecord


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def register_journal(connection: sqlite3.Connection, feed: FeedConfig) -> None:
    connection.execute(
        """
        INSERT INTO journals(id, name, publisher, feed_url, enabled)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name, publisher=excluded.publisher,
          feed_url=excluded.feed_url, enabled=excluded.enabled
        """,
        (feed.id, feed.name, feed.publisher, feed.feed_url, int(feed.enabled)),
    )
    connection.commit()


def get_feed_state(connection: sqlite3.Connection, feed_id: str) -> tuple[str | None, str | None]:
    row = connection.execute(
        "SELECT etag, last_modified FROM journals WHERE id=?", (feed_id,)
    ).fetchone()
    return (row["etag"], row["last_modified"]) if row else (None, None)


def mark_journal_status(
    connection: sqlite3.Connection,
    feed_id: str,
    *,
    status: str,
    error: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
) -> None:
    now = utc_now()
    connection.execute(
        """
        UPDATE journals SET etag=COALESCE(?, etag), last_modified=COALESCE(?, last_modified),
          last_checked_at=?, last_success_at=CASE WHEN ?='ok' THEN ? ELSE last_success_at END,
          last_status=?, last_error=? WHERE id=?
        """,
        (etag, last_modified, now, status, now, status, error, feed_id),
    )
    connection.commit()


ARTICLE_COLUMNS = (
    "doi", "journal_id", "title", "abstract", "authors_json", "published_at",
    "article_type", "article_url", "normalized_url", "oa_status", "source_feed_url",
    "metadata_status",
)


def _article_values(article: ArticleRecord) -> tuple:
    return (
        article.doi,
        article.journal_id,
        article.title,
        article.abstract,
        json.dumps(article.authors, ensure_ascii=False),
        article.published_at,
        article.article_type,
        article.article_url,
        article.normalized_url,
        article.oa_status,
        article.source_feed_url,
        article.metadata_status,
    )


def upsert_article(connection: sqlite3.Connection, article: ArticleRecord) -> str:
    existing = connection.execute(
        f"SELECT {', '.join(ARTICLE_COLUMNS)} FROM articles WHERE uid=?", (article.uid,)
    ).fetchone()
    values = _article_values(article)
    now = utc_now()
    if existing is None:
        connection.execute(
            """
            INSERT INTO articles(
              uid, doi, journal_id, title, abstract, authors_json, published_at, article_type,
              article_url, normalized_url, oa_status, source_feed_url, metadata_status,
              first_seen_at, last_updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (article.uid, *values, now, now),
        )
        connection.commit()
        return "inserted"
    if tuple(existing[column] for column in ARTICLE_COLUMNS) == values:
        return "skipped"
    assignments = ", ".join(f"{column}=?" for column in ARTICLE_COLUMNS)
    connection.execute(
        f"UPDATE articles SET {assignments}, last_updated_at=? WHERE uid=?",
        (*values, now, article.uid),
    )
    connection.commit()
    return "updated"


def replace_article_tags(
    connection: sqlite3.Connection,
    article_uid: str,
    topics: list[TopicConfig],
) -> None:
    connection.execute("DELETE FROM article_tags WHERE article_uid=?", (article_uid,))
    for topic in topics:
        connection.execute(
            "INSERT INTO tags(id, label) VALUES(?, ?) ON CONFLICT(id) DO UPDATE SET label=excluded.label",
            (topic.id, topic.label),
        )
        connection.execute(
            "INSERT OR IGNORE INTO article_tags(article_uid, tag_id) VALUES(?, ?)",
            (article_uid, topic.id),
        )
    connection.commit()


def create_run(connection: sqlite3.Connection) -> int:
    cursor = connection.execute(
        "INSERT INTO runs_log(started_at, status) VALUES(?, 'running')", (utc_now(),)
    )
    connection.commit()
    return int(cursor.lastrowid)


def finish_run(
    connection: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    inserted: int,
    updated: int,
    skipped: int,
    failed: int,
    notes: str,
) -> None:
    connection.execute(
        """
        UPDATE runs_log SET finished_at=?, status=?, inserted_count=?, updated_count=?,
          skipped_count=?, failed_count=?, notes=? WHERE id=?
        """,
        (utc_now(), status, inserted, updated, skipped, failed, notes, run_id),
    )
    connection.commit()
```

- [ ] **Step 4: Run repository tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_database_repository.py -v
git add src/paper_radar/database.py tests/test_database_repository.py
git commit -m "feat: persist articles tags and run state"
```

Expected: both repository tests pass.

### Task 6: Add topic classification and optional metadata enrichment

**Files:**
- Create: `src/paper_radar/classify.py`
- Create: `src/paper_radar/enrich.py`
- Test: `tests/test_classify.py`
- Test: `tests/test_enrich.py`

- [ ] **Step 1: Write failing topic classification tests**

```python
# tests/test_classify.py
from paper_radar.classify import classify_article
from paper_radar.config import TopicConfig
from paper_radar.models import ArticleRecord


def test_classify_matches_phrases_without_matching_substrings() -> None:
    article = ArticleRecord(
        "uid", None, "journal", "An AlScN surface acoustic wave resonator", None, (), None,
        "research", "https://example.org", "https://example.org", "unknown",
        "https://example.org/feed", "rss_only",
    )
    topics = [
        TopicConfig("saw", "SAW", ("surface acoustic wave", "SAW")),
        TopicConfig("rf", "RF", ("RF",)),
    ]
    assert [topic.id for topic in classify_article(article, topics)] == ["saw"]
```

- [ ] **Step 2: Write failing Crossref and Unpaywall tests**

```python
# tests/test_enrich.py
import httpx
import respx

from paper_radar.enrich import enrich_article
from paper_radar.models import ArticleRecord


def article() -> ArticleRecord:
    return ArticleRecord(
        "doi:10.1000/test", "10.1000/test", "apl", "RSS title", None, (), None,
        "other", "https://example.org", "https://example.org", "unknown",
        "https://example.org/feed", "rss_only",
    )


@respx.mock
def test_enrich_uses_crossref_and_unpaywall() -> None:
    respx.get("https://api.crossref.org/works/10.1000%2Ftest").mock(
        return_value=httpx.Response(200, json={"message": {
            "title": ["Crossref title"], "author": [{"given": "Ada", "family": "Example"}],
            "abstract": "Crossref abstract", "type": "journal-article"
        }})
    )
    respx.get("https://api.unpaywall.org/v2/10.1000%2Ftest", params={"email": "me@example.org"}).mock(
        return_value=httpx.Response(200, json={"is_oa": True})
    )
    with httpx.Client() as client:
        result = enrich_article(client, article(), unpaywall_email="me@example.org")
    assert result.title == "RSS title"
    assert result.authors == ("Ada Example",)
    assert result.oa_status == "open"
    assert result.metadata_status == "enriched"


@respx.mock
def test_enrichment_failure_keeps_rss_record() -> None:
    respx.get("https://api.crossref.org/works/10.1000%2Ftest").mock(return_value=httpx.Response(503))
    with httpx.Client() as client:
        result = enrich_article(client, article())
    assert result.title == "RSS title"
    assert result.metadata_status == "partial"
```

- [ ] **Step 3: Run the tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_classify.py tests\test_enrich.py -v
```

Expected: imports fail because both modules are missing.

- [ ] **Step 4: Implement deterministic keyword classification**

```python
# src/paper_radar/classify.py
from __future__ import annotations

import re

from paper_radar.config import TopicConfig
from paper_radar.models import ArticleRecord


def classify_article(article: ArticleRecord, topics: list[TopicConfig]) -> list[TopicConfig]:
    haystack = f"{article.title}\n{article.abstract or ''}".casefold()
    matches: list[TopicConfig] = []
    for topic in topics:
        if any(
            re.search(rf"(?<!\w){re.escape(keyword.casefold())}(?!\w)", haystack)
            for keyword in topic.keywords
        ):
            matches.append(topic)
    return matches
```

- [ ] **Step 5: Implement graceful Crossref and Unpaywall enrichment**

```python
# src/paper_radar/enrich.py
from __future__ import annotations

import re
from dataclasses import replace
from urllib.parse import quote

import httpx

from paper_radar.models import ArticleRecord
from paper_radar.normalize import clean_text, normalize_article_type


def _plain_abstract(value: str | None) -> str | None:
    if not value:
        return None
    return clean_text(re.sub(r"<[^>]+>", " ", value))


def enrich_article(
    client: httpx.Client,
    article: ArticleRecord,
    *,
    unpaywall_email: str | None = None,
) -> ArticleRecord:
    if not article.doi:
        return article
    encoded = quote(article.doi, safe="")
    authors = article.authors
    abstract = article.abstract
    article_type = article.article_type
    oa_status = article.oa_status
    had_success = False
    had_failure = False
    try:
        response = client.get(f"https://api.crossref.org/works/{encoded}", timeout=20.0)
        response.raise_for_status()
        message = response.json()["message"]
        if not authors:
            authors = tuple(
                " ".join(part for part in (row.get("given"), row.get("family")) if part)
                for row in message.get("author", [])
            )
        abstract = abstract or _plain_abstract(message.get("abstract"))
        if article_type == "other":
            article_type = normalize_article_type(message.get("type"))
        had_success = True
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        had_failure = True
    if unpaywall_email:
        try:
            response = client.get(
                f"https://api.unpaywall.org/v2/{encoded}",
                params={"email": unpaywall_email},
                timeout=20.0,
            )
            response.raise_for_status()
            oa_status = "open" if response.json().get("is_oa") else "closed"
            had_success = True
        except (httpx.HTTPError, TypeError, ValueError):
            had_failure = True
    metadata_status = "partial" if had_failure else ("enriched" if had_success else "rss_only")
    return replace(
        article,
        authors=authors,
        abstract=abstract,
        article_type=article_type,
        oa_status=oa_status,
        metadata_status=metadata_status,
    )
```

- [ ] **Step 6: Run enrichment/classification tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_classify.py tests\test_enrich.py -v
git add src/paper_radar/classify.py src/paper_radar/enrich.py tests/test_classify.py tests/test_enrich.py
git commit -m "feat: classify topics and enrich DOI metadata"
```

Expected: all tests pass, including the failure-preserves-RSS case.

### Task 7: Orchestrate updates with per-feed failure isolation

**Files:**
- Modify: `src/paper_radar/models.py`
- Create: `src/paper_radar/http_client.py`
- Create: `src/paper_radar/pipeline.py`
- Test: `tests/test_http_client.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Add the run summary contract**

Append to `src/paper_radar/models.py`:

```python
@dataclass(frozen=True, slots=True)
class RunSummary:
    status: str
    inserted: int
    updated: int
    skipped: int
    failed: int
    successful_feeds: tuple[str, ...]
    failed_feeds: tuple[str, ...]
```

- [ ] **Step 2: Write a failing failure-isolation integration test**

```python
# tests/test_http_client.py
import httpx
import respx

from paper_radar.http_client import PoliteClient


@respx.mock
def test_polite_client_paces_requests_to_the_same_domain() -> None:
    respx.get("https://example.org/one").mock(return_value=httpx.Response(200))
    respx.get("https://example.org/two").mock(return_value=httpx.Response(200))
    times = iter((0.0, 0.1))
    sleeps: list[float] = []
    with PoliteClient(
        min_interval_seconds=0.5,
        clock=lambda: next(times),
        sleeper=sleeps.append,
    ) as client:
        client.get("https://example.org/one")
        client.get("https://example.org/two")
    assert sleeps == [0.4]
```

```python
# tests/test_pipeline.py
from pathlib import Path

import httpx

from paper_radar.config import FeedConfig, TopicConfig
from paper_radar.models import FeedFetchResult
from paper_radar.pipeline import update_database

FIXTURE = Path(__file__).parent / "fixtures" / "rss2.xml"


def test_one_failed_feed_does_not_block_other_feeds(tmp_path: Path) -> None:
    good = FeedConfig("good", "Good", "ieee", "https://example.org/good.xml")
    bad = FeedConfig("bad", "Bad", "wiley", "https://example.org/bad.xml")

    def fake_fetcher(client, feed, **state):
        if feed.id == "bad":
            raise httpx.ConnectTimeout("offline")
        return FeedFetchResult(FIXTURE.read_bytes(), '"etag"', None, False)

    with httpx.Client() as client:
        summary = update_database(
            tmp_path / "papers.db",
            [good, bad],
            [TopicConfig("ultrasound", "Ultrasound", ("ultrasonic",))],
            client=client,
            fetcher=fake_fetcher,
            enricher=lambda client, article, **kwargs: article,
            sleeper=lambda seconds: None,
        )
    assert summary.status == "partial"
    assert summary.inserted == 1
    assert summary.successful_feeds == ("good",)
    assert summary.failed_feeds == ("bad",)
```

- [ ] **Step 3: Run the test and verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_pipeline.py -v
```

Expected: imports fail because `paper_radar.http_client` and `paper_radar.pipeline` are missing.

- [ ] **Step 4: Implement per-domain pacing**

```python
# src/paper_radar/http_client.py
from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx


class PoliteClient(httpx.Client):
    def __init__(
        self,
        *,
        min_interval_seconds: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._min_interval = min_interval_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._last_request: dict[str, float] = {}

    def get(self, url, **kwargs):  # type: ignore[override]
        host = urlsplit(str(url)).netloc.lower()
        now = self._clock()
        previous = self._last_request.get(host)
        delay = 0.0 if previous is None else self._min_interval - (now - previous)
        if delay > 0:
            self._sleeper(delay)
        self._last_request[host] = now + max(delay, 0.0)
        return super().get(url, **kwargs)
```

- [ ] **Step 5: Implement retries and isolated feed processing**

```python
# src/paper_radar/pipeline.py
from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from paper_radar.classify import classify_article
from paper_radar.config import FeedConfig, TopicConfig
from paper_radar.database import (
    connect_database, create_run, finish_run, get_feed_state, initialize_database,
    mark_journal_status, register_journal, replace_article_tags, upsert_article,
)
from paper_radar.enrich import enrich_article
from paper_radar.feeds import fetch_feed, parse_feed_bytes
from paper_radar.http_client import PoliteClient
from paper_radar.models import FeedFetchResult, RunSummary
from paper_radar.normalize import normalize_item


def _fetch_with_retries(
    client: httpx.Client,
    feed: FeedConfig,
    etag: str | None,
    last_modified: str | None,
    fetcher: Callable,
    sleeper: Callable[[float], None],
) -> FeedFetchResult:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return fetcher(client, feed, etag=etag, last_modified=last_modified)
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < 2:
                sleeper(0.5 * (2**attempt))
    assert last_error is not None
    raise last_error


def update_database(
    database_path: Path,
    feeds: list[FeedConfig],
    topics: list[TopicConfig],
    *,
    unpaywall_email: str | None = None,
    client: httpx.Client | None = None,
    fetcher: Callable = fetch_feed,
    enricher: Callable = enrich_article,
    sleeper: Callable[[float], None] = time.sleep,
) -> RunSummary:
    connection = connect_database(database_path)
    initialize_database(connection)
    run_id = create_run(connection)
    owns_client = client is None
    client = client or PoliteClient(follow_redirects=True)
    counts = {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
    successful_feeds: list[str] = []
    failed_feeds: list[str] = []
    item_errors: list[dict[str, str]] = []
    try:
        for feed in feeds:
            if not feed.enabled:
                continue
            register_journal(connection, feed)
            etag, last_modified = get_feed_state(connection, feed.id)
            try:
                result = _fetch_with_retries(
                    client, feed, etag, last_modified, fetcher, sleeper
                )
                if result.not_modified:
                    mark_journal_status(connection, feed.id, status="ok", etag=result.etag)
                    successful_feeds.append(feed.id)
                    continue
                feed_item_failures = 0
                for raw_item in parse_feed_bytes(result.content or b"", feed):
                    try:
                        article = normalize_item(raw_item, feed)
                        article = enricher(
                            client, article, unpaywall_email=unpaywall_email
                        )
                        outcome = upsert_article(connection, article)
                        counts[outcome] += 1
                        replace_article_tags(
                            connection, article.uid, classify_article(article, topics)
                        )
                    except Exception as exc:
                        counts["failed"] += 1
                        feed_item_failures += 1
                        item_errors.append(
                            {"feed": feed.id, "title": raw_item.title, "error": str(exc)[:300]}
                        )
                mark_journal_status(
                    connection,
                    feed.id,
                    status="partial" if feed_item_failures else "ok",
                    error=(f"{feed_item_failures} item failures" if feed_item_failures else None),
                    etag=result.etag,
                    last_modified=result.last_modified,
                )
                successful_feeds.append(feed.id)
            except Exception as exc:
                counts["failed"] += 1
                failed_feeds.append(feed.id)
                mark_journal_status(
                    connection, feed.id, status="error", error=str(exc)[:500]
                )
        status = "error" if not successful_feeds else (
            "partial" if failed_feeds or counts["failed"] else "ok"
        )
        notes = json.dumps(
            {
                "successful_feeds": successful_feeds,
                "failed_feeds": failed_feeds,
                "item_errors": item_errors[:50],
            },
            ensure_ascii=False,
        )
        finish_run(connection, run_id, status=status, notes=notes, **counts)
        return RunSummary(
            status=status,
            successful_feeds=tuple(successful_feeds),
            failed_feeds=tuple(failed_feeds),
            **counts,
        )
    finally:
        connection.close()
        if owns_client:
            client.close()
```

- [ ] **Step 6: Run pacing, isolation, and the full Python suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_http_client.py tests\test_pipeline.py -v
.\.venv\Scripts\python.exe -m pytest -v
.\.venv\Scripts\python.exe -m ruff check src tests
```

Expected: all tests pass and Ruff reports no errors.

- [ ] **Step 7: Commit the update pipeline**

```powershell
git add src/paper_radar/models.py src/paper_radar/http_client.py src/paper_radar/pipeline.py tests/test_http_client.py tests/test_pipeline.py
git commit -m "feat: isolate failures in RSS update pipeline"
```

### Task 8: Add database validation, safe publishing, and the Windows update command

**Files:**
- Create: `src/paper_radar/validation.py`
- Create: `src/paper_radar/cli.py`
- Create: `src/paper_radar/__main__.py`
- Create: `scripts/update.ps1`
- Test: `tests/test_validation.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing publication-gate tests**

```python
# tests/test_validation.py
from pathlib import Path

import pytest

from paper_radar.database import connect_database, initialize_database
from paper_radar.validation import ValidationError, publish_database, validate_database


def test_empty_database_is_not_publishable(tmp_path: Path) -> None:
    working = tmp_path / "working.db"
    connection = connect_database(working)
    initialize_database(connection)
    connection.close()
    with pytest.raises(ValidationError, match="database has no articles"):
        validate_database(working)


def test_failed_publish_keeps_existing_database(tmp_path: Path) -> None:
    working = tmp_path / "working.db"
    published = tmp_path / "docs" / "data" / "papers.db"
    published.parent.mkdir(parents=True)
    published.write_bytes(b"known-good")
    connection = connect_database(working)
    initialize_database(connection)
    connection.close()
    with pytest.raises(ValidationError):
        publish_database(working, published)
    assert published.read_bytes() == b"known-good"
```

```python
# tests/test_cli.py
import pytest

from paper_radar.cli import build_parser


def test_cli_requires_a_subcommand() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_validation.py tests\test_cli.py -v
```

Expected: imports fail because validation and CLI modules do not exist.

- [ ] **Step 3: Implement integrity checks and atomic publication**

```python
# src/paper_radar/validation.py
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


class ValidationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ValidationReport:
    article_count: int
    journal_count: int
    earliest_date: str | None
    latest_date: str | None


def _open_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def validate_database(path: Path, *, previous_path: Path | None = None) -> ValidationReport:
    if not path.exists():
        raise ValidationError(f"database does not exist: {path}")
    try:
        connection = _open_readonly(path)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValidationError(f"integrity check failed: {integrity}")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValidationError("foreign key check failed")
        article_count = connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        if article_count == 0:
            raise ValidationError("database has no articles")
        journal_count = connection.execute("SELECT COUNT(*) FROM journals").fetchone()[0]
        latest_run = connection.execute(
            "SELECT status FROM runs_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if latest_run is None or latest_run[0] == "error":
            raise ValidationError("latest run did not succeed")
        earliest, latest = connection.execute(
            "SELECT MIN(published_at), MAX(published_at) FROM articles"
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        raise ValidationError(f"invalid SQLite database: {exc}") from exc
    finally:
        if "connection" in locals():
            connection.close()
    if previous_path and previous_path.exists():
        try:
            previous = _open_readonly(previous_path)
            previous_count = previous.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            previous.close()
        except sqlite3.DatabaseError:
            previous_count = 0
        if previous_count and article_count < previous_count * 0.5:
            raise ValidationError(
                f"article count dropped from {previous_count} to {article_count}"
            )
    return ValidationReport(article_count, journal_count, earliest, latest)


def publish_database(working_path: Path, published_path: Path) -> ValidationReport:
    report = validate_database(working_path, previous_path=published_path)
    published_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = published_path.with_suffix(".db.tmp")
    temporary.unlink(missing_ok=True)
    source = sqlite3.connect(working_path)
    destination = sqlite3.connect(temporary)
    try:
        source.backup(destination)
    finally:
        source.close()
        destination.close()
    validate_database(temporary, previous_path=published_path)
    os.replace(temporary, published_path)
    return report
```

- [ ] **Step 4: Implement the CLI and combined update command**

```python
# src/paper_radar/cli.py
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

from paper_radar.config import load_feeds, load_topics
from paper_radar.pipeline import update_database
from paper_radar.validation import publish_database, validate_database

ROOT = Path.cwd()
DEFAULT_WORKING = ROOT / "data" / "papers.db"
DEFAULT_PUBLISHED = ROOT / "docs" / "data" / "papers.db"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper-radar")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("fetch", "validate", "publish", "update"):
        command = subparsers.add_parser(name)
        command.add_argument("--feeds", type=Path, default=ROOT / "feeds.yml")
        command.add_argument("--topics", type=Path, default=ROOT / "topics.yml")
        command.add_argument("--database", type=Path, default=DEFAULT_WORKING)
        command.add_argument("--published", type=Path, default=DEFAULT_PUBLISHED)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    if args.command in {"fetch", "update"}:
        summary = update_database(
            args.database,
            load_feeds(args.feeds),
            load_topics(args.topics),
            unpaywall_email=os.getenv("UNPAYWALL_EMAIL") or None,
        )
        print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
        if summary.status == "error":
            return 1
    if args.command in {"validate", "update"}:
        report = validate_database(args.database, previous_path=args.published)
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    if args.command in {"publish", "update"}:
        report = publish_database(args.database, args.published)
        print(
            f"published {report.article_count} articles to {args.published} "
            f"(working={args.database.stat().st_size} bytes, "
            f"published={args.published.stat().st_size} bytes)"
        )
    return 0
```

```python
# src/paper_radar/__main__.py
from paper_radar.cli import main

raise SystemExit(main())
```

```powershell
# scripts/update.ps1
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Virtual environment missing. Run: py -3.11 -m venv .venv'
}
Push-Location $Root
try {
    & $Python -m paper_radar update
    if ($LASTEXITCODE -ne 0) { throw "Update failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}
```

- [ ] **Step 5: Run validation/CLI tests and inspect CLI help**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_validation.py tests\test_cli.py -v
.\.venv\Scripts\python.exe -m paper_radar --help
```

Expected: tests pass and help lists `fetch`, `validate`, `publish`, and `update`.

- [ ] **Step 6: Commit the safe manual workflow**

```powershell
git add src/paper_radar/validation.py src/paper_radar/cli.py src/paper_radar/__main__.py scripts/update.ps1 tests/test_validation.py tests/test_cli.py
git commit -m "feat: add validated manual publish workflow"
```

### Task 9: Build the accessible responsive application shell

**Files:**
- Create: `docs/.nojekyll`
- Create: `docs/index.html`
- Create: `docs/styles.css`
- Create: `docs/js/app.js`
- Vendor: `docs/sql-wasm.js`
- Vendor: `docs/sql-wasm.wasm`

- [ ] **Step 1: Vendor sql.js 1.10.2 locally**

Run:

```powershell
New-Item -ItemType Directory -Force -Path docs\js, docs\data | Out-Null
Invoke-WebRequest -Uri 'https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.10.2/sql-wasm.js' -OutFile 'docs\sql-wasm.js'
Invoke-WebRequest -Uri 'https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.10.2/sql-wasm.wasm' -OutFile 'docs\sql-wasm.wasm'
New-Item -ItemType File -Force -Path 'docs\.nojekyll' | Out-Null
```

Expected: both sql.js files exist, `sql-wasm.wasm` is non-empty, and Pages will not run Jekyll processing.

- [ ] **Step 2: Create the complete semantic HTML shell**

```html
<!-- docs/index.html -->
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="description" content="Personal multi-publisher academic paper tracker">
  <title>Paper Radar</title>
  <link rel="stylesheet" href="styles.css">
  <script src="sql-wasm.js"></script>
</head>
<body>
  <header class="site-header">
    <a class="brand" href="./">Paper Radar</a>
    <nav aria-label="主导航">
      <a href="#papers">最新论文</a><a href="#data-status">数据状态</a><a href="#about">关于</a>
    </nav>
    <button id="open-filters" class="filter-toggle" type="button" aria-controls="filters" aria-expanded="false">
      筛选 <span id="active-filter-count" class="badge">0</span>
    </button>
  </header>

  <div id="filter-overlay" class="overlay" hidden></div>
  <main id="papers" class="layout">
    <aside id="filters" class="filters" aria-label="论文筛选">
      <div class="filters-heading"><h2>筛选</h2><button id="close-filters" type="button">关闭</button></div>
      <label>起始日期<input id="date-from" type="date"></label>
      <label>结束日期<input id="date-to" type="date"></label>
      <label>期刊<select id="journal"><option value="">全部期刊</option></select></label>
      <label>出版社<select id="publisher"><option value="">全部出版社</option></select></label>
      <label>文章类型<select id="article-type"><option value="">全部类型</option></select></label>
      <label>开放获取<select id="oa-status"><option value="">全部状态</option><option value="open">开放</option><option value="closed">非开放</option><option value="unknown">未知</option></select></label>
      <fieldset><legend>主题标签</legend><div id="tag-options" class="tag-options"></div></fieldset>
      <button id="clear-filters" class="secondary" type="button">清除筛选</button>
    </aside>

    <section class="results" aria-labelledby="results-title">
      <h1 id="results-title">最新论文</h1>
      <div class="search-row">
        <label class="search-label" for="search">搜索标题、摘要或 DOI</label>
        <input id="search" type="search" autocomplete="off" placeholder="例如 AlScN、BAW、ultrasound">
        <select id="sort" aria-label="排序"><option value="latest">最新优先</option><option value="oldest">最早优先</option></select>
      </div>
      <p id="status" class="status" role="status">正在加载论文数据库…</p>
      <div class="result-summary"><strong id="result-count">0</strong> 篇论文</div>
      <div id="article-list" class="article-list"></div>
      <nav id="pagination" class="pagination" aria-label="结果分页"></nav>
    </section>
  </main>

  <section id="data-status" class="info-section"><h2>数据状态</h2><p id="database-summary">数据库尚未加载。</p></section>
  <section id="about" class="info-section"><h2>关于</h2><p>本站聚合公开的期刊 RSS 元数据，文章版权归原出版商所有。</p></section>
  <script type="module" src="js/app.js"></script>
</body>
</html>
```

- [ ] **Step 3: Add the desktop sidebar, cards, and mobile drawer styles**

```css
/* docs/styles.css */
:root{color-scheme:light;--ink:#172033;--muted:#667085;--line:#d8dee9;--paper:#fff;--wash:#f5f7fb;--accent:#4f46e5;--open:#087f5b}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--wash);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
button,input,select{font:inherit}.site-header{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:24px;padding:14px clamp(16px,4vw,48px);background:rgba(255,255,255,.96);border-bottom:1px solid var(--line)}
.brand{font-size:1.25rem;font-weight:800;color:var(--ink);text-decoration:none}.site-header nav{display:flex;gap:18px;margin-left:auto}.site-header nav a{color:var(--muted);text-decoration:none}.filter-toggle{display:none;margin-left:auto}
.layout{display:grid;grid-template-columns:280px minmax(0,1fr);gap:24px;max-width:1440px;margin:auto;padding:28px clamp(16px,4vw,48px)}
.filters{position:sticky;top:86px;align-self:start;max-height:calc(100vh - 110px);overflow:auto;padding:20px;background:var(--paper);border:1px solid var(--line);border-radius:16px}.filters-heading{display:flex;justify-content:space-between;align-items:center}.filters-heading h2{margin:0}.filters-heading button{display:none}
.filters label,.filters fieldset{display:grid;gap:6px;margin:16px 0}.filters input,.filters select,.search-row input,.search-row select{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:#fff}.filters fieldset{padding:0;border:0}.tag-options{display:grid;gap:7px}.tag-options label{display:flex;align-items:center;gap:8px;margin:0}.tag-options input{width:auto}
.results{min-width:0}.results h1{margin:0 0 14px;font-size:clamp(1.8rem,4vw,3rem)}.search-row{display:grid;grid-template-columns:minmax(0,1fr) 150px;gap:10px}.search-label{grid-column:1/-1;font-weight:700}.status{padding:10px 12px;border-radius:10px;background:#eef2ff}.status.error{background:#fff0f0;color:#b42318}.result-summary{margin:16px 0;color:var(--muted)}
.article-list{display:grid;gap:14px}.article-card{padding:20px;background:var(--paper);border:1px solid var(--line);border-radius:16px;box-shadow:0 8px 24px rgba(23,32,51,.05)}.article-card h2{margin:0 0 8px;font-size:1.2rem}.article-card a{color:var(--ink)}.meta,.authors,.abstract{color:var(--muted)}.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{padding:3px 8px;border-radius:999px;background:#eef2ff;font-size:.82rem}.chip.open{background:#e6fcf5;color:var(--open)}
.pagination{display:flex;flex-wrap:wrap;gap:8px;margin:24px 0}.pagination button,.secondary,.filter-toggle{padding:9px 12px;border:1px solid var(--line);border-radius:10px;background:#fff}.pagination button[aria-current="page"]{background:var(--accent);color:#fff}.info-section{max-width:1440px;margin:0 auto 24px;padding:24px clamp(16px,4vw,48px)}.badge{display:inline-grid;place-items:center;min-width:22px;border-radius:999px;background:var(--accent);color:#fff}.overlay{position:fixed;inset:0;z-index:29;background:rgba(23,32,51,.45)}
@media(max-width:820px){.site-header nav{display:none}.filter-toggle{display:block}.layout{grid-template-columns:1fr}.filters{position:fixed;z-index:30;top:0;left:0;bottom:0;width:min(88vw,360px);max-height:none;border-radius:0;transform:translateX(-105%);transition:transform .2s ease}.filters.open{transform:translateX(0)}.filters-heading button{display:block}.search-row{grid-template-columns:1fr}.article-card{padding:16px}}
```

- [ ] **Step 4: Add a temporary module that proves the shell loads**

```javascript
// docs/js/app.js
const status = document.querySelector("#status");
status.textContent = "界面已准备，等待数据库查询模块。";
```

- [ ] **Step 5: Preview the shell locally**

Run in a dedicated terminal:

```powershell
.\.venv\Scripts\python.exe -m http.server 8000 --directory docs
```

Expected: `http://localhost:8000` shows the sidebar layout on desktop and no horizontal overflow at a narrow browser width.

- [ ] **Step 6: Commit the static shell and vendored dependency**

```powershell
git add docs
git commit -m "feat: add responsive Paper Radar shell"
```

### Task 10: Implement URL state and parameterized SQLite queries

**Files:**
- Create: `package.json`
- Create: `docs/js/state.js`
- Create: `docs/js/db.js`
- Test: `tests/web/state.test.mjs`
- Test: `tests/web/query.test.mjs`

- [ ] **Step 1: Add the zero-build JavaScript test command**

```json
// package.json
{
  "name": "paper-radar-web-tests",
  "private": true,
  "type": "module",
  "scripts": {"test:web": "node --test tests/web/*.test.mjs"}
}
```

- [ ] **Step 2: Write failing URL-state tests**

```javascript
// tests/web/state.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { parseState, serializeState } from "../../docs/js/state.js";

test("URL state round-trips multi-value tags", () => {
  const state = parseState("?q=AlScN&journal=apl&tag=saw&tag=baw&page=2&sort=oldest");
  assert.deepEqual(state.tags, ["saw", "baw"]);
  assert.equal(state.page, 2);
  assert.equal(parseState(`?${serializeState(state)}`).query, "AlScN");
});

test("invalid page and sort fall back safely", () => {
  const state = parseState("?page=-4&sort=drop-table");
  assert.equal(state.page, 1);
  assert.equal(state.sort, "latest");
});
```

- [ ] **Step 3: Write failing query-builder tests**

```javascript
// tests/web/query.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { buildArticleQuery } from "../../docs/js/db.js";

test("query builder parameterizes text and every selected tag", () => {
  const query = buildArticleQuery({
    query:"AlScN",from:"2026-01-01",to:"",journal:"apl",publisher:"aip",
    articleType:"research",oaStatus:"open",tags:["saw","baw"],sort:"latest",page:1,
  });
  assert.match(query.where, /a\.title/);
  assert.match(query.where, /EXISTS/);
  assert.equal(query.params[":tag0"], "saw");
  assert.equal(query.params[":tag1"], "baw");
  assert.equal(query.orderBy, "a.published_at DESC, a.uid DESC");
});
```

- [ ] **Step 4: Run the web tests and verify they fail**

Run:

```powershell
npm run test:web
```

Expected: both imports fail because `state.js` and `db.js` do not exist.

- [ ] **Step 5: Implement strict URL parsing and serialization**

```javascript
// docs/js/state.js
export const DEFAULT_STATE = Object.freeze({
  query:"",from:"",to:"",journal:"",publisher:"",articleType:"",oaStatus:"",
  tags:[],sort:"latest",page:1,
});

export function parseState(search = "") {
  const params = new URLSearchParams(search);
  const pageValue = Number.parseInt(params.get("page") || "1", 10);
  const sortValue = params.get("sort") === "oldest" ? "oldest" : "latest";
  return {
    query:params.get("q") || "", from:params.get("from") || "", to:params.get("to") || "",
    journal:params.get("journal") || "", publisher:params.get("publisher") || "",
    articleType:params.get("type") || "", oaStatus:params.get("oa") || "",
    tags:params.getAll("tag"), sort:sortValue,
    page:Number.isInteger(pageValue) && pageValue > 0 ? pageValue : 1,
  };
}

export function serializeState(state) {
  const params = new URLSearchParams();
  const pairs = [
    ["q",state.query],["from",state.from],["to",state.to],["journal",state.journal],
    ["publisher",state.publisher],["type",state.articleType],["oa",state.oaStatus],
  ];
  for (const [key,value] of pairs) if (value) params.set(key,value);
  for (const tag of state.tags) params.append("tag",tag);
  if (state.sort !== "latest") params.set("sort",state.sort);
  if (state.page > 1) params.set("page",String(state.page));
  return params.toString();
}
```

- [ ] **Step 6: Implement sql.js loading, options, and the query builder**

```javascript
// docs/js/db.js
const PAGE_SIZE = 20;

function rowsFromStatement(statement) {
  const rows=[];
  while(statement.step()) rows.push(statement.getAsObject());
  statement.free();
  return rows;
}

export async function loadDatabase() {
  const SQL = await initSqlJs({locateFile:file=>file});
  const response = await fetch(`data/papers.db?v=${Date.now()}`, {cache:"no-store"});
  if (!response.ok) throw new Error(`数据库下载失败：HTTP ${response.status}`);
  return new SQL.Database(new Uint8Array(await response.arrayBuffer()));
}

export function loadFilterOptions(db) {
  return {
    journals:rowsFromStatement(db.prepare("SELECT id,name FROM journals WHERE enabled=1 ORDER BY name")),
    publishers:rowsFromStatement(db.prepare("SELECT DISTINCT publisher AS id,publisher AS name FROM journals ORDER BY publisher")),
    tags:rowsFromStatement(db.prepare("SELECT id,label FROM tags ORDER BY label")),
  };
}

export function buildArticleQuery(state) {
  const clauses=[];
  const params={};
  if(state.query){clauses.push("LOWER(a.title || ' ' || COALESCE(a.abstract,'') || ' ' || COALESCE(a.doi,'')) LIKE :search");params[":search"]=`%${state.query.toLowerCase()}%`;}
  if(state.from){clauses.push("DATE(a.published_at)>=:from");params[":from"]=state.from;}
  if(state.to){clauses.push("DATE(a.published_at)<=:to");params[":to"]=state.to;}
  if(state.journal){clauses.push("a.journal_id=:journal");params[":journal"]=state.journal;}
  if(state.publisher){clauses.push("j.publisher=:publisher");params[":publisher"]=state.publisher;}
  if(state.articleType){clauses.push("a.article_type=:articleType");params[":articleType"]=state.articleType;}
  if(state.oaStatus){clauses.push("a.oa_status=:oaStatus");params[":oaStatus"]=state.oaStatus;}
  state.tags.forEach((tag,index)=>{const key=`:tag${index}`;clauses.push(`EXISTS(SELECT 1 FROM article_tags wanted WHERE wanted.article_uid=a.uid AND wanted.tag_id=${key})`);params[key]=tag;});
  return {where:clauses.length?`WHERE ${clauses.join(" AND ")}`:"",params,orderBy:state.sort==="oldest"?"a.published_at ASC, a.uid ASC":"a.published_at DESC, a.uid DESC"};
}

export function queryArticles(db,state) {
  const {where,params,orderBy}=buildArticleQuery(state);
  const count=db.prepare(`SELECT COUNT(*) AS total FROM articles a JOIN journals j ON j.id=a.journal_id ${where}`);
  count.bind(params);count.step();const total=Number(count.getAsObject().total);count.free();
  const page=Math.min(state.page,Math.max(1,Math.ceil(total/PAGE_SIZE)));
  const query=db.prepare(`SELECT a.*,j.name AS journal_name,j.publisher,
    (SELECT GROUP_CONCAT(t.label,'|||') FROM article_tags at JOIN tags t ON t.id=at.tag_id WHERE at.article_uid=a.uid) AS tag_labels
    FROM articles a JOIN journals j ON j.id=a.journal_id ${where}
    ORDER BY ${orderBy} LIMIT :limit OFFSET :offset`);
  query.bind({...params,":limit":PAGE_SIZE,":offset":PAGE_SIZE*(page-1)});
  return {rows:rowsFromStatement(query),total,pageSize:PAGE_SIZE,page};
}
```

- [ ] **Step 7: Run web tests and commit query/state modules**

Run:

```powershell
npm run test:web
git add package.json docs/js/state.js docs/js/db.js tests/web
git commit -m "feat: add URL state and SQLite article queries"
```

Expected: all Node tests pass.

### Task 11: Render articles, filters, pagination, and the mobile drawer

**Files:**
- Replace: `docs/js/app.js`
- Test: `tests/web/render-contract.test.mjs`

- [ ] **Step 1: Add a failing static contract test for required controls**

```javascript
// tests/web/render-contract.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("application shell exposes every enhanced filter", async () => {
  const html=await readFile(new URL("../../docs/index.html",import.meta.url),"utf8");
  for(const id of ["search","date-from","date-to","journal","publisher","article-type","oa-status","tag-options","sort"]){
    assert.match(html,new RegExp(`id=["']${id}["']`));
  }
});
```

- [ ] **Step 2: Run the contract test**

Run:

```powershell
npm run test:web
```

Expected: the contract test passes; it protects the HTML IDs that `app.js` will bind.

- [ ] **Step 3: Replace the temporary app module with the complete controller**

```javascript
// docs/js/app.js
import {loadDatabase,loadFilterOptions,queryArticles} from "./db.js";
import {DEFAULT_STATE,parseState,serializeState} from "./state.js";

const byId=id=>document.getElementById(id);
const controls={query:byId("search"),from:byId("date-from"),to:byId("date-to"),journal:byId("journal"),publisher:byId("publisher"),articleType:byId("article-type"),oaStatus:byId("oa-status"),sort:byId("sort")};
let state=parseState(location.search);let db;

function escapeHtml(value){return String(value??"").replace(/[&<>'"]/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));}
function safeUrl(value){try{const url=new URL(value);return ["http:","https:"].includes(url.protocol)?url.href:"#";}catch{return "#";}}
function syncControls(){for(const [key,control] of Object.entries(controls))control.value=state[key];document.querySelectorAll("[data-tag]").forEach(box=>box.checked=state.tags.includes(box.value));}
function readControls(){return {...state,...Object.fromEntries(Object.entries(controls).map(([key,control])=>[key,control.value])),tags:[...document.querySelectorAll("[data-tag]:checked")].map(box=>box.value),page:1};}
function writeUrl(){const query=serializeState(state);history.replaceState(null,"",query?`?${query}`:location.pathname);}

function populateOptions(options){
  for(const item of options.journals)controls.journal.insertAdjacentHTML("beforeend",`<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`);
  for(const item of options.publishers)controls.publisher.insertAdjacentHTML("beforeend",`<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`);
  for(const value of ["research","review","editorial","correction","other"])byId("article-type").insertAdjacentHTML("beforeend",`<option value="${value}">${value}</option>`);
  byId("tag-options").innerHTML=options.tags.map(item=>`<label><input data-tag type="checkbox" value="${escapeHtml(item.id)}">${escapeHtml(item.label)}</label>`).join("");
}

function articleCard(row){
  const authors=JSON.parse(row.authors_json||"[]").join(" · ");
  const tags=(row.tag_labels||"").split("|||").filter(Boolean).map(tag=>`<span class="chip">${escapeHtml(tag)}</span>`).join("");
  const oa=row.oa_status==="open"?'<span class="chip open">Open Access</span>':`<span class="chip">OA: ${escapeHtml(row.oa_status)}</span>`;
  return `<article class="article-card"><h2><a href="${escapeHtml(safeUrl(row.article_url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(row.title)}</a></h2>
    <p class="meta">${escapeHtml(row.journal_name)} · ${escapeHtml(row.publisher)} · ${escapeHtml((row.published_at||"").slice(0,10))} · ${escapeHtml(row.article_type)}</p>
    ${authors?`<p class="authors">${escapeHtml(authors)}</p>`:""}${row.abstract?`<p class="abstract">${escapeHtml(row.abstract)}</p>`:""}
    <div class="chips">${oa}${tags}</div></article>`;
}

function renderPagination(total,pageSize){
  const pages=Math.max(1,Math.ceil(total/pageSize));state.page=Math.min(state.page,pages);
  const buttons=[];for(let page=1;page<=pages;page++){if(pages>9&&Math.abs(page-state.page)>2&&page!==1&&page!==pages)continue;buttons.push(`<button data-page="${page}" ${page===state.page?'aria-current="page"':""}>${page}</button>`);}
  byId("pagination").innerHTML=buttons.join("");
}

function render(){
  const result=queryArticles(db,state);state.page=result.page;byId("result-count").textContent=String(result.total);
  byId("article-list").innerHTML=result.rows.length?result.rows.map(articleCard).join(""):'<p class="status">没有匹配的论文。</p>';
  renderPagination(result.total,result.pageSize);writeUrl();
  byId("active-filter-count").textContent=String([state.query,state.from,state.to,state.journal,state.publisher,state.articleType,state.oaStatus,...state.tags].filter(Boolean).length);
}

function setDrawer(open){byId("filters").classList.toggle("open",open);byId("filter-overlay").hidden=!open;byId("open-filters").setAttribute("aria-expanded",String(open));}
function bind(){
  for(const control of Object.values(controls))control.addEventListener("change",()=>{state=readControls();render();});
  controls.query.addEventListener("input",()=>{state=readControls();render();});
  byId("tag-options").addEventListener("change",()=>{state=readControls();render();});
  byId("pagination").addEventListener("click",event=>{const page=Number(event.target.dataset.page);if(page){state={...state,page};render();scrollTo({top:0,behavior:"smooth"});}});
  byId("clear-filters").addEventListener("click",()=>{state={...DEFAULT_STATE};syncControls();render();});
  byId("open-filters").addEventListener("click",()=>setDrawer(true));byId("close-filters").addEventListener("click",()=>setDrawer(false));byId("filter-overlay").addEventListener("click",()=>setDrawer(false));
}

async function start(){
  try{db=await loadDatabase();populateOptions(loadFilterOptions(db));syncControls();bind();render();byId("status").hidden=true;byId("database-summary").textContent=`已加载 ${byId("result-count").textContent} 篇可检索论文。`;}
  catch(error){byId("status").textContent=error.message;byId("status").classList.add("error");console.error(error);}
}
start();
```

- [ ] **Step 4: Run static web tests and commit the controller**

Run:

```powershell
npm run test:web
git add docs/js/app.js tests/web/render-contract.test.mjs
git commit -m "feat: render filters articles and pagination"
```

Expected: every Node test passes.

### Task 12: Add browser-level tests with a generated fixture database

**Files:**
- Create: `tests/e2e/conftest.py`
- Create: `tests/e2e/test_site.py`

- [ ] **Step 1: Create a fixture site server and populated database**

```python
# tests/e2e/conftest.py
from __future__ import annotations

import shutil
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from paper_radar.config import FeedConfig, TopicConfig
from paper_radar.database import (
    connect_database, create_run, finish_run, initialize_database, register_journal,
    replace_article_tags, upsert_article,
)
from paper_radar.models import ArticleRecord


@pytest.fixture(scope="session")
def site_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    root = tmp_path_factory.mktemp("site")
    shutil.copytree(Path("docs"), root, dirs_exist_ok=True)
    database_path = root / "data" / "papers.db"
    connection = connect_database(database_path)
    initialize_database(connection)
    register_journal(connection, FeedConfig("apl", "Applied Physics Letters", "aip", "https://example.org/apl.xml"))
    article = ArticleRecord(
        "doi:10.1000/alscn", "10.1000/alscn", "apl", "AlScN surface acoustic wave resonator",
        "A fixture abstract about piezoelectric filters.", ("Ada Example",),
        "2026-07-01T00:00:00+00:00", "research", "https://example.org/article",
        "https://example.org/article", "open", "https://example.org/apl.xml", "enriched",
    )
    upsert_article(connection, article)
    replace_article_tags(connection, article.uid, [TopicConfig("saw", "SAW", ("SAW",))])
    run_id = create_run(connection)
    finish_run(connection, run_id, status="ok", inserted=1, updated=0, skipped=0, failed=0, notes="fixture")
    connection.close()
    handler = partial(SimpleHTTPRequestHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown();thread.join(timeout=5)
```

- [ ] **Step 2: Write desktop filter and mobile drawer tests**

```python
# tests/e2e/test_site.py
from playwright.sync_api import Page, expect


def test_search_and_tag_filter_restore_from_url(page: Page, site_url: str) -> None:
    page.goto(f"{site_url}/?q=AlScN&tag=saw")
    expect(page.locator("#status")).to_be_hidden()
    expect(page.locator("#result-count")).to_have_text("1")
    expect(page.locator(".article-card h2")).to_contain_text("AlScN")
    assert page.locator('[data-tag=""]').count() == 0
    expect(page.locator('input[data-tag][value="saw"]')).to_be_checked()


def test_mobile_filter_drawer_opens(page: Page, site_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(site_url)
    page.locator("#open-filters").click()
    expect(page.locator("#filters")).to_have_class("filters open")
    expect(page.locator("#filter-overlay")).to_be_visible()
```

- [ ] **Step 3: Install the test browser and run E2E tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest tests\e2e -v
```

Expected: both browser tests pass. The first test proves URL restoration plus combined search/tag filtering; the second proves the mobile drawer interaction.

- [ ] **Step 4: Run the complete automated suite and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
npm run test:web
.\.venv\Scripts\python.exe -m ruff check src tests
git add tests/e2e
git commit -m "test: verify desktop filters and mobile drawer"
```

Expected: Python tests, Node tests, E2E tests, and Ruff all pass.

### Task 13: Document, run real-feed acceptance, and publish to GitHub Pages

**Files:**
- Modify: `.gitignore`
- Create: `README.md`
- Generate and commit: `docs/data/papers.db`

- [ ] **Step 1: Ignore working artifacts but keep the published database trackable**

Append to `.gitignore`:

```gitignore
data/*.db
data/*.db-*
data/*.tmp
docs/data/*.tmp
playwright-report/
test-results/
node_modules/
```

- [ ] **Step 2: Write the beginner-facing operating guide**

Create `README.md` with this exact structure and commands:

````markdown
# Paper Radar

个人使用的多出版社学术 RSS 聚合网站。数据来自公开的 Nature、AIP、IEEE 和 Wiley RSS，网站通过 GitHub Pages 发布。

## 1. Windows 首次安装

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

如需开放获取状态，请在 `.env` 中填写 `UNPAYWALL_EMAIL`；不填写时 OA 状态显示为 unknown。

## 2. 手动更新

```powershell
.\scripts\update.ps1
```

命令依次抓取 RSS、校验工作数据库，并仅在校验通过后更新 `docs/data/papers.db`。

## 3. 本地预览

```powershell
.\.venv\Scripts\python.exe -m http.server 8000 --directory docs
```

浏览器打开 `http://localhost:8000`。不要直接双击 `index.html`，因为浏览器不允许文件页面加载 SQLite/WASM。

## 4. 运行测试

```powershell
.\.venv\Scripts\python.exe -m pytest -v
npm run test:web
.\.venv\Scripts\python.exe -m ruff check src tests
```

## 5. 添加期刊

在 `feeds.yml` 的 `feeds` 列表中增加 `id`、`name`、`publisher` 和官方 HTTPS `feed_url`。运行测试和 `update.ps1`；前端会自动从数据库读取新期刊。

## 6. 添加主题

在 `topics.yml` 中增加唯一 `id`、显示名 `label` 和至少一个 `keywords`。再次更新数据库后，新标签自动出现在侧栏。

## 7. 发布

确认本地网站正常后提交 `docs/data/papers.db` 和代码并推送。GitHub 仓库 Settings → Pages 中选择 Deploy from a branch，分支选择 `main`，目录选择 `/docs`。

## 数据与访问说明

本站只展示公开论文元数据并链接出版社原文，不下载付费全文，也不绕过访问控制。`.env` 和本地工作数据库不会提交。
````

- [ ] **Step 3: Run the real feeds and inspect the acceptance summary**

Run:

```powershell
.\scripts\update.ps1
```

Expected:

- `summary.status` is `ok` or `partial`, never `error`.
- `successful_feeds` contains at least one Nature feed, `applied-physics-letters`, one `ieee-` feed, and one Wiley feed.
- `docs/data/papers.db` exists and is larger than zero bytes.
- Re-running the command adds no duplicate DOI records and reports mostly skipped records.

- [ ] **Step 4: Run a local browser acceptance pass**

Run in a dedicated terminal:

```powershell
.\.venv\Scripts\python.exe -m http.server 8000 --directory docs
```

Verify in the browser:

1. Search `AlScN` and confirm only matching title/abstract/DOI records remain.
2. Select Applied Physics Letters and one topic tag together.
3. Select publisher `ieee`, article type `research`, and an OA state.
4. Refresh and confirm the URL restores all selected filters.
5. Resize to 390 px width and confirm the filter drawer opens, closes, and has no horizontal overflow.
6. Open one article title and confirm it goes to the publisher page in a new tab.

- [ ] **Step 5: Run the full release gate**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
npm run test:web
.\.venv\Scripts\python.exe -m ruff check src tests
git status --short
```

Expected: every test passes, Ruff is clean, and `git status --short` lists only intentional README, ignore-rule, and generated published-database changes.

- [ ] **Step 6: Commit the verified MVP**

```powershell
git add .gitignore README.md docs/data/papers.db
git commit -m "docs: add operating guide and initial paper database"
```

- [ ] **Step 7: Create the user's public GitHub repository and push**

Run only after confirming the GitHub CLI is signed in:

```powershell
gh auth status
gh repo create academic-rss-site --public --source . --remote origin --push
```

Expected: GitHub reports a new public repository named `academic-rss-site`, adds `origin`, and pushes `main`.

- [ ] **Step 8: Enable GitHub Pages and verify production**

In the new repository, open **Settings → Pages**. Under **Build and deployment**, choose **Deploy from a branch**, select `main` and `/docs`, then save. Wait for the Pages screen to display the production URL, open that exact URL, and repeat the six browser checks from Step 4.

- [ ] **Step 9: Record the deployment result**

Append a `## Production site` section to `README.md` containing the exact URL shown by GitHub Pages, then run:

```powershell
git add README.md
git commit -m "docs: record production site URL"
git push
```

Expected: the README links to the live site and the working tree is clean.

## MVP completion gate

Do not claim completion until all of the following are true:

- Python unit/integration tests pass.
- Node URL/query tests pass.
- Playwright desktop and mobile tests pass.
- At least one real feed from each publisher family succeeded.
- A second update created no DOI duplicates.
- The safe publish gate rejected an empty test database.
- The production Pages URL loads the committed SQLite database.
- All seven filter groups work in production.
- `.env`, the working database, backups, and logs are absent from Git history.
- Scheduled GitHub Actions remain absent; automation starts only in a separate follow-up plan after the manual MVP is stable.
