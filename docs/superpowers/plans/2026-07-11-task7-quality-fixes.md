# Task 7 Quality Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all seven Task 7 quality-review gaps without weakening feed/item isolation, persistence accounting, or HTTP politeness.

**Architecture:** Keep transport policy in `http_client.py`, canonical persisted-record reconstruction in `database.py`, and orchestration contracts/lifecycle in `pipeline.py`. Each subsystem gets a focused RED/GREEN cycle and its own commit; the final gate reruns Task 7, repository/schema, concurrency, and the full suite.

**Tech Stack:** Python 3.11, HTTPX, SQLite, pytest, Ruff.

---

### Task 1: HTTP hook ordering and retry policy

**Files:**
- Modify: `src/paper_radar/http_client.py`
- Modify: `tests/test_http_client.py`

- [ ] **Step 1: Write failing hook-order and concurrency tests**

Add tests where caller hooks mutate `/a` to another origin before pacing, and where a blocking caller hook allows `/b` to reach the scheduler first. Assert transport arrival reservations remain at least the configured interval and that `_owned_direct_client` clones preserve the same caller-hook-then-pacing order.

```python
def mutate_origin(request):
    request.url = request.url.copy_with(host="mutated.test")

client = PoliteClient(event_hooks={"request": [mutate_origin]}, clock=clock, sleeper=sleep)
assert sleeps == [0.4]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/Scripts/python.exe -m pytest -q tests/test_http_client.py -k "hook or blocking or clone"`

Expected: hook-order/origin assertions fail because pacing currently runs first.

- [ ] **Step 3: Append the pacing hook after caller hooks**

```python
copied_hooks["request"] = [*copied_hooks.get("request", []), self._pace_request]
```

- [ ] **Step 4: Write failing Retry-After and permanent-status tests**

```python
@pytest.mark.parametrize(("value", "expected"), [("0", 0.5), ("9" * 400, 60.0)])
def test_retry_after_numeric_bounds(value, expected): ...

@pytest.mark.parametrize("status", [501, 505])
def test_permanent_5xx_is_not_retried(status): ...
```

- [ ] **Step 5: Run tests and verify RED**

Run: `.venv/Scripts/python.exe -m pytest -q tests/test_http_client.py -k "retry_after or permanent"`

Expected: huge numeric Retry-After falls back to exponential delay and 501/505 retry.

- [ ] **Step 6: Implement bounded integer parsing and explicit retryable 5xx policy**

```python
if value.isdigit():
    return float(min(int(value), int(cap)))
return _http_date_retry_after(value, wall_clock, cap)

return status in {408, 425, 429, 500, 502, 503, 504} or 506 <= status <= 599
```

- [ ] **Step 7: Run focused tests and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_http_client.py`

Commit: `fix: harden HTTP pacing and retry policy`

### Task 2: Reconstruct and classify canonical persisted articles

**Files:**
- Modify: `src/paper_radar/database.py`
- Modify: `src/paper_radar/pipeline.py`
- Modify: `tests/test_database_repository.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing repository reconstruction tests**

Test `get_article(connection, uid)` for all `ArticleRecord` fields, safe authors/enriched-fields JSON reconstruction, `None` for a missing row, and rejection of corrupt provenance JSON.

```python
stored = get_article(connection, record.uid)
assert stored == record
assert get_article(connection, "missing") is None
```

- [ ] **Step 2: Run repository tests and verify RED**

Run: `.venv/Scripts/python.exe -m pytest -q tests/test_database_repository.py -k get_article`

Expected: import/function missing.

- [ ] **Step 3: Implement `get_article`**

Select canonical columns by UID and construct `ArticleRecord`, requiring a JSON list of strings for authors and using the repository's strict enriched-field deserializer.

```python
return ArticleRecord(
    uid=row["uid"], doi=row["doi"], journal_id=row["journal_id"],
    title=row["title"], abstract=row["abstract"], authors=tuple(authors),
    published_at=row["published_at"], article_type=row["article_type"],
    article_url=row["article_url"], normalized_url=row["normalized_url"],
    oa_status=row["oa_status"], source_feed_url=row["source_feed_url"],
    metadata_status=row["metadata_status"],
    enriched_fields=_json_enriched_fields(row["enriched_fields_json"]),
)
```

- [ ] **Step 4: Write failing merged-classification pipeline tests**

Cover a stored `SAW article` followed by same-identity `Untitled`, a merged retained abstract supplying the only topic match, and a forced missing canonical row that is counted as an item failure.

