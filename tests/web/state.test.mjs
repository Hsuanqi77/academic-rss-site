import assert from "node:assert/strict";
import test from "node:test";

import { DEFAULT_STATE, parseState, serializeState } from "../../docs/js/state.js";

test("URL state round-trips every supported field in stable order", () => {
  const search = "?q=AlScN%20resonator&from=2026-01-02&to=2026-02-03&journal=apl"
    + "&publisher=aip&type=research&oa=open&tag=saw&tag=baw&page=2&sort=oldest";
  const state = parseState(search);

  assert.deepEqual(state, {
    query: "AlScN resonator",
    from: "2026-01-02",
    to: "2026-02-03",
    journal: "apl",
    publisher: "aip",
    articleType: "research",
    oaStatus: "open",
    tags: ["saw", "baw"],
    sort: "oldest",
    page: 2,
  });
  assert.equal(
    serializeState(state),
    "q=AlScN+resonator&from=2026-01-02&to=2026-02-03&journal=apl&publisher=aip"
      + "&type=research&oa=open&tag=saw&tag=baw&sort=oldest&page=2",
  );
  assert.deepEqual(parseState(`?${serializeState(state)}`), state);
});

test("invalid dates, enums, and pages fall back safely", () => {
  const state = parseState(
    "?from=2026-02-30&to=2026-2-03&type=DROP&oa=public&sort=drop-table&page=2oops",
  );

  assert.equal(state.from, "");
  assert.equal(state.to, "");
  assert.equal(state.articleType, "");
  assert.equal(state.oaStatus, "");
  assert.equal(state.sort, "latest");
  assert.equal(state.page, 1);
});

test("empty and repeated tags are trimmed, deduplicated, and isolated per parse", () => {
  const first = parseState("?tag=&tag=%20saw%20&tag=saw&tag=baw&tag=%20");
  const second = parseState();

  assert.deepEqual(first.tags, ["saw", "baw"]);
  first.tags.push("local-only");
  assert.deepEqual(second.tags, []);
  assert.deepEqual(DEFAULT_STATE.tags, []);
  assert.ok(Object.isFrozen(DEFAULT_STATE));
  assert.ok(Object.isFrozen(DEFAULT_STATE.tags));
});

test("oversized and prototype-shaped input cannot alter the state contract", () => {
  const oversized = parseState(`?q=${"x".repeat(9_000)}&page=4`);
  const shaped = parseState("?__proto__=polluted&constructor=bad&q=valid");

  assert.deepEqual(oversized, { ...DEFAULT_STATE, tags: [] });
  assert.equal(shaped.query, "valid");
  assert.equal(Object.hasOwn(shaped, "__proto__"), false);
  assert.equal(Object.hasOwn(shaped, "constructor"), false);
});

test("serialization sanitizes partial caller state without mutating it", () => {
  const source = { tags: [" saw ", "", "saw", "baw"], sort: "invalid", page: -9 };
  const snapshot = structuredClone(source);

  assert.equal(serializeState(source), "tag=saw&tag=baw");
  assert.deepEqual(source, snapshot);
});
