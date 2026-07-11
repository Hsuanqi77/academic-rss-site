import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { paginationItems, parseAuthors, safeExternalUrl } from "../../docs/js/app.js";

test("application shell exposes every enhanced filter", async () => {
  const html = await readFile(new URL("../../docs/index.html", import.meta.url), "utf8");
  for (const id of [
    "search", "date-from", "date-to", "journal", "publisher", "article-type",
    "oa-status", "tag-options", "sort", "clear-filters", "article-list", "pagination",
  ]) assert.match(html, new RegExp(`id=["']${id}["']`));
});

test("controller imports state and database modules without unsafe HTML sinks", async () => {
  const app = await readFile(new URL("../../docs/js/app.js", import.meta.url), "utf8");
  assert.match(app, /from ["']\.\/db\.js["']/);
  assert.match(app, /from ["']\.\/state\.js["']/);
  assert.doesNotMatch(app, /\.innerHTML\s*=|insertAdjacentHTML|document\.write/u);
  assert.match(app, /textContent/u);
});

test("safe rendering helpers reject active URLs and tolerate malformed authors", () => {
  assert.equal(safeExternalUrl("javascript:alert(1)"), null);
  assert.equal(safeExternalUrl("data:text/html,bad"), null);
  assert.equal(safeExternalUrl("/relative"), null);
  assert.equal(safeExternalUrl("https://example.test/paper"), "https://example.test/paper");
  assert.deepEqual(parseAuthors("not-json"), []);
  assert.deepEqual(parseAuthors('["Ada",{"name":"Grace"},null]'), ["Ada", "Grace"]);
});

test("pagination helper returns a finite accessible window", () => {
  assert.deepEqual(paginationItems(1, 1), [1]);
  assert.deepEqual(paginationItems(5, 20), [1, "ellipsis", 3, 4, 5, 6, 7, "ellipsis", 20]);
  assert.ok(paginationItems(500, 1000).length <= 11);
});
