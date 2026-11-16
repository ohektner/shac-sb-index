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
