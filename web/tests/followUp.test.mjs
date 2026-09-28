// No browser or database needed: verify that missing/held counts never look clear.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/followUp.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } });
const { followUpSummary, CORE_FOLLOW_UP_CODES } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const row = (leads, replies, gate = true) => ({ gate_passed: gate, leads_uncontacted_24h: leads, convos_waiting: replies });

test("only verified zeros are clear", () => {
  assert.equal(followUpSummary(row(0, 0)).status, "clear");
  for (const input of [null, row(null, 0), row(0, null), row(null, null), row(0, 0, null)]) {
    assert.equal(followUpSummary(input).status, "check-data");
  }
});
test("held snapshots do not surface untrusted counts or action prompts", () => {
  const summary = followUpSummary(row(12, 40, false));
  assert.equal(summary.leads, null);
  assert.equal(summary.replies, null);
  assert.equal(summary.status, "check-data");
  assert.match(summary.action, /coverage/);
});
test("known follow-up stays visible when the other count is unavailable", () => {
  for (const input of [row(2, null), row(null, 5)]) {
    const summary = followUpSummary(input);
    assert.equal(summary.status, "follow-up");
    assert.equal(summary.incomplete, true);
  }
});
test("work queues include small counts without claiming notification eligibility", () => {
  const summary = followUpSummary(row(1, 1));
  assert.equal(summary.status, "follow-up");
  assert.match(summary.action, /contact missed leads and reply/);
  assert.doesNotMatch(summary.action, /sent|email|trigger/i);
});
test("follow-up sorts first, data review next, verified clear last", () => {
  const list = [row(0, 0), row(null, null), row(2, 0)].sort((a, b) => followUpSummary(a).rank - followUpSummary(b).rank);
  assert.deepEqual(list.map((item) => followUpSummary(item).status), ["follow-up", "check-data", "clear"]);
});
test("core action list excludes unrelated reporting issues", () => {
  assert.deepEqual([...CORE_FOLLOW_UP_CODES], ["SLOW_RESPONSE", "CONVOS_WAITING"]);
  assert.equal(CORE_FOLLOW_UP_CODES.has("PIPELINE_FROZEN"), false);
});
