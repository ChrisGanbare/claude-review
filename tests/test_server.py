from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
TEST_STATE = ROOT / ".test-state"
TEST_STATE.mkdir(parents=True, exist_ok=True)
os.environ.update({
    "CLAUDE_REVIEW_STATE_DIR": str(TEST_STATE),
    "CLAUDE_REVIEW_CLAUDE_BIN": sys.executable,
    "CLAUDE_REVIEW_MODEL": "sonnet",
    "CLAUDE_REVIEW_MAX_BUDGET_USD": "0.10",
    "CLAUDE_REVIEW_DAILY_BUDGET_USD": "1.00",
    "CLAUDE_REVIEW_MAX_LOG_BYTES": "1024",
})
SPEC = importlib.util.spec_from_file_location("claude_review_server", ROOT / "scripts" / "server.py")
server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(server)


class ServerTests(unittest.TestCase):
    def test_claude_launcher_resolves(self):
        self.assertTrue(server.CLAUDE_COMMAND)

    def test_rejects_missing_artifacts(self):
        with self.assertRaisesRegex(ValueError, "artifacts is required"):
            server._validate_request({"task": "x", "working_directory": str(ROOT)})

    def test_rejects_outside_artifact(self):
        with self.assertRaisesRegex(ValueError, "outside working_directory"):
            server._validate_request({"task": "x", "working_directory": str(ROOT), "artifacts": [str(ROOT.parent)]})

    def test_event_accumulator_keeps_final_result_and_metadata(self):
        accumulator = server.EventAccumulator()
        event = {"type": "result", "result": "完成", "is_error": False, "subtype": "success", "total_cost_usd": 0.01, "duration_ms": 10, "num_turns": 1, "modelUsage": {"claude-sonnet": {}}}
        accumulator.consume((json.dumps(event) + "\n").encode())
        self.assertEqual(accumulator.result(), "完成")
        self.assertTrue(accumulator.metadata["model_recognized"])

    def test_inherited_model_omits_model_flag(self):
        original = server.MODEL
        try:
            server.MODEL = ""
            command = server._claude_command()
        finally:
            server.MODEL = original
        self.assertNotIn("--model", command)

    def test_bounded_log_retains_tail(self):
        path = TEST_STATE / "bounded.log"
        tail = server.deque([b"a" * 400, b"b" * 400])
        server._write_bounded_log(path, tail, True)
        self.assertLessEqual(path.stat().st_size, 850)
        self.assertTrue(path.read_bytes().startswith(b"[earlier"))

    def test_persisted_running_job_becomes_interrupted(self):
        job_id = "persisted-test"
        payload = {"job_id": job_id, "phase": "running", "created_at": server._utc_now(), "created_epoch": time.time(), "working_directory": str(ROOT), "artifacts": [], "scope": {}, "metadata": {}}
        (server.JOBS_DIR / f"{job_id}.json").write_text(json.dumps(payload), encoding="utf-8")
        server.JOBS.pop(job_id, None)
        server._load_jobs()
        self.assertEqual(server.JOBS[job_id]["phase"], "interrupted")

    def test_budget_accounts_for_active_reservations(self):
        with server.JOBS_LOCK:
            server.JOBS["budget-test"] = {"phase": "queued", "created_epoch": time.time(), "metadata": {}}
            spent, reserved = server._budget_usage_locked()
            server.JOBS.pop("budget-test")
        self.assertGreaterEqual(reserved, server.MAX_BUDGET_USD)
        self.assertGreaterEqual(spent, 0)


if __name__ == "__main__":
    unittest.main()