- [ ] **Step 5: Run tests and verify RED**

Run: `.venv/Scripts/python.exe -m pytest -q tests/test_pipeline.py -k "persisted or merged or canonical"`

Expected: stale incoming metadata removes tags.

- [ ] **Step 6: Reload before classifying**

```python
outcome = upsert_article(connection, article)
persisted_uid = resolve_article_uid(connection, article)
canonical = get_article(connection, persisted_uid)
if canonical is None:
    raise RuntimeError("persisted article could not be reloaded")
matched_topics = classify_article(canonical, topic_list)
replace_article_tags(connection, persisted_uid, matched_topics)
```

- [ ] **Step 7: Run repository/pipeline tests and commit**

Commit: `fix: classify canonical persisted articles`

### Task 3: Pipeline callback contracts and partial validator healing

**Files:**
- Modify: `src/paper_radar/pipeline.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing preflight contract tests**

Test non-callable fetcher/enricher/sleeper and non-HTTPX client before DB creation. Test wrong callback signatures, callback `TypeError`, wrong fetch return, and wrong enrich return propagating as contract errors rather than summaries.

```python
with pytest.raises(PipelineConfigurationError):
    update_database(path, feeds, topics, fetcher=None)
assert not path.exists()
```

- [ ] **Step 2: Run contract tests and verify RED**

Expected: invalid inputs create a DB or become ordinary feed/item failures.

- [ ] **Step 3: Implement contract errors and wrappers**

```python
class PipelineConfigurationError(TypeError): ...
class PipelineContractError(RuntimeError): ...

def _call_fetcher(...):
    try:
        result = fetcher(...)
    except TypeError as exc:
        raise PipelineContractError("fetcher call contract failed") from exc
    if not isinstance(result, FeedFetchResult):
        raise PipelineContractError("fetcher must return FeedFetchResult")
    return result
```

Explicitly re-raise `PipelineContractError` before ordinary feed/item `Exception` isolation.

- [ ] **Step 4: Write failing two-run partial-validator regression**

Seed an old validator, return a new validator plus a tag/item failure, then rerun successfully. Assert the second fetch receives the old validator, refetches `200`, repairs tags, and only then advances the validator.

- [ ] **Step 5: Run and verify RED**

Expected: the second request receives the new validator and can be incorrectly suppressed by `304`.

- [ ] **Step 6: Preserve pre-fetch validators on partial status**

```python
mark_journal_status(
    connection, feed.id, status="partial", error=diagnostic,
    etag=etag, last_modified=last_modified,
)
```

- [ ] **Step 7: Run pipeline tests and commit**

Commit: `fix: enforce pipeline contracts and healing retries`

### Task 4: Reliable terminal run finalization

**Files:**
- Modify: `src/paper_radar/pipeline.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing one-shot and exhausted finalization tests**

Monkeypatch `finish_run` so the first terminal write raises `sqlite3.OperationalError`. Assert injected backoff, a recovered returned summary, and a terminal row. Add exhaustion followed by fallback success, plus all-attempts-fail preserving the original exception without double-finishing successful runs.

- [ ] **Step 2: Run tests and verify RED**

Expected: `finish_attempted=True` prevents fallback and leaves `running`.

- [ ] **Step 3: Implement success-based bounded finalization**

```python
run_finished = False
try:
    _retry_finish(..., attempts=3, sleeper=sleeper)
    run_finished = True
except BaseException:
    if not run_finished:
        _attempt_error_finish(...)
    raise
```

Retry only `sqlite3.OperationalError` with deterministic `0.5`, `1.0` delays. Set `run_finished` only after success; fallback error-finalization also uses the bounded retry and never retries an already successful run.

- [ ] **Step 4: Run finalization tests repeatedly**

Run: `.venv/Scripts/python.exe -m pytest -q tests/test_pipeline.py -k finish --count=5` when repeat support exists; otherwise run the focused command several times.

- [ ] **Step 5: Run all gates and commit**

Run Task 7 tests, database/schema/repository tests, full pytest, pacing tests repeatedly, Ruff, formatting, and diff checks.

Commit: `fix: make run finalization retryable`

---

## Self-Review

- Spec coverage: all seven review findings map to Tasks 1-4.
- No placeholder steps remain; every change has an explicit test, command, and implementation shape.
- Type consistency: `get_article`, `PipelineConfigurationError`, `PipelineContractError`, `journal_status_errors`, and `run_finished` use the same names throughout.
