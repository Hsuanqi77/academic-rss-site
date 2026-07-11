# RSS Guide and Research Taxonomy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the homepage About section with a configuration-backed Guide, expand the production research taxonomy to eight approved directions and 56 precise tags, and reclassify every stored article on each successful update.

**Architecture:** `topics.yml` becomes a validated catalog containing display groups and classifier topics; the database continues to store only flat precise tags. Classification remains deterministic and gains dash/space normalization plus optional group context requirements. A transactional reclassification pass runs after RSS persistence, while a deterministic renderer injects the enabled RSS sources and approved taxonomy into static HTML between stable markers.

**Tech Stack:** Python 3.12, dataclasses, PyYAML, SQLite, stdlib HTML escaping, pytest, Ruff, static HTML/CSS, vanilla JavaScript, Node test runner, Playwright, GitHub Actions and GitHub Pages.

---

## Scope and file map

Create:

- `src/paper_radar/matching.py` — shared case/dash/whitespace normalization without config imports.
- `src/paper_radar/reclassify.py` — deterministic full-database classification orchestration.
- `src/paper_radar/guide.py` — deterministic Guide HTML rendering from validated configs.
- `scripts/render_site_guide.py` — small command-line wrapper with write and `--check` modes.
- `tests/test_reclassify.py` — atomic reclassification and statistics tests.
- `tests/test_guide.py` — Guide renderer, escaping, grouping and drift tests.

Modify:

- `topics.yml` — eight groups and the exact 56-topic production vocabulary from the approved spec.
- `src/paper_radar/config.py` — group/catalog models and validation.
- `src/paper_radar/classify.py` — normalized separators and two-pass context gates.
- `src/paper_radar/database.py` — stable article listing and atomic bulk tag replacement.
- `src/paper_radar/models.py` — classification statistics in the update result.
- `src/paper_radar/pipeline.py` — invoke full reclassification after RSS persistence.
- `src/paper_radar/cli.py` — load the validated catalog while preserving command behavior.
- `docs/index.html` — navigation rename, Guide markers and generated content.
- `docs/styles.css` — Guide grids, details cards and approved 12px/11px type sizes.
- `tests/test_config.py`, `tests/test_classify.py`, `tests/test_database_repository.py`, `tests/test_pipeline.py`, `tests/test_cli.py` — backend contracts.
- `tests/test_static_shell.py`, `tests/test_static_shell_behavior.py`, `tests/web/*.test.mjs` — markup, accessibility, responsive and regression contracts.
- `tests/test_release_files.py` — renderer drift and README release contracts.
- `README.md` — taxonomy and Guide maintenance instructions.

Do not modify:

- `.github/workflows/daily-rss-update.yml` — its existing database-only commit guard remains correct.
- `src/paper_radar/schema.sql` — group metadata is configuration-only; no SQLite migration is needed.
- RSS source URLs or publisher adapters.

## Execution constraints

- Create an isolated worktree and branch `codex/rss-guide-taxonomy` before Task 1.
- Use the worktree-local `.venv`; install `-e '.[dev]'` if it does not exist.
- Do not run a real RSS update until Task 9 cloud acceptance. Unit tests must use fixtures and temporary databases.
- Do not edit `docs/data/papers.db` by hand. The first successful cloud workflow produces the only publication database commit.
- Every task follows RED → GREEN → focused regression → commit.
- After each implementation task, run specification review before code-quality review; do not continue with open Critical or Important findings.

### Task 1: Add the validated grouped topic catalog

**Files:**

- Create: `src/paper_radar/matching.py`
- Modify: `src/paper_radar/config.py`
- Modify: `topics.yml`
- Modify: `tests/test_config.py`
- Modify: topic constructors in `tests/test_classify.py`, `tests/test_database_repository.py`, `tests/test_pipeline.py`, `tests/e2e/conftest.py`

- [ ] **Step 1: Write failing catalog model and validation tests**

Add tests that define the desired public types and production invariants:

```python
from paper_radar.config import ConfigError, TopicCatalog, TopicConfig, TopicGroupConfig


def test_topic_catalog_models_are_immutable_and_slotted() -> None:
    group = TopicGroupConfig(id="acoustic-rf", label="声学与射频器件", order=1)
    topic = TopicConfig(
        id="baw",
        label="BAW",
        keywords=("bulk acoustic wave",),
        group="acoustic-rf",
    )
    catalog = TopicCatalog(groups=(group,), topics=(topic,))
    assert catalog.groups == (group,)
    assert catalog.topics == (topic,)
    with pytest.raises(FrozenInstanceError):
        group.label = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        (
            "topic_groups:\n  - {id: one, label: One, order: 1}\n"
            "topics:\n  - {id: x, label: X, group: missing, keywords: [word]}\n",
            "topic x references unknown group: missing",
        ),
        (
            "topic_groups:\n  - {id: one, label: One, order: 2}\n"
            "topics:\n  - {id: x, label: X, group: one, keywords: [word]}\n",
            "topic group order must be consecutive starting at 1",
        ),
        (
            "topic_groups:\n  - {id: one, label: One, order: 1}\n"
            "topics:\n  - {id: x, label: X, group: one, keywords: [Word, word]}\n",
            "topic x has duplicate normalized keyword: word",
        ),
        (
            "topic_groups:\n  - {id: one, label: One, order: 1}\n"
            "topics:\n  - id: x\n    label: X\n    group: one\n"
            "    requires_any_group: [one]\n    keywords: [word]\n",
            "topic x context groups must not include its own group",
        ),
    ],
)
def test_topic_catalog_rejects_invalid_group_contract(
    tmp_path: Path, yaml_text: str, message: str
) -> None:
    path = tmp_path / "topics.yml"
    path.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(ConfigError, match=f"^{re.escape(message)}$"):
        load_topic_catalog(path)
```

