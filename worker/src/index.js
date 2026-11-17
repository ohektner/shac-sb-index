// shac-scheduler: starts the GitHub Actions jobs on time.
//
// GitHub's own cron is best effort and often runs jobs hours late or skips them. A
// Cloudflare cron trigger is reliable, so every 5 minutes this Worker checks the slot
// table (slots.json, kept equal to the workflow files by tests/test_worker_slots.py) and
// asks GitHub to start any job whose slot began in the last 5 minutes. The jobs still run
// on GitHub, so the public commit times stay real.
//
// Needs one secret, GITHUB_TOKEN: a fine-grained token for shac-sb-index only, with
// Actions: read and write. It cannot read code or the private data repo.
import slots from "../slots.json";
import { dueWorkflows, shouldRetry, slotAlreadyCovered } from "./logic.js";

// Jobs worth starting again after a failed run. Empty since 2026-11-17: news-collect, the
// only one, moved to Owen's PC because Google News blocks GitHub's runners.
const RETRY = [];

const REPO = "ohektner/shac-sb-index";
const API = `https://api.github.com/repos/${REPO}`;

function headers(env) {
  return {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "shac-scheduler",
  };
}

// Fails open: if the check breaks, the job is started anyway.
async function covered(env, workflow, slot) {
  try {
    const response = await fetch(`${API}/actions/workflows/${workflow}/runs?per_page=5`, {
      headers: headers(env),
    });
    if (!response.ok) return false;
    const body = await response.json();
    return slotAlreadyCovered(slot, body.workflow_runs.map((run) => run.created_at));
  } catch {
    return false;
  }
}

async function recentRuns(env, workflow) {
  try {
    const response = await fetch(`${API}/actions/workflows/${workflow}/runs?per_page=10`, {
      headers: headers(env),
    });
    if (!response.ok) return null;
    return (await response.json()).workflow_runs;
  } catch {
    return null;
  }
}

async function dispatch(env, workflow) {
  let last = "no attempt";
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const response = await fetch(`${API}/actions/workflows/${workflow}/dispatches`, {
        method: "POST",
        headers: { ...headers(env), "Content-Type": "application/json" },
        body: JSON.stringify({ ref: "main" }),
      });
      if (response.status === 204) return "started";
      last = `HTTP ${response.status}`;
      if (response.status < 500) break; // a 4xx will not fix itself
    } catch (error) {
      last = String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, attempt * 2000));
  }
  return `FAILED (${last})`;
}

export default {
  async scheduled(event, env) {
    const now = new Date(event.scheduledTime);
    for (const { workflow, slot } of dueWorkflows(slots, now)) {
      const label = `${workflow} slot ${slot.toISOString()}`;
      if (await covered(env, workflow, slot)) {
        console.log(`${label}: already has a run, skipped`);
        continue;
      }
      console.log(`${label}: ${await dispatch(env, workflow)}`);
    }
    for (const workflow of RETRY) {
      const runs = await recentRuns(env, workflow);
      if (runs && shouldRetry(slots[workflow], now, runs)) {
        console.log(`${workflow}: last run failed, retrying: ${await dispatch(env, workflow)}`);
      }
    }
  },

  // Visiting the Worker's address shows whether the token works. It never starts a job.
  async fetch(request, env) {
    const check = await fetch(`${API}/actions/workflows`, { headers: headers(env) });
    const text =
      check.status === 200
        ? "shac-scheduler: GitHub token works.\n"
        : `shac-scheduler: GitHub token problem (HTTP ${check.status}).\n`;
    return new Response(text, { status: check.status === 200 ? 200 : 500 });
  },
};
