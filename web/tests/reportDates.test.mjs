// A date-only snapshot must not shift to the previous day in Central time.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

async function load(name) {
  const source = await readFile(new URL(`../src/lib/${name}.ts`, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
}
const { fmtDate, fmtDateTime } = await load("format");
const { buildClientSummary } = await load("clientSummary");
test("calendar dates retain the account's reporting day in Central time", () => {
  process.env.TZ = "America/Chicago";
  assert.equal(fmtDate("2026-09-28"), "Sep 28");
  assert.equal(fmtDate(null), "Unknown");
  assert.match(fmtDateTime("2026-09-28T10:30:00Z"), /5:30 AM/);
  const snapshot = new Proxy({ snapshot_date: "2026-09-28" }, { get: (obj, key) => obj[key] ?? null });
  assert.match(buildClientSummary({ name: "Example account" }, snapshot), /September 28, 2026/);
});