Add a production contract:

```python
def test_production_topic_catalog_has_approved_shape() -> None:
    catalog = load_topic_catalog(PROJECT_ROOT / "topics.yml")
    assert [group.id for group in catalog.groups] == [
        "acoustic-rf",
        "piezo-ferroelectric",
        "ultrasound-sensing",
        "mems-nems",
        "electronic-semiconductor",
        "ai-computational",
        "characterization-reliability",
        "emerging-cross-disciplinary",
    ]
    assert [group.order for group in catalog.groups] == list(range(1, 9))
    assert len(catalog.topics) == 56
    assert len({topic.id for topic in catalog.topics}) == 56
    assert all(topic.group in {group.id for group in catalog.groups} for topic in catalog.topics)
    assert {topic.id for topic in catalog.topics if topic.group == "acoustic-rf"} == {
        "baw", "saw", "fbar", "lamb-wave", "acoustic-resonator", "rf-microwave", "multiplexer"
    }
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py -v
```

Expected: collection or assertion failures because `TopicGroupConfig`, `TopicCatalog`, `group`, `requires_any_group`, and `load_topic_catalog()` do not exist.

- [ ] **Step 3: Implement the catalog types and strict loader**

Keep the original `TopicConfig` field order compatible where possible, then add the group fields:

```python
_TOPIC_GROUP_FIELDS = frozenset({"id", "label", "order"})
_TOPIC_FIELDS = frozenset({"id", "label", "group", "keywords", "requires_any_group"})


@dataclass(frozen=True, slots=True)
class TopicGroupConfig:
    id: str
    label: str
    order: int


@dataclass(frozen=True, slots=True)
class TopicConfig:
    id: str
    label: str
    keywords: tuple[str, ...]
    group: str
    requires_any_group: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TopicCatalog:
    groups: tuple[TopicGroupConfig, ...]
    topics: tuple[TopicConfig, ...]
```

Implement `load_topic_catalog(path)` by loading the YAML once with `_UniqueKeyLoader`, validating root mapping, parsing both non-empty lists and enforcing the approved invariants. Keyword duplicate comparison must call the shared separator normalizer below; do not silently drop duplicates.

Create `matching.py` without importing config or normalize modules, preventing circular imports:

```python
from unicodedata import category, normalize as normalize_unicode


def normalize_match_separators(value: str) -> str:
    normalized = normalize_unicode("NFC", value).casefold()
    separated = "".join(
        " "
        if character == "-" or character.isspace() or category(character) == "Pd"
        else character
        for character in normalized
    )
    return " ".join(separated.split())
```

Config validation calls `normalize_match_separators(keyword.strip())`. Classifier Task 2 first calls `clean_text()` to remove HTML and then calls this helper. Keep:

```python
def load_topics(path: Path) -> list[TopicConfig]:
    return list(load_topic_catalog(path).topics)
```

Update all test-only `TopicConfig(...)` constructors to supply a valid group such as `group="test"`. For tests that use context gating, create the referenced groups in their catalog fixtures.

- [ ] **Step 4: Replace `topics.yml` with the exact approved catalog**

Add the eight `topic_groups` in the order specified by the design spec. Add all 56 topics, labels and keywords exactly from sections 5.1–5.8 of:

`docs/superpowers/specs/2026-07-11-rss-guide-taxonomy-design.md`

Use these ids for the 56 topics, preserving order within each group:

```text
acoustic-rf: baw, saw, fbar, lamb-wave, acoustic-resonator, rf-microwave, multiplexer
piezo-ferroelectric: piezoelectric, ferroelectric, aln, alscn, pzt, linbo3, hfo2-hzo, lead-free-piezoelectrics, film-growth
ultrasound-sensing: pmut, cmut, ultrasonic-transducer, ultrasound-imaging, therapeutic-ultrasound, acoustic-sensing
mems-nems: mems, nems, microfabrication, wafer-integration, cmos-integration, packaging
electronic-semiconductor: transistor, ferroelectric-transistor, memory-memristor, power-electronics, wide-bandgap-devices, 2d-electronics, sensors
ai-computational: machine-learning, transformer-llm, inverse-design, surrogate-modelling, physics-informed-ai, materials-informatics, autonomous-research, digital-twin
characterization-reliability: xray-characterization, electron-microscopy, probe-microscopy, spectroscopy, crystal-quality, reliability
emerging-cross-disciplinary: phononics, quantum-acoustics, optomechanics, acoustofluidics, energy-harvesting, flexible-devices, nonreciprocal-acoustics
```

