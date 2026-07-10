import assert from "node:assert/strict";
import test from "node:test";

import {
  buildArticleQuery,
  loadDatabase,
  loadFilterOptions,
  queryArticles,
} from "../../docs/js/db.js";
import { loadSqlJs } from "./sqljs-helper.mjs";

let SQL;

test.before(async () => {
  SQL = await loadSqlJs();
});

function createDatabase() {
  const db = new SQL.Database();
  db.run(`
    CREATE TABLE journals(id TEXT PRIMARY KEY, name TEXT, publisher TEXT, enabled INTEGER);
    CREATE TABLE articles(
      uid TEXT PRIMARY KEY, doi TEXT, journal_id TEXT, title TEXT, abstract TEXT,
      authors_json TEXT, published_at TEXT, article_type TEXT, article_url TEXT,
      normalized_url TEXT, oa_status TEXT, source_feed_url TEXT, metadata_status TEXT,
      first_seen_at TEXT, last_updated_at TEXT, enriched_fields_json TEXT
    );
    CREATE TABLE tags(id TEXT PRIMARY KEY, label TEXT);
    CREATE TABLE article_tags(article_uid TEXT, tag_id TEXT, PRIMARY KEY(article_uid, tag_id));
    INSERT INTO journals VALUES ('apl','Applied Physics Letters','aip',1);
    INSERT INTO journals VALUES ('hidden','Hidden Journal','ieee',0);
    INSERT INTO tags VALUES ('saw','SAW'),('baw','BAW');
  `);
  const insert = db.prepare(`
    INSERT INTO articles VALUES (?, ?, 'apl', ?, ?, '[]', ?, 'research', ?, ?, 'open',
      'feed', 'rss_only', '2026-01-01', '2026-01-01', '[]')
  `);
  try {
    for (const article of [
      ["a1", "10.1/a1", "AlScN dual mode", "both tags", "2026-03-03", "https://x/a1", "https://x/a1"],
      ["a2", "10.1/a2", "SAW only", "surface wave", "2026-03-02", "https://x/a2", "https://x/a2"],
      ["a3", "10.1/a3", "BAW only", "bulk wave", "2026-03-01", "https://x/a3", "https://x/a3"],
    ]) insert.run(article);
  } finally {
    insert.free();
  }
  db.run("INSERT INTO article_tags VALUES ('a1','saw'),('a1','baw'),('a2','saw'),('a3','baw')");
  return db;
}

