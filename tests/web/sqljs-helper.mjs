import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const docs = path.join(root, "docs");

export async function loadSqlJs() {
  const filename = path.join(docs, "sql-wasm.js");
  const module = { exports: {} };
  const sandbox = {
    Buffer,
    WebAssembly,
    __dirname: docs,
    __filename: filename,
    clearInterval,
    clearTimeout,
    console,
    module,
    process,
    require,
    setInterval,
    setTimeout,
  };
  sandbox.exports = module.exports;
  vm.runInNewContext(fs.readFileSync(filename, "utf8"), sandbox, { filename });
  return module.exports({ locateFile: (file) => path.join(docs, file) });
}