Set `requires_any_group` on all six characterization/reliability topics to the other seven group ids. Do not add standalone `AI`, `ML`, `DL`, `quality factor`, `insertion loss`, `sputtering`, `annealing`, `thin film`, `etching`, `lithography`, or `packaging` keywords beyond the approved qualified phrases.

- [ ] **Step 5: Run catalog tests and regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_classify.py tests/test_database_repository.py tests/test_pipeline.py tests/e2e -q
.\.venv\Scripts\python.exe -m ruff check src/paper_radar/config.py tests/test_config.py
git diff --check
```

Expected: all selected tests pass; Ruff and diff check exit 0.

- [ ] **Step 6: Commit Task 1**

```powershell
git add topics.yml src/paper_radar/matching.py src/paper_radar/config.py tests/test_config.py tests/test_classify.py tests/test_database_repository.py tests/test_pipeline.py tests/e2e/conftest.py
git commit -m "feat: add grouped research taxonomy"
```

### Task 2: Normalize classifier separators and apply context gates

**Files:**

- Modify: `src/paper_radar/classify.py`
- Modify: `tests/test_classify.py`

- [ ] **Step 1: Add failing separator and context tests**

Update the local test helper first so every synthetic topic has explicit group metadata:

```python
def _topic(
    topic_id: str,
    group: str,
    *keywords: str,
    requires_any_group: tuple[str, ...] = (),
) -> TopicConfig:
    return TopicConfig(
        id=topic_id,
        label=topic_id.upper(),
        keywords=keywords,
        group=group,
        requires_any_group=requires_any_group,
    )
```

```python
def test_classify_normalizes_ascii_and_unicode_dashes_to_phrase_spaces() -> None:
    topic = _topic("saw", "acoustic-rf", "surface acoustic wave")
    assert classify_article(_article(title="surface-acoustic-wave filter"), [topic]) == [topic]
    assert classify_article(_article(title="surface–acoustic—wave filter"), [topic]) == [topic]


def test_classify_keeps_short_acronyms_on_complete_token_boundaries() -> None:
    saw = _topic("saw", "acoustic-rf", "SAW")
    rf = _topic("rf", "acoustic-rf", "RF")
    assert classify_article(_article(title="A seesaw response"), [saw]) == []
    assert classify_article(_article(title="RF2 and RF_filter"), [rf]) == []
    assert classify_article(_article(title="An RF-MEMS SAW filter"), [rf, saw]) == [rf, saw]


def test_context_topic_requires_a_base_match_from_an_allowed_group() -> None:
    xrd = _topic(
        "xrd",
        "characterization-reliability",
        "XRD",
        requires_any_group=("piezo-ferroelectric",),
    )
    alscn = _topic("alscn", "piezo-ferroelectric", "AlScN")
    assert classify_article(_article(title="XRD refinement of a mineral"), [xrd, alscn]) == []
    assert classify_article(_article(title="XRD analysis of AlScN films"), [xrd, alscn]) == [xrd, alscn]


def test_context_topics_do_not_activate_each_other_cyclically() -> None:
    first = _topic("first", "one", "alpha", requires_any_group=("two",))
    second = _topic("second", "two", "beta", requires_any_group=("one",))
    assert classify_article(_article(title="alpha beta"), [first, second]) == []
```

- [ ] **Step 2: Run classifier tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_classify.py -v
```

Expected: dashed phrase and context tests fail with the current one-pass matcher.

- [ ] **Step 3: Implement a shared match-normalization function**

In `classify.py`, normalize both fields and keywords before boundary search by importing the Task 1 helper:

```python
def normalize_match_text(value: str) -> str | None:
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    return normalize_match_separators(cleaned)
```

Preserve the existing punctuation-aware token boundary rules for periods, plus signs and connector characters.

- [ ] **Step 4: Implement two-pass context evaluation**

```python
def classify_article(article: ArticleRecord, topics: Sequence[TopicConfig]) -> list[TopicConfig]:
    fields = tuple(
        normalized
        for value in (article.title, article.abstract)
        if (normalized := normalize_match_text(value)) is not None
    )
    base_matches = {
        topic.id: any(_keyword_matches(keyword, fields) for keyword in topic.keywords)
        for topic in topics
    }
    base_groups = {
        topic.group for topic in topics if base_matches[topic.id] and not topic.requires_any_group
    }
    return [
        topic
        for topic in topics
        if base_matches[topic.id]
        and (
            not topic.requires_any_group
            or any(group in base_groups for group in topic.requires_any_group)
        )
    ]
```

The group set must only use ungated base topics, so two gated topics cannot activate each other.

