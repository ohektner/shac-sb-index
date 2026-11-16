import test from "node:test";
import assert from "node:assert/strict";
import { dueWorkflows, slotAlreadyCovered } from "../src/logic.js";

const slots = { "odds-snapshot.yml": ["13:17", "21:17"], "cbbd-ingest.yml": ["10:07"] };

test("a tick 3 minutes after a slot starts it", () => {
  const due = dueWorkflows(slots, new Date("2026-11-17T13:20:00Z"));
  assert.deepEqual(due.map((d) => d.workflow), ["odds-snapshot.yml"]);
  assert.equal(due[0].slot.toISOString(), "2026-11-17T13:17:00.000Z");
});

test("the tick before a slot and the tick after its window start nothing", () => {
  assert.equal(dueWorkflows(slots, new Date("2026-11-17T13:15:00Z")).length, 0);
  assert.equal(dueWorkflows(slots, new Date("2026-11-17T13:25:00Z")).length, 0);
});

test("every slot is due on exactly one 5-minute tick", () => {
  let hits = 0;
  for (let minute = 0; minute < 24 * 60; minute += 5) {
    const now = new Date(Date.UTC(2026, 10, 17, 0, minute));
    hits += dueWorkflows(slots, now).length;
  }
  assert.equal(hits, 3);
});

test("a run after the slot covers it; an older run does not", () => {
  const slot = new Date("2026-11-17T13:17:00Z");
  assert.equal(slotAlreadyCovered(slot, ["2026-11-17T13:19:00Z"]), true);
  assert.equal(slotAlreadyCovered(slot, ["2026-11-17T10:00:00Z"]), false);
  assert.equal(slotAlreadyCovered(slot, []), false);
});
