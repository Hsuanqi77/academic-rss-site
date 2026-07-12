import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  buildArticleQuery,
  loadDatabase,
  loadFilterOptions,
  queryArticles,
} from "../../docs/js/db.js";
import { loadSqlJs } from "./sqljs-helper.mjs";

let SQL;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const schema = fs.readFileSync(path.join(root, "src/paper_radar/schema.sql"), "utf8");

test.before(async () => {
  SQL = await loadSqlJs();
});

function createDatabase() {
  const db = new SQL.Database();
  db.run(schema);
  db.run(`
    INSERT INTO journals(id,name,publisher,feed_url,enabled)
      VALUES ('apl','Applied Physics Letters','aip','https://example.test/apl.xml',1);
    INSERT INTO journals(id,name,publisher,feed_url,enabled)
      VALUES ('hidden','Hidden Journal','ieee','https://example.test/hidden.xml',0);
    INSERT INTO tags(id,label) VALUES ('saw','Surface wave'),('baw','Bulk wave');
  `);
  const insert = db.prepare(`
    INSERT INTO articles(
      uid,doi,journal_id,title,abstract,published_at,article_type,article_url,normalized_url,
      oa_status,source_feed_url,first_seen_at,last_updated_at
    ) VALUES (?, ?, 'apl', ?, ?, ?, 'research', ?, ?, 'open',
      'https://example.test/apl.xml', '2026-01-01', '2026-01-01')
  `);
  try {
    for (const article of [
      ["a1", "10.1/a1", "Álpha 100%_ AlScN dual mode", "中文声学 both tags", "2026-03-03", "https://x/a1", "https://x/a1"],
      ["a2", "10.1/a2", "SAW only", "surface wave", "2026-03-02", "https://x/a2", "https://x/a2"],
      ["a3", "10.1/a3", "BAW only", "bulk wave", "2026-03-01", "https://x/a3", "https://x/a3"],
      ["a4", "10.1/a4", "Undated", "no date", null, "https://x/a4", "https://x/a4"],
    ]) insert.run(article);
  } finally {
    insert.free();
  }
  // Intentionally insert a1's labels in reverse display order; aggregation must align by tag id.
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

  assert.match(query.where, /COALESCE\(a\.title/);
  assert.equal((query.where.match(/EXISTS/g) || []).length, 2);
  assert.ok(!query.where.includes(injection));
  assert.ok(!query.where.includes("apl' OR"));
  assert.equal(query.params[":journal"], "apl' OR 1=1 --");
  assert.equal(query.params[":tag0"], "saw");
  assert.equal(query.params[":tag1"], "baw");
  assert.equal(
    query.orderBy,
    "(a.published_at IS NULL) ASC, a.published_at DESC, a.uid DESC",
  );
});

test("real SQLite search keeps raw Unicode while LIKE handles ASCII case only", () => {
  const db = createDatabase();
  try {
    for (const query of ["alscn", "Álpha", "中文", "%_"]) {
      const result = queryArticles(db, { query, tags: [], page: 1, sort: "latest" });
      assert.deepEqual(result.rows.map((row) => row.uid), ["a1"], query);
    }
    const differentAccentCase = queryArticles(db, {
      query: "álpha", tags: [], page: 1, sort: "latest",
    });
    assert.deepEqual(differentAccentCase.rows, []);
  } finally {
    db.close();
  }
});

test("latest and oldest sorts both put missing publication dates last", () => {
  const db = createDatabase();
  try {
    const latest = queryArticles(db, { tags: [], page: 1, sort: "latest" });
    const oldest = queryArticles(db, { tags: [], page: 1, sort: "oldest" });
    assert.deepEqual(latest.rows.map((row) => row.uid), ["a1", "a2", "a3", "a4"]);
    assert.deepEqual(oldest.rows.map((row) => row.uid), ["a3", "a2", "a1", "a4"]);
  } finally {
    db.close();
  }
});

test("real sql.js query uses all-tags semantics and does not duplicate count", () => {
  const db = createDatabase();
  try {
    const both = queryArticles(db, { tags: ["saw", "baw"], page: 1, sort: "latest" });
    assert.equal(both.total, 1);
    assert.deepEqual(both.rows.map((row) => row.uid), ["a1"]);
    assert.equal(both.rows[0].tag_ids, "baw|||saw");
    assert.equal(both.rows[0].tag_labels, "Bulk wave|||Surface wave");

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
    assert.deepEqual(last.rows.map((row) => row.uid), ["a3", "a4"]);

    const first = queryArticles(db, { tags: [], page: -4, sort: "latest" }, { pageSize: 0 });
    assert.equal(first.page, 1);
    assert.equal(first.pageSize, 1);
    assert.deepEqual(first.rows.map((row) => row.uid), ["a1"]);
  } finally {
    db.close();
  }
});

test("direct query tags reject invalid or excessive values before preparing SQL", () => {
  assert.throws(
    () => buildArticleQuery({ tags: Array.from({ length: 33 }, (_, index) => `t${index}`) }),
    /最多 32 个/,
  );
  assert.throws(() => buildArticleQuery({ tags: "saw" }), /必须是数组/);
  assert.throws(() => buildArticleQuery({ tags: [""] }), /不能为空/);
  assert.throws(() => buildArticleQuery({ tags: ["x".repeat(129)] }), /不能超过 128/);

  const deduplicated = buildArticleQuery({ tags: [" saw ", "saw", "baw"] });
  assert.deepEqual(deduplicated.params, { ":tag0": "saw", ":tag1": "baw" });
});

test("filter options match enabled schema v4 values and always free statements", () => {
  const db = createDatabase();
  try {
    const options = loadFilterOptions(db);
    assert.deepEqual(options.journals, [{ id: "apl", name: "Applied Physics Letters" }]);
    assert.deepEqual(options.publishers, [{ id: "aip", name: "aip" }]);
    assert.deepEqual(options.tags, [
      { id: "baw", label: "Bulk wave" },
      { id: "saw", label: "Surface wave" },
    ]);
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

  const source = createDatabase();
  const exported = source.export();
  source.close();
  const real = await loadDatabase({
    initSqlJs: async ({ locateFile }) => {
      assert.equal(locateFile("sql-wasm.wasm"), "sql-wasm.wasm");
      return SQL;
    },
    fetch: async () => ({
      ok: true,
      status: 200,
      arrayBuffer: async () => exported.buffer.slice(
        exported.byteOffset,
        exported.byteOffset + exported.byteLength,
      ),
    }),
    now: () => 456,
  });
  try {
    assert.equal(real.exec("PRAGMA user_version")[0].values[0][0], 4);
  } finally {
    real.close();
  }

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

  const constructionError = new Error("constructor exploded");
  class BrokenDatabase { constructor() { throw constructionError; } }
  await assert.rejects(
    loadDatabase({
      initSqlJs: async ({ locateFile }) => {
        assert.equal(locateFile("sql-wasm.wasm"), "sql-wasm.wasm");
        return { Database: BrokenDatabase };
      },
      fetch: fetchOk,
      now: () => 9,
    }),
    (error) => error.message.includes("无法打开") && error.cause === constructionError,
  );
});