- [ ] **Step 5: Run classifier and config tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_classify.py tests/test_config.py -v
.\.venv\Scripts\python.exe -m ruff check src/paper_radar/classify.py src/paper_radar/matching.py src/paper_radar/config.py tests/test_classify.py
git diff --check
```

Expected: all pass.

- [ ] **Step 6: Commit Task 2**

```powershell
git add src/paper_radar/classify.py tests/test_classify.py
git commit -m "feat: harden taxonomy keyword matching"
```

### Task 3: Add atomic full-database reclassification

**Files:**

- Create: `src/paper_radar/reclassify.py`
- Create: `tests/test_reclassify.py`
- Modify: `src/paper_radar/database.py`
- Modify: `src/paper_radar/models.py`
- Modify: `tests/test_database_repository.py`

- [ ] **Step 1: Write failing repository and orchestration tests**

Build temporary SQLite databases with two persisted articles. Cover replacement, orphan cleanup, statistics, idempotence and rollback:

```python
def _topic(topic_id: str, label: str, group: str, *keywords: str) -> TopicConfig:
    return TopicConfig(
        id=topic_id,
        label=label,
        keywords=keywords,
        group=group,
    )
```

```python
def test_reclassify_all_articles_replaces_stale_links_and_reports_counts(tmp_path: Path) -> None:
    connection = _database_with_articles(tmp_path, [
        _article("one", title="An AlScN BAW resonator"),
        _article("two", title="Unrelated editorial"),
    ])
    topics = (
        _topic("baw", "BAW", "acoustic-rf", "bulk acoustic wave", "BAW"),
        _topic("alscn", "AlScN", "piezo-ferroelectric", "AlScN"),
    )
    _seed_tag(connection, article_uid="two", tag_id="obsolete", label="Obsolete")

    summary = reclassify_all_articles(connection, topics)

    assert summary == ClassificationSummary(
        articles_scanned=2,
        articles_tagged=1,
        tag_assignments=2,
        active_tags=2,
    )
    assert _tag_rows(connection) == [("one", "alscn"), ("one", "baw")]
    assert _all_tag_ids(connection) == ["alscn", "baw"]


def test_reclassify_all_articles_is_idempotent(tmp_path: Path) -> None:
    connection = _database_with_articles(tmp_path, [_article("one", title="BAW resonator")])
    topics = (_topic("baw", "BAW", "acoustic-rf", "BAW"),)
    first = reclassify_all_articles(connection, topics)
    second = reclassify_all_articles(connection, topics)
    assert second == first
    assert _tag_rows(connection) == [("one", "baw")]


def test_bulk_tag_replacement_rolls_back_on_insert_failure(tmp_path: Path, monkeypatch) -> None:
    connection = _database_with_articles(tmp_path, [_article("one", title="BAW resonator")])
    _seed_tag(connection, article_uid="one", tag_id="old", label="Old")
    with pytest.raises(RepositoryNotFoundError):
        replace_all_article_tags(connection, {"missing": (_topic("baw", "BAW", "acoustic-rf", "BAW"),)})
    assert _tag_rows(connection) == [("one", "old")]
```

- [ ] **Step 2: Run new tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_reclassify.py tests/test_database_repository.py -v
```

Expected: import failures for the new summary and functions.

- [ ] **Step 3: Add stable article listing and atomic bulk replacement**

Refactor the row-to-`ArticleRecord` conversion currently inside `get_article()` into `_article_from_row(row)`. Add:

```python
def list_articles(connection: sqlite3.Connection) -> tuple[ArticleRecord, ...]:
    rows = connection.execute("SELECT * FROM articles ORDER BY uid").fetchall()
    return tuple(_article_from_row(row) for row in rows)


def replace_all_article_tags(
    connection: sqlite3.Connection,
    assignments: Mapping[str, Sequence[TopicConfig]],
) -> None:
    with _atomic(connection):
        known = {row["uid"] for row in connection.execute("SELECT uid FROM articles")}
        missing = sorted(set(assignments) - known)
        if missing:
            raise RepositoryNotFoundError(f"article not found: {missing[0]}")
        # Validate ids and labels before deleting any links.
        topics_by_id = _validated_topics(topic for values in assignments.values() for topic in values)
        connection.execute("DELETE FROM article_tags")
        for topic in topics_by_id.values():
            _upsert_or_migrate_tag(connection, topic)
        connection.executemany(
            "INSERT INTO article_tags (article_uid, tag_id) VALUES (?, ?)",
            (
                (uid, topic.id)
                for uid in sorted(assignments)
                for topic in assignments[uid]
            ),
        )
        connection.execute(
            "DELETE FROM tags WHERE NOT EXISTS "
            "(SELECT 1 FROM article_tags WHERE article_tags.tag_id = tags.id)"
        )
```

Extract `_validated_topics()` from the validation already duplicated in `replace_article_tags()`; both single-article and bulk paths must call it.

- [ ] **Step 4: Add classification summary and orchestration module**

In `models.py`:

```python
@dataclass(frozen=True, slots=True)
class ClassificationSummary:
    articles_scanned: int
    articles_tagged: int
    tag_assignments: int
    active_tags: int
```

In `reclassify.py`:

```python
def reclassify_all_articles(
    connection: sqlite3.Connection,
    topics: Sequence[TopicConfig],
) -> ClassificationSummary:
    articles = list_articles(connection)
    assignments = {
        article.uid: tuple(classify_article(article, topics))
        for article in articles
    }
    replace_all_article_tags(connection, assignments)
    used_ids = {topic.id for matched in assignments.values() for topic in matched}
    return ClassificationSummary(
        articles_scanned=len(articles),
        articles_tagged=sum(bool(matched) for matched in assignments.values()),
        tag_assignments=sum(len(matched) for matched in assignments.values()),
        active_tags=len(used_ids),
    )
```

- [ ] **Step 5: Run repository and reclassification regressions**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_reclassify.py tests/test_database_repository.py -v
.\.venv\Scripts\python.exe -m ruff check src/paper_radar/database.py src/paper_radar/models.py src/paper_radar/reclassify.py tests/test_reclassify.py
git diff --check
```

Expected: all pass.

- [ ] **Step 6: Commit Task 3**

```powershell
git add src/paper_radar/database.py src/paper_radar/models.py src/paper_radar/reclassify.py tests/test_database_repository.py tests/test_reclassify.py
git commit -m "feat: reclassify all stored articles atomically"
```

### Task 4: Integrate full reclassification into update results

**Files:**

- Modify: `src/paper_radar/models.py`
- Modify: `src/paper_radar/pipeline.py`
- Modify: `src/paper_radar/cli.py`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing pipeline integration tests**

Add a test proving an already stored article is reclassified even when the feed is HTTP-not-modified:

```python
def _topic(topic_id: str, group: str, *keywords: str) -> TopicConfig:
    return TopicConfig(
        id=topic_id,
        label=topic_id.upper(),
        keywords=keywords,
        group=group,
    )
```

```python
def test_not_modified_update_reclassifies_every_stored_article(tmp_path: Path) -> None:
    database_path = tmp_path / "papers.db"
    _seed_article(database_path, _article(title="AlScN BAW resonator"))
    result = FeedFetchResult(
        content=None,
        etag='"same"',
        last_modified="Fri, 10 Jul 2026 00:00:00 GMT",
        not_modified=True,
    )
    topics = (
        _topic("baw", "acoustic-rf", "BAW"),
        _topic("alscn", "piezo-ferroelectric", "AlScN"),
    )

    summary = update_database(
        database_path,
        [_feed()],
        topics,
        client=_client(),
        fetcher=lambda *_args, **_kwargs: result,
    )

    assert summary.classification == ClassificationSummary(1, 1, 2, 2)
    assert _rows(database_path, "SELECT tag_id FROM article_tags ORDER BY tag_id") == [
        {"tag_id": "alscn"},
        {"tag_id": "baw"},
    ]
```

Add a rollback/fatal test by monkeypatching `pipeline.reclassify_all_articles` to raise; assert the run is finalized as error, the exception propagates, and the preexisting tag relationship remains unchanged.

Add a CLI JSON contract:

```python
assert payload["result"]["classification"] == {
    "active_tags": 2,
    "articles_scanned": 1,
    "articles_tagged": 1,
    "tag_assignments": 2,
}
```

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_pipeline.py tests/test_cli.py -v
```

Expected: missing `RunSummary.classification` and no full reclassification call.

- [ ] **Step 3: Extend `RunSummary` and call the classifier once per update**

Add the field after feed status fields:

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
    classification: ClassificationSummary
```

In `update_database()`, remove per-item calls to `classify_article()` and `replace_article_tags()`. After all feed loops and before `_terminal_status()`/`finish_run()`, call:

```python
classification = reclassify_all_articles(connection, topic_list)
```

Include `classification=classification` in `RunSummary`. This single pass is the only production classification path. If it raises, the existing outer exception handling finalizes the run as error and CLI publication remains blocked.

- [ ] **Step 4: Load the full catalog once in CLI**

Change CLI config loading to:

```python
catalog = load_topic_catalog(args.topics)
summary = update_database(
    args.database,
    load_feeds(args.feeds),
    catalog.topics,
    unpaywall_email=os.getenv("UNPAYWALL_EMAIL") or None,
)
```

Do not change CLI flags, default paths, error JSON or publish gates. `asdict(summary)` automatically exposes classification statistics.

- [ ] **Step 5: Run pipeline, CLI and E2E tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_pipeline.py tests/test_cli.py tests/e2e -v
.\.venv\Scripts\python.exe -m ruff check src/paper_radar/pipeline.py src/paper_radar/cli.py src/paper_radar/models.py tests/test_pipeline.py tests/test_cli.py
git diff --check
```

Expected: all pass.

- [ ] **Step 6: Commit Task 4**

```powershell
git add src/paper_radar/models.py src/paper_radar/pipeline.py src/paper_radar/cli.py tests/test_pipeline.py tests/test_cli.py tests/e2e
git commit -m "feat: refresh taxonomy during every update"
```

### Task 5: Generate the Guide from feeds and taxonomy config

**Files:**

- Create: `src/paper_radar/guide.py`
- Create: `scripts/render_site_guide.py`
- Create: `tests/test_guide.py`
- Modify: `docs/index.html`
- Modify: `tests/test_release_files.py`

- [ ] **Step 1: Add failing deterministic renderer tests**

