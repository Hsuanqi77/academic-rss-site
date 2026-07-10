const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { test, after } = require("node:test");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const docs = path.join(root, "docs");
const edge = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const contentTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".wasm": "application/wasm",
};

const server = http.createServer((request, response) => {
  const pathname = new URL(request.url, "http://127.0.0.1").pathname;
  const relative = pathname === "/" ? "index.html" : pathname.slice(1);
  const filename = path.resolve(docs, relative);
  if (!filename.startsWith(`${docs}${path.sep}`)) {
    response.writeHead(403).end();
    return;
  }
  fs.readFile(filename, (error, content) => {
    if (error) {
      response.writeHead(404).end();
      return;
    }
    response.writeHead(200, { "Content-Type": contentTypes[path.extname(filename)] || "application/octet-stream" });
    response.end(content);
  });
});

let browser;
let origin;

async function pageAt(width) {
  if (!browser) {
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    origin = `http://127.0.0.1:${server.address().port}`;
    browser = await chromium.launch({ executablePath: edge, headless: true });
  }
  const page = await browser.newPage({ viewport: { width, height: 800 } });
  await page.goto(origin);
  await page.waitForFunction(() => document.querySelector("#status")?.textContent.includes("界面已准备"));
  return page;
}

after(async () => {
  if (browser) await browser.close();
  if (server.listening) await new Promise((resolve) => server.close(resolve));
});

test("mobile drawer becomes a modal and isolates every marked background region", async () => {
  const page = await pageAt(500);
  await page.locator("#open-filters").click();

  const state = await page.evaluate(() => ({
    role: document.querySelector("#filters").getAttribute("role"),
    modal: document.querySelector("#filters").getAttribute("aria-modal"),
    expanded: document.querySelector("#open-filters").getAttribute("aria-expanded"),
    bodyLocked: document.body.classList.contains("drawer-open"),
    overlayHidden: document.querySelector("#filter-overlay").hidden,
    active: document.activeElement.id,
    backgrounds: [...document.querySelectorAll("[data-drawer-background]")].map((element) => ({
      id: element.id || element.tagName,
      inert: element.inert,
      hidden: element.getAttribute("aria-hidden"),
    })),
  }));
  assert.equal(state.role, "dialog");
  assert.equal(state.modal, "true");
  assert.equal(state.expanded, "true");
  assert.equal(state.bodyLocked, true);
  assert.equal(state.overlayHidden, false);
  assert.equal(state.active, "close-filters");
  assert.ok(state.backgrounds.length >= 5);
  assert.ok(state.backgrounds.every((item) => item.inert && item.hidden === "true"));
  await page.close();
});

test("Tab and Shift+Tab wrap across the mobile modal controls", async () => {
  const page = await pageAt(500);
  await page.locator("#open-filters").click();

  await page.keyboard.press("Shift+Tab");
  assert.equal(await page.evaluate(() => document.activeElement.id), "clear-filters");
  await page.keyboard.press("Tab");
  assert.equal(await page.evaluate(() => document.activeElement.id), "close-filters");
  await page.close();
});

test("Escape and overlay close the drawer and restore its trigger focus", async () => {
  const page = await pageAt(500);
  await page.locator("#open-filters").click();
  await page.keyboard.press("Escape");
  assert.equal(await page.evaluate(() => document.activeElement.id), "open-filters");

  await page.locator("#open-filters").click();
  await page.mouse.click(480, 400);
  assert.equal(await page.evaluate(() => document.activeElement.id), "open-filters");
  assert.equal(await page.locator("#filters").getAttribute("role"), null);
  assert.equal(await page.locator("#filters").getAttribute("aria-modal"), null);
  await page.close();
});

test("desktop to closed mobile moves drawer focus before making it inert", async () => {
  const page = await pageAt(1000);
  await page.locator("#date-from").focus();
  await page.setViewportSize({ width: 500, height: 800 });
  await page.waitForFunction(() => document.querySelector("#filters").inert);

  assert.equal(await page.evaluate(() => document.activeElement.id), "open-filters");
  assert.equal(await page.locator("#filters").getAttribute("aria-hidden"), "true");
  await page.close();
});

test("open mobile to desktop removes modal isolation and keeps focus useful", async () => {
  const page = await pageAt(500);
  await page.locator("#open-filters").click();
  await page.setViewportSize({ width: 1000, height: 800 });
  await page.waitForFunction(() => !document.body.classList.contains("drawer-open"));

  const state = await page.evaluate(() => ({
    role: document.querySelector("#filters").getAttribute("role"),
    modal: document.querySelector("#filters").getAttribute("aria-modal"),
    inert: document.querySelector("#filters").inert,
    hidden: document.querySelector("#filters").getAttribute("aria-hidden"),
    active: document.activeElement.id,
    backgrounds: [...document.querySelectorAll("[data-drawer-background]")].map((element) => ({
      inert: element.inert,
      hidden: element.getAttribute("aria-hidden"),
    })),
  }));
  assert.equal(state.role, null);
  assert.equal(state.modal, null);
  assert.equal(state.inert, false);
  assert.equal(state.hidden, null);
  assert.equal(state.active, "date-from");
  assert.ok(state.backgrounds.every((item) => !item.inert && item.hidden === null));
  await page.close();
});