test("query builder parameterizes injection-shaped text, filters, and every tag", () => {
  const injection = "x%' OR 1=1 --";
  const query = buildArticleQuery({
    query: injection,
    from: "2026-01-01",
    to: "2026-12-31",
    journal: "apl' OR 1=1 --",
    publisher: "aip",
    articleType: "research",
    oaStatus: "open",
    tags: ["saw", "baw"],
    sort: "oldest; DROP TABLE articles",
  });

  assert.match(query.where, /LOWER\(/);
  assert.equal((query.where.match(/EXISTS/g) || []).length, 2);
  assert.ok(!query.where.includes(injection));
  assert.ok(!query.where.includes("apl' OR"));
  assert.equal(query.params[":journal"], "apl' OR 1=1 --");
  assert.equal(query.params[":tag0"], "saw");
  assert.equal(query.params[":tag1"], "baw");
  assert.equal(query.orderBy, "a.published_at DESC, a.uid DESC");
});

test("real sql.js query uses all-tags semantics and does not duplicate count", () => {
  const db = createDatabase();
  try {
    const both = queryArticles(db, { tags: ["saw", "baw"], page: 1, sort: "latest" });
    assert.equal(both.total, 1);
    assert.deepEqual(both.rows.map((row) => row.uid), ["a1"]);
    assert.equal(both.rows[0].tag_ids, "baw|||saw");
    assert.equal(both.rows[0].tag_labels, "BAW|||SAW");

    const saw = queryArticles(db, { tags: ["saw"], page: 1, sort: "latest" });
    assert.equal(saw.total, 2);
    assert.deepEqual(saw.rows.map((row) => row.uid), ["a1", "a2"]);
  } finally {
    db.close();
  }
});

test("query clamps page and page size at both boundaries", () => {
  const db = createDatabase();
  try {
    const last = queryArticles(db, { tags: [], page: 999, sort: "latest" }, { pageSize: 2 });
    assert.equal(last.page, 2);
    assert.equal(last.pageSize, 2);
    assert.deepEqual(last.rows.map((row) => row.uid), ["a3"]);

    const first = queryArticles(db, { tags: [], page: -4, sort: "latest" }, { pageSize: 0 });
    assert.equal(first.page, 1);
    assert.equal(first.pageSize, 1);
    assert.deepEqual(first.rows.map((row) => row.uid), ["a1"]);
  } finally {
    db.close();
  }
});

test("filter options match enabled schema v3 values and always free statements", () => {
  const db = createDatabase();
  try {
    const options = loadFilterOptions(db);
    assert.deepEqual(options.journals, [{ id: "apl", name: "Applied Physics Letters" }]);
    assert.deepEqual(options.publishers, [{ id: "aip", name: "aip" }]);
    assert.deepEqual(options.tags, [{ id: "baw", label: "BAW" }, { id: "saw", label: "SAW" }]);
    assert.deepEqual(options.articleTypes, [{ id: "research", name: "research" }]);
  } finally {
    db.close();
  }

  let freed = false;
  const broken = {
    prepare() {
      return {
        step() { throw new Error("step failed"); },
        getAsObject() { return {}; },
        free() { freed = true; },
      };
    },
  };
  assert.throws(() => loadFilterOptions(broken), /step failed/);
  assert.equal(freed, true);
});

test("query statements are freed when bind or row extraction fails", () => {
  const statements = [];
  const db = {
    prepare() {
      const statement = {
        freeCalled: false,
        bind() { throw new Error("bind failed"); },
        free() { this.freeCalled = true; },
      };
      statements.push(statement);
      return statement;
    },
  };

  assert.throws(() => queryArticles(db, { tags: [], page: 1 }), /bind failed/);
  assert.equal(statements.length, 1);
  assert.equal(statements[0].freeCalled, true);
});

test("database loader uses local WASM, cache busting, and clear validation errors", async () => {
  const sqliteHeader = new Uint8Array(100);
  sqliteHeader.set(new TextEncoder().encode("SQLite format 3\0"));
  const calls = {};
  class FakeDatabase { constructor(bytes) { calls.bytes = bytes; } }
  const init = async (options) => {
    calls.wasm = options.locateFile("sql-wasm.wasm");
    return { Database: FakeDatabase };
  };
  const fetchOk = async (url, options) => {
    calls.url = url;
    calls.fetchOptions = options;
    return { ok: true, status: 200, arrayBuffer: async () => sqliteHeader.buffer };
  };

  const db = await loadDatabase({ initSqlJs: init, fetch: fetchOk, now: () => 123 });
  assert.ok(db instanceof FakeDatabase);
  assert.equal(calls.wasm, "sql-wasm.wasm");
  assert.equal(calls.url, "data/papers.db?v=123");
  assert.deepEqual(calls.fetchOptions, { cache: "no-store" });

  await assert.rejects(
    loadDatabase({ initSqlJs: init, fetch: async () => ({ ok: false, status: 503 }), now: () => 1 }),
    /HTTP 503/,
  );
  await assert.rejects(
    loadDatabase({
      initSqlJs: init,
      fetch: async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(0) }),
      now: () => 1,
    }),
    /为空/,
  );
  await assert.rejects(
    loadDatabase({
      initSqlJs: init,
      fetch: async () => ({ ok: true, arrayBuffer: async () => new Uint8Array(100).buffer }),
      now: () => 1,
    }),
    /不是有效的 SQLite/,
  );
});