Use small temporary feed/catalog fixtures and assert exact structural output, escaping and disabled-feed handling:

```python
def test_render_guide_groups_enabled_feeds_and_topics_with_escaped_text() -> None:
    feeds = (
        FeedConfig("nature", "Nature <Research>", "nature", "https://example.com/nature.rss"),
        FeedConfig("disabled", "Disabled", "ieee", "https://example.com/off.rss", enabled=False),
    )
    catalog = TopicCatalog(
        groups=(TopicGroupConfig("acoustic-rf", "声学与射频器件", 1),),
        topics=(TopicConfig("baw", "BAW & filters", ("bulk acoustic wave",), "acoustic-rf"),),
    )
    html = render_guide(feeds, catalog)
    assert "Nature &lt;Research&gt;" in html
    assert "BAW &amp; filters" in html
    assert "https://example.com/nature.rss" in html
    assert "Disabled" not in html
    assert 'target="_blank" rel="noopener noreferrer"' in html


def test_replace_guide_region_is_deterministic_and_preserves_surroundings() -> None:
    source = "before\n<!-- GUIDE:START -->\nold\n<!-- GUIDE:END -->\nafter\n"
    rendered = replace_guide_region(source, "<section id=\"guide\">new</section>")
    assert rendered == (
        "before\n<!-- GUIDE:START -->\n<section id=\"guide\">new</section>\n"
        "<!-- GUIDE:END -->\nafter\n"
    )
    assert replace_guide_region(rendered, "<section id=\"guide\">new</section>") == rendered


def test_renderer_check_mode_detects_drift(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text(_index_with_markers("stale"), encoding="utf-8")
    assert render_file(index, feeds=_feeds(), catalog=_catalog(), check=True) is False
    assert index.read_text(encoding="utf-8") == _index_with_markers("stale")
```

- [ ] **Step 2: Run renderer tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_guide.py -v
```

Expected: import failure for `paper_radar.guide`.

- [ ] **Step 3: Implement pure rendering functions**

`guide.py` must define `GUIDE_START`, `GUIDE_END` and the exact publisher display map below, and expose the typed public functions `render_guide(feeds: Sequence[FeedConfig], catalog: TopicCatalog) -> str`, `replace_guide_region(source: str, rendered_guide: str) -> str`, and `render_file(index_path: Path, *, feeds: Sequence[FeedConfig], catalog: TopicCatalog, check: bool) -> bool`.

```python
GUIDE_START = "<!-- GUIDE:START -->"
GUIDE_END = "<!-- GUIDE:END -->"
PUBLISHER_LABELS = {
    "nature": "Nature Portfolio",
    "ieee": "IEEE",
    "aip": "AIP Publishing",
    "wiley": "Wiley",
}
```

Use `html.escape(value, quote=True)` for every label, name, URL and keyword. Render only enabled feeds, grouped in the fixed publisher order above. Render group and topic order from catalog. Use native `<details>`/`<summary>`; set the first publisher and first topic group to `open`. Count values come from the actual sequences, never hard-coded.

`replace_guide_region()` must require exactly one start and end marker in the correct order; otherwise raise `GuideRenderError` without writing.

- [ ] **Step 4: Add the CLI wrapper**

`scripts/render_site_guide.py` parses only `--check`, resolves repository root from the script location, loads validated configs, and exits:

```python
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    feeds = load_feeds(root / "feeds.yml")
    catalog = load_topic_catalog(root / "topics.yml")
    current = root / "docs" / "index.html"
    in_sync = render_file(current, feeds=feeds, catalog=catalog, check=args.check)
    if args.check and not in_sync:
        print("docs/index.html Guide region is out of date", file=sys.stderr)
        return 1
    return 0
```

- [ ] **Step 5: Replace About with stable Guide markers and generate content**

In `docs/index.html`:

- change navigation text and href from `关于`/`#about` to `说明`/`#guide`;
- remove the old About section;
- add exactly one empty marker pair before the footer;
- keep `data-drawer-background` on the generated section.

Run:

```powershell
.\.venv\Scripts\python.exe scripts/render_site_guide.py
.\.venv\Scripts\python.exe scripts/render_site_guide.py --check
```

Expected: first command writes the full approved Guide; second exits 0 without changing the file.

- [ ] **Step 6: Add release drift contract and run tests**

In `tests/test_release_files.py` assert the real renderer check returns true, the production HTML contains `每天检查的 RSS 列表`, `标签与关键词`, `20 SOURCES`, all eight group labels, and does not contain `href="#about"`.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_guide.py tests/test_release_files.py -v
.\.venv\Scripts\python.exe -m ruff check src/paper_radar/guide.py scripts/render_site_guide.py tests/test_guide.py
git diff --check
```

Expected: all pass.

- [ ] **Step 7: Commit Task 5**

```powershell
git add src/paper_radar/guide.py scripts/render_site_guide.py tests/test_guide.py docs/index.html tests/test_release_files.py
git commit -m "feat: generate the site Guide from configuration"
```

### Task 6: Style and verify the responsive Guide

**Files:**

- Modify: `docs/styles.css`
- Modify: `tests/test_static_shell.py`
- Modify: `tests/test_static_shell_behavior.py`

- [ ] **Step 1: Add failing static structure and typography tests**

Assert the generated markup exposes one guide section, native details/summary, safe external links and CSS contracts:

```python
def test_guide_has_accessible_native_disclosures_and_safe_links() -> None:
    parser = _parse_index()
    assert parser.ids.count("guide") == 1
    assert parser.details_count == 12  # four publishers plus eight topic groups
    assert parser.summary_count == 12
    assert parser.unsafe_external_links == []


