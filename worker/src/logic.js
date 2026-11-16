// Pure scheduling logic, kept apart from the network code so it can be tested alone.

export const TICK_MINUTES = 5;

/** Workflows whose UTC slot started in the last TICK_MINUTES before `now` (a Date). */
export function dueWorkflows(slots, now) {
  const due = [];
  for (const [workflow, times] of Object.entries(slots)) {
    for (const time of times) {
      const [hour, minute] = time.split(":").map(Number);
      const slot = new Date(
        Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), hour, minute),
      );
      const ageMinutes = (now.getTime() - slot.getTime()) / 60000;
      if (ageMinutes >= 0 && ageMinutes < TICK_MINUTES) {
        due.push({ workflow, slot });
      }
    }
  }
  return due;
}

/** True if any run was created at or after (slot - 2 minutes), so the slot is covered. */
export function slotAlreadyCovered(slot, runCreatedAts) {
  const cutoff = slot.getTime() - 2 * 60000;
  return runCreatedAts.some((created) => new Date(created).getTime() >= cutoff);
}

export const RETRY_AFTER_MINUTES = 15;
export const RETRY_WINDOW_MINUTES = 150;
export const MAX_RUNS_PER_SLOT = 3; // the slot's own start plus 2 retries

/**
 * Whether a job whose latest run failed should be started again on a fresh runner.
 * Google News blocks some GitHub runner addresses; a new run usually gets a different one.
 * `runs` are GitHub run objects ({created_at, status, conclusion}) for this workflow.
 */
export function shouldRetry(times, now, runs) {
  const candidates = [];
  for (const dayOffset of [-1, 0]) {
    for (const time of times) {
      const [hour, minute] = time.split(":").map(Number);
      candidates.push(
        new Date(
          Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + dayOffset, hour, minute),
        ),
      );
    }
  }
  const started = candidates.filter((slot) => {
    const age = (now.getTime() - slot.getTime()) / 60000;
    return age >= 0 && age <= RETRY_WINDOW_MINUTES;
  });
  if (started.length === 0) return false;
  const slot = new Date(Math.max(...started.map((s) => s.getTime())));
  const since = runs
    .filter((run) => new Date(run.created_at).getTime() >= slot.getTime() - 2 * 60000)
    .sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
  if (since.length === 0 || since.length >= MAX_RUNS_PER_SLOT) return false;
  if (since.some((run) => run.status !== "completed" || run.conclusion === "success")) return false;
  const waited = (now.getTime() - new Date(since[0].created_at).getTime()) / 60000;
  return waited >= RETRY_AFTER_MINUTES;
}
