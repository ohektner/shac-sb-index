# shac-scheduler (Cloudflare Worker)

Starts the GitHub Actions jobs on time. GitHub's own cron is best effort (jobs run hours late or not at all); a Cloudflare cron trigger is reliable. Every 5 minutes the Worker checks `slots.json` and asks GitHub to start any job whose slot began in the last 5 minutes (`workflow_dispatch`). The jobs still run on GitHub, so public commit times stay real. GitHub's own schedule and the PC backup trigger stay as extra layers; the Worker skips a slot that already has a run, and the odds job's guard skips a late scheduled run.

- `slots.json` is the slot table (UTC). `tests/test_worker_slots.py` fails if it drifts from the workflow `cron:` lines. When you change a cron line, update `slots.json` and redeploy.
- Secret `GITHUB_TOKEN` (set in the Cloudflare dashboard, never in the repo): fine-grained token, `shac-sb-index` only, Actions read and write. It can't read code or the private data repo. Expires after 1 year; renew it and update the secret.
- Check it: open the Worker's `workers.dev` address. It says whether the token works and never starts a job.
- Logs: Cloudflare dashboard, Worker, Observability.
- Deploy: `cd worker && npx wrangler@4 deploy` (first time: `npx wrangler@4 login`).
- Test the logic: `cd worker && node --test`.