def test_guide_uses_approved_type_sizes() -> None:
    css = STYLES.read_text(encoding="utf-8")
    assert re.search(r"\.guide-tag-name\s*\{[^}]*font-size:\s*12px", css, re.S)
    assert re.search(r"\.guide-keywords\s*\{[^}]*font-size:\s*11px", css, re.S)
```

- [ ] **Step 2: Add failing browser tests**

Add desktop and 390px Playwright tests to `tests/test_static_shell_behavior.py` using the existing `page_factory` fixture:

```python
def test_guide_disclosures_are_keyboard_operable_and_show_config_content(
    page_factory: Callable[..., Page],
) -> None:
    page = page_factory()
    first = page.locator("#guide details").first
    summary = first.locator("summary")
    summary.focus()
    page.keyboard.press("Enter")
    assert first.get_attribute("open") is None
    page.keyboard.press("Enter")
    assert first.get_attribute("open") == ""
    assert "Nature Communications" in (page.locator("#guide").text_content() or "")
    assert "bulk acoustic wave" in (page.locator("#guide").text_content() or "")


def test_guide_stacks_at_390px_without_overflow_and_keeps_approved_sizes(
    page_factory: Callable[..., Page],
) -> None:
    page = page_factory(width=390)
    assert page.locator(".guide-tag-name").first.evaluate(
        "element => getComputedStyle(element).fontSize"
    ) == "12px"
    assert page.locator(".guide-keywords").first.evaluate(
        "element => getComputedStyle(element).fontSize"
    ) == "11px"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth") is True
    assert page.locator(".guide-grid").first.evaluate(
        "element => getComputedStyle(element).gridTemplateColumns.split(' ').length"
    ) == 1
```

- [ ] **Step 3: Run new UI tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_static_shell.py tests/test_static_shell_behavior.py -v
```

Expected: missing Guide styles and responsive contracts fail.

- [ ] **Step 4: Implement the approved Guide CSS**

Add focused classes:

```css
.guide-stats { display:flex; flex-wrap:wrap; gap:8px; margin:16px 0 28px; }
.guide-stat { padding:6px 10px; border:1px solid var(--line); border-radius:999px; }
.guide-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
.guide-group { border:1px solid var(--line); border-radius:8px; background:var(--paper); overflow:hidden; }
.guide-group summary { cursor:pointer; display:flex; justify-content:space-between; gap:12px; padding:14px; font-size:13px; font-weight:700; }
.guide-group-content { padding:0 14px 14px; border-top:1px solid var(--line); }
.guide-tag { padding:11px 0; border-bottom:1px solid var(--line); }
.guide-tag-name { font-size:12px; line-height:1.45; font-weight:800; }
.guide-keywords { margin-top:5px; color:var(--muted); font:11px/1.65 ui-monospace,Consolas,monospace; overflow-wrap:anywhere; }
.guide-feed-url { display:block; overflow-wrap:anywhere; }
@media (max-width:820px) { .guide-grid { grid-template-columns:1fr; } }
```

Use existing project color variables and spacing rhythm; do not introduce a new visual theme. Preserve Noto Sans SC and the existing 13px technical-label standard.

- [ ] **Step 5: Run static, browser and regression tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_static_shell.py tests/test_static_shell_behavior.py -v
$node = 'C:\Users\Xuanqi\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
$files = Get-ChildItem tests\web -Filter *.test.mjs | Sort-Object Name | Select-Object -ExpandProperty FullName
& $node --test $files
git diff --check
```

Expected: all pass with no skips.

- [ ] **Step 6: Commit Task 6**

```powershell
git add docs/styles.css tests/test_static_shell.py tests/test_static_shell_behavior.py
git commit -m "feat: style the responsive Guide"
```

### Task 7: Document taxonomy and Guide maintenance

**Files:**

- Modify: `README.md`
- Modify: `tests/test_release_files.py`

- [ ] **Step 1: Add a failing README contract**

Require these phrases or equivalent exact headings:

```python
def test_readme_documents_guide_generation_and_full_reclassification() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for phrase in (
        "8 个一级方向",
        "56 个精细标签",
        "scripts/render_site_guide.py",
        "--check",
        "全部已存论文",
        "完整单词边界",
        "自动标签仅供初步筛选",
    ):
        assert phrase in readme
```

- [ ] **Step 2: Run and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_release_files.py::test_readme_documents_guide_generation_and_full_reclassification -v
```

Expected: fail because the new maintenance workflow is not documented.

- [ ] **Step 3: Update README maintenance instructions**

