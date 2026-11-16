"""worker/slots.json (what the Cloudflare scheduler starts) must equal the workflow crons."""

import json
from pathlib import Path

from shac.snapshots.backup_trigger import cron_slots

ROOT = Path(__file__).resolve().parents[1]


def test_worker_slots_match_workflow_crons():
    slots = json.loads((ROOT / "worker" / "slots.json").read_text())
    assert slots, "worker/slots.json is empty"
    for workflow, times in slots.items():
        expected = [
            f"{hour:02d}:{minute:02d}"
            for hour, minute in cron_slots((ROOT / ".github" / "workflows" / workflow).read_text())
        ]
        assert times == expected, f"{workflow}: worker/slots.json is out of date"
