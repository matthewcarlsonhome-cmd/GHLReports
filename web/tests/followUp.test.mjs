// No browser or database needed: verify that missing/held counts never look clear.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/followUp.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } });
const { followUpSummary, CORE_FOLLOW_UP_CODES, sourceComplete, reportingMetrics } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const complete = { status: "complete", exhausted: true, error: null, skipped: false, retrieved: 10 };
const coverage = (source, override) => ({ sources: {
  contacts: complete, speed_to_lead: complete, conversations: complete,
  ...(source ? { [source]: { ...complete, ...override } } : {}),
} });
const row = (leads, replies, gate = true) => ({ gate_passed: gate, coverage: coverage(), leads_uncontacted_24h: leads, convos_waiting: replies });

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

test("a single failed source cannot produce a false clear even when the overall gate passed", () => {
  const summary = followUpSummary({ ...row(0, 0), coverage: coverage("contacts", { status: "unavailable", error: "failed" }) });
  assert.equal(summary.leads, null);
  assert.equal(summary.replies, 0);
  assert.equal(summary.status, "check-data");
});
test("missing, skipped, capped and errored sources are not measured totals", () => {
  for (const override of [{ status: "partial" }, { status: "missing" }, { skipped: true }, { exhausted: false }, { error: "failed" }]) {
    assert.equal(sourceComplete({ ...row(0, 0), coverage: coverage("contacts", override) }, "contacts"), false);
  }
  assert.equal(sourceComplete({ ...row(0, 0), coverage: null }, "contacts"), false);
  assert.equal(sourceComplete({ ...row(0, 0), coverage: { sources: {} } }, "contacts"), false);
});
test("unrelated source failures do not hide valid follow-up counts", () => {
  assert.equal(followUpSummary({ ...row(0, 0), coverage: coverage("blogs", { status: "unavailable" }) }).status, "clear");
});
const snapshot = { ...row(3, 5), leads_new_7d: 12, leads_trailing_avg: 10, leads_delta_pct: 20,
  leads_by_source_7d: { Website: 12 }, leads_unassigned_7d: 2,
  speed_to_lead_median_min: 18, speed_to_lead_p90_min: 75, convos_waiting_max_hours: 42 };
test("reporting preserves collected values and does not mutate the snapshot", () => {
  const before = structuredClone(snapshot);
  const report = reportingMetrics(snapshot);
  for (const [key, value] of Object.entries(report)) assert.deepEqual(value, snapshot[key]);
  assert.deepEqual(snapshot, before);
});
test("contact failure masks volume, baseline, ownership and response metrics, but not replies", () => {
  const report = reportingMetrics({ ...snapshot, coverage: coverage("contacts", { status: "unavailable" }) });
  for (const key of ["leads_new_7d", "leads_trailing_avg", "leads_delta_pct", "leads_by_source_7d", "leads_unassigned_7d", "leads_uncontacted_24h", "speed_to_lead_median_min", "speed_to_lead_p90_min"]) assert.equal(report[key], null);
  assert.equal(report.convos_waiting, 5);
  assert.equal(report.convos_waiting_max_hours, 42);
});
test("speed failures preserve lead volume; conversation failures mask oldest reply age", () => {
  const speed = reportingMetrics({ ...snapshot, coverage: coverage("speed_to_lead", { status: "partial", exhausted: false }) });
  assert.equal(speed.speed_to_lead_median_min, null);
  assert.equal(speed.leads_new_7d, 12);
  const replies = reportingMetrics({ ...snapshot, coverage: coverage("conversations", { status: "unavailable" }) });
  assert.equal(replies.convos_waiting, null);
  assert.equal(replies.convos_waiting_max_hours, null);
  assert.equal(replies.speed_to_lead_median_min, 18);
});
test("a held snapshot masks every displayed metric", () => {
  assert.ok(Object.values(reportingMetrics({ ...snapshot, gate_passed: false })).every(value => value === null));
});