In the existing tag section, document:

```markdown
`topics.yml` 同时定义 8 个一级方向、56 个精细标签、英文关键词及可选上下文门槛。一级方向只组织说明内容，侧栏仍按精细标签筛选。分类使用标题和摘要，缩写必须满足完整单词边界；自动标签仅供初步筛选。

修改 `feeds.yml` 或 `topics.yml` 后运行：

```powershell
.\.venv\Scripts\python.exe scripts/render_site_guide.py
.\.venv\Scripts\python.exe scripts/render_site_guide.py --check
```

每日 `update` 会重新标注全部已存论文，因此词表修改也会应用到旧论文。生成后的 `docs/index.html` 必须与 YAML 配置在同一次人工提交中提交。
```

Do not duplicate the daily cloud automation instructions already present elsewhere in README.

- [ ] **Step 4: Run release tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_release_files.py -v
git diff --check
git add README.md tests/test_release_files.py
git commit -m "docs: explain taxonomy and Guide maintenance"
```

Expected: all release tests pass and worktree is clean.

### Task 8: Run complete local acceptance and final review

**Files:**

- Verify only: complete repository

- [ ] **Step 1: Confirm generated content is current**

```powershell
.\.venv\Scripts\python.exe scripts/render_site_guide.py --check
```

Expected: exit 0 and no file changes.

- [ ] **Step 2: Run all Python and Playwright tests**

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

Expected: all tests pass, no browser acceptance skips.

- [ ] **Step 3: Run all Node tests**

```powershell
$node = 'C:\Users\Xuanqi\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
$files = Get-ChildItem tests\web -Filter *.test.mjs | Sort-Object Name | Select-Object -ExpandProperty FullName
& $node --test $files
```

Expected: all Node tests pass, with count equal to the repository's updated suite.

- [ ] **Step 4: Run lint and repository integrity gates**

```powershell
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
git status --short
```

Expected: Ruff and diff check exit 0; worktree is clean.

- [ ] **Step 5: Run a no-network production-data reclassification probe**

Copy `docs/data/papers.db` into a temporary directory, load the production catalog, call `reclassify_all_articles()` on the copy and verify:

```text
integrity_check = ok
articles_scanned = SQL count(*) from articles
articles_tagged > 0
tag_assignments >= articles_tagged
active_tags > 0
```

Do not modify either tracked database. Record counts for release comparison.

- [ ] **Step 6: Perform final specification and code-quality reviews**

Review the entire diff against:

`docs/superpowers/specs/2026-07-11-rss-guide-taxonomy-design.md`

Block release on any Critical or Important finding. Re-run Steps 1–5 after every fix.

### Task 9: Integrate, deploy and verify production

**External state:**

- `Hsuanqi77/academic-rss-site` `main`
- GitHub Actions workflow `Daily RSS Update`
- GitHub Pages production site

- [ ] **Step 1: Integrate using the selected branch-completion path**

Use `superpowers:finishing-a-development-branch`. For direct main deployment, require explicit user approval, then fast-forward local `main` and push without force:

```powershell
git push origin main
```

Expected: remote main advances to the reviewed implementation commit.

- [ ] **Step 2: Trigger Daily RSS Update manually**

```powershell
$gh = 'C:\Program Files\GitHub CLI\gh.exe'
& $gh workflow run daily-rss-update.yml --repo Hsuanqi77/academic-rss-site --ref main
```

Expected: command returns a run URL.

- [ ] **Step 3: Watch the run and diagnose actual failures**

```powershell
$runId = & $gh run list --repo Hsuanqi77/academic-rss-site --workflow daily-rss-update.yml --event workflow_dispatch --limit 1 --json databaseId --jq '.[0].databaseId'
if (-not $runId) { throw 'No manual Daily RSS Update run found' }
& $gh run watch $runId --repo Hsuanqi77/academic-rss-site --exit-status
```

Expected: success. On failure, use systematic debugging and `gh run view $runId --log-failed`; never weaken validation or force push.

- [ ] **Step 4: Verify bot commit scope and classification result**

Read remote main and assert:

```text
commit message = chore(data): daily RSS update
changed files = docs/data/papers.db only
```

Download the remote database and verify SQLite integrity, total article count, active tag count and assignment count. Compare the last two values to the local no-network probe; differences are acceptable only when the cloud run inserted new articles, and must be explainable from the run summary.

- [ ] **Step 5: Verify Pages and the live Guide**

Confirm the latest Pages build is `built` for the bot commit. In desktop and 390px browser sessions verify:

```text
navigation contains 说明 and no 关于 link
Guide contains 20 enabled RSS sources
Guide contains 8 directions and 56 precise labels
external RSS links are clickable and safe
details are keyboard operable
precise label computed font-size = 12px
keyword computed font-size = 11px
no horizontal overflow or console errors
existing search, filtering and pagination still work
```

- [ ] **Step 6: Synchronize local main and report completion**

```powershell
git pull --ff-only origin main
git status --short
```

Expected: local main includes the bot database commit and is clean. Report the workflow URL, Pages build commit, article count, active tags, assignment count and production URL.
