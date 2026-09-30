#!/usr/bin/env python3
"""Bounded read-only Claude Code delegation server for Codex."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid


SERVER_NAME = "claude-review"
SERVER_VERSION = "0.2.0"
CLAUDE_BIN = os.environ.get("CLAUDE_REVIEW_CLAUDE_BIN", "claude")
MODEL = os.environ.get("CLAUDE_REVIEW_MODEL", "").strip()
ALLOW_CUSTOM_MODEL = os.environ.get("CLAUDE_REVIEW_ALLOW_CUSTOM_MODEL", "false").lower() == "true"
MAX_TURNS = int(os.environ.get("CLAUDE_REVIEW_MAX_TURNS", "8"))
MAX_BUDGET_USD = float(os.environ.get("CLAUDE_REVIEW_MAX_BUDGET_USD", "0.50"))
DAILY_BUDGET_USD = float(os.environ.get("CLAUDE_REVIEW_DAILY_BUDGET_USD", "3.00"))
MAX_CONCURRENT_JOBS = int(os.environ.get("CLAUDE_REVIEW_MAX_CONCURRENT_JOBS", "2"))
MAX_QUEUED_JOBS = int(os.environ.get("CLAUDE_REVIEW_MAX_QUEUED_JOBS", "8"))
SYNC_TIMEOUT_SEC = int(os.environ.get("CLAUDE_REVIEW_SYNC_TIMEOUT_SEC", "90"))
HARD_TIMEOUT_SEC = int(os.environ.get("CLAUDE_REVIEW_HARD_TIMEOUT_SEC", "600"))
MAX_RESULT_CHARS = int(os.environ.get("CLAUDE_REVIEW_MAX_RESULT_CHARS", "12000"))
MAX_LOG_BYTES = int(os.environ.get("CLAUDE_REVIEW_MAX_LOG_BYTES", "262144"))
MAX_LOG_TOTAL_BYTES = int(os.environ.get("CLAUDE_REVIEW_MAX_LOG_TOTAL_BYTES", "5242880"))
LOG_RETENTION_DAYS = int(os.environ.get("CLAUDE_REVIEW_LOG_RETENTION_DAYS", "7"))
MAX_JOB_HISTORY = int(os.environ.get("CLAUDE_REVIEW_MAX_JOB_HISTORY", "200"))
MAX_ARTIFACT_FILES = int(os.environ.get("CLAUDE_REVIEW_MAX_ARTIFACT_FILES", "40"))
MAX_ARTIFACT_BYTES = int(os.environ.get("CLAUDE_REVIEW_MAX_ARTIFACT_BYTES", "20971520"))
REQUIRE_ARTIFACTS = os.environ.get("CLAUDE_REVIEW_REQUIRE_ARTIFACTS", "true").lower() == "true"

STATE_DIR = Path(os.environ.get("CLAUDE_REVIEW_STATE_DIR", str(Path(tempfile.gettempdir()) / "codex-claude-review")))
JOBS_DIR = STATE_DIR / "jobs"
STATE_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.RLock()
JOB_SEMAPHORE = threading.BoundedSemaphore(max(1, MAX_CONCURRENT_JOBS))
TERMINAL_PHASES = {"completed", "failed", "timed_out", "cancelled", "interrupted"}
OFFICIAL_MODEL_PATTERN = re.compile(r"(?:claude|haiku|sonnet|opus)", re.IGNORECASE)


def _resolve_claude_command(value: str) -> list[str]:
    """Resolve native executables and npm-generated Windows command shims."""
    resolved = shutil.which(value)
    if not resolved:
        candidate = Path(value).expanduser()
        if candidate.is_file():
            resolved = str(candidate.resolve())
    if not resolved:
        return []
    suffix = Path(resolved).suffix.lower()
    if os.name == "nt" and suffix in {".cmd", ".bat"}:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", "call", resolved]
    return [resolved]


CLAUDE_COMMAND = _resolve_claude_command(CLAUDE_BIN)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _bounded_text(value: object, limit: int = MAX_RESULT_CHARS) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit] + f"\n\n[truncated after {limit} characters]"


def _decode_cli_output(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _startup_check() -> dict:
    check = {"checked_at": _utc_now(), "ready": True, "warnings": [], "model": MODEL or None}
    try:
        if not CLAUDE_COMMAND:
            raise FileNotFoundError(f"Claude Code executable not found on PATH: {CLAUDE_BIN}")
        completed = subprocess.run(
            [*CLAUDE_COMMAND, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        check["claude_version"] = _bounded_text(_decode_cli_output(completed.stdout), 300)
        check["claude_launcher"] = Path(CLAUDE_COMMAND[-1]).name
        if completed.returncode != 0:
            check["ready"] = False
            check["warnings"].append(f"claude --version exited {completed.returncode}")
    except Exception as error:
        check["ready"] = False
        check["warnings"].append(f"Claude executable unavailable: {type(error).__name__}: {error}")
    if not MODEL:
        check["warnings"].append(
            "Model selection is inherited from Claude Code; set CLAUDE_REVIEW_MODEL for reproducible runs"
        )
    elif not OFFICIAL_MODEL_PATTERN.search(MODEL):
        if ALLOW_CUSTOM_MODEL:
            check["warnings"].append(f"Custom model explicitly allowed: {MODEL}")
        else:
            check["ready"] = False
            check["warnings"].append(f"Unrecognized custom model is blocked: {MODEL}")
    return check


STARTUP_CHECK = _startup_check()


def _artifact_inventory(cwd: Path, paths: list[Path]) -> tuple[int, int]:
    file_count = 0
    total_bytes = 0
    seen: set[Path] = set()
    for path in paths:
        candidates = [path] if path.is_file() else (item for item in path.rglob("*") if item.is_file())
        for item in candidates:
            resolved = item.resolve()
            if resolved in seen:
                continue
            try:
                resolved.relative_to(cwd)
            except ValueError as error:
                raise ValueError(f"artifact traversal escaped working_directory: {item}") from error
            seen.add(resolved)
            file_count += 1
            try:
                total_bytes += resolved.stat().st_size
            except OSError:
                pass
            if file_count > MAX_ARTIFACT_FILES:
                raise ValueError(f"artifact scope contains more than {MAX_ARTIFACT_FILES} files")
            if total_bytes > MAX_ARTIFACT_BYTES:
                raise ValueError(f"artifact scope exceeds {MAX_ARTIFACT_BYTES} bytes")
    return file_count, total_bytes


def _validate_request(arguments: dict) -> tuple[str, Path, list[str], dict]:
    task = str(arguments.get("task", "")).strip()
    if not task:
        raise ValueError("task must be a non-empty string")
    if len(task) > 12000:
        raise ValueError("task exceeds the 12000-character limit")
    raw_cwd = str(arguments.get("working_directory", "")).strip()
    if not raw_cwd:
        raise ValueError("working_directory is required")
    cwd = Path(raw_cwd).expanduser().resolve()
    if not cwd.is_dir():
        raise ValueError(f"working_directory does not exist: {cwd}")
    if os.name == "nt" and str(cwd).startswith("\\\\"):
        raise ValueError("UNC working directories are not supported")

    artifacts = arguments.get("artifacts") or []
    if not isinstance(artifacts, list) or len(artifacts) > 50:
        raise ValueError("artifacts must be an array with at most 50 entries")
    if REQUIRE_ARTIFACTS and not artifacts:
        raise ValueError("artifacts is required so broad workspace scans are rejected before spending")

    normalized: list[str] = []
    resolved_paths: list[Path] = []
    for raw_path in artifacts:
        value = str(raw_path).strip()
        if not value:
            continue
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(cwd)
        except ValueError as error:
            raise ValueError(f"artifact is outside working_directory: {value}") from error
        if not candidate.exists():
            raise ValueError(f"artifact does not exist: {value}")
        normalized.append(candidate.relative_to(cwd).as_posix())
        resolved_paths.append(candidate)
    if REQUIRE_ARTIFACTS and not normalized:
        raise ValueError("artifacts must contain at least one existing path")
    file_count, total_bytes = _artifact_inventory(cwd, resolved_paths)
    return task, cwd, normalized, {"artifact_files": file_count, "artifact_bytes": total_bytes}


def _delegated_prompt(task: str, artifacts: list[str]) -> str:
    artifact_block = "\n".join(f"- {path}" for path in artifacts)
    return f"""You are a one-shot read-only evidence worker delegated by Codex.

Use only Read, Glob, and Grep. The artifact list below is the exclusive task scope: do not inspect other workspace files. Never modify files, execute commands, use the network, submit forms, or decide architecture, security, permissions, finance, production operations, or business rules.

If the task requires unavailable files, deep judgment, write access, or an out-of-scope decision, return `DELEGATION_UNSUITABLE:` followed by one concise reason.

Return a concise Chinese result with workspace-relative evidence paths. Separate confirmed facts from uncertainty. Do not include chain-of-thought, tool traces, or generic advice. Reserve the final turn for the answer.

Task:
{task}

Exclusive artifacts:
{artifact_block}
"""


def _claude_command() -> list[str]:
    options = [
        "--safe-mode",
        "--disable-slash-commands",
        "--no-session-persistence",
        "-p",
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--tools", "Read,Glob,Grep",
        "--max-turns", str(MAX_TURNS),
        "--max-budget-usd", str(MAX_BUDGET_USD),
        "--effort", "low",
        "--permission-mode", "dontAsk",
        "--permission-prompts", "none",
    ]
    if MODEL:
        options[0:0] = ["--model", MODEL]
    return [*CLAUDE_COMMAND, *options]


def _terminate_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


class EventAccumulator:
    def __init__(self) -> None:
        self.final_result = ""
        self.last_assistant_text = ""
        self.metadata: dict = {}
        self.diagnostics: list[str] = []

    def consume(self, raw_line: bytes) -> None:
        line = _decode_cli_output(raw_line).strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            if len(self.diagnostics) < 8:
                self.diagnostics.append(_bounded_text(line, 1000))
            return
        if event.get("type") == "assistant":
            blocks = (event.get("message") or {}).get("content") or []
            text_parts = [str(block.get("text", "")) for block in blocks if isinstance(block, dict) and block.get("type") == "text"]
            if text_parts:
                self.last_assistant_text = "\n".join(text_parts).strip()
        if event.get("type") == "result":
            self.final_result = str(event.get("result") or "").strip()
            model_usage = event.get("modelUsage") or {}
            models = list(model_usage.keys())
            self.metadata = {
                "is_error": bool(event.get("is_error")),
                "subtype": event.get("subtype"),
                "total_cost_usd": event.get("total_cost_usd"),
                "duration_ms": event.get("duration_ms"),
                "num_turns": event.get("num_turns"),
                "models": models,
                "terminal_reason": event.get("terminal_reason"),
                "api_error_status": event.get("api_error_status"),
                "model_recognized": all(OFFICIAL_MODEL_PATTERN.search(name or "") for name in models) if models else None,
            }

    def result(self) -> str:
        return _bounded_text(self.final_result or self.last_assistant_text)


def _job_public(job: dict, include_result: bool = True) -> dict:
    snapshot = {
        "job_id": job["job_id"],
        "phase": job["phase"],
        "created_at": job["created_at"],
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "working_directory": job["working_directory"],
        "artifacts": job.get("artifacts", []),
        "scope": job.get("scope", {}),
        "metadata": job.get("metadata", {}),
    }
    if job.get("error"):
        snapshot["error"] = job["error"]
    if include_result and job["phase"] in TERMINAL_PHASES:
        snapshot["result"] = job.get("result", "")
        if job.get("diagnostics") and job["phase"] != "completed":
            snapshot["diagnostics"] = job["diagnostics"]
    return snapshot


def _persist_job(job: dict) -> None:
    payload = _job_public(job, include_result=True)
    payload["created_epoch"] = job.get("created_epoch", time.time())
    path = JOBS_DIR / f"{job['job_id']}.json"
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


def _load_jobs() -> None:
    paths = sorted(JOBS_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime)[-MAX_JOB_HISTORY:]
    for path in paths:
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            if job.get("phase") not in TERMINAL_PHASES:
                job["phase"] = "interrupted"
                job["error"] = "MCP server restarted before the job reached a terminal state"
                job["finished_at"] = _utc_now()
            job["done"] = threading.Event()
            job["done"].set()
            job["cancel_requested"] = threading.Event()
            JOBS[job["job_id"]] = job
            _persist_job(job)
        except Exception:
            continue


def _cleanup_state() -> None:
    cutoff = time.time() - max(1, LOG_RETENTION_DAYS) * 86400
    logs = sorted(STATE_DIR.glob("*.stream.log"), key=lambda item: item.stat().st_mtime)
    for path in list(logs):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                logs.remove(path)
        except OSError:
            pass
    total = sum(path.stat().st_size for path in logs if path.exists())
    for path in logs:
        if total <= MAX_LOG_TOTAL_BYTES:
            break
        try:
            size = path.stat().st_size
            path.unlink()
            total -= size
        except OSError:
            pass
    job_files = sorted(JOBS_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime)
    for path in job_files[:-MAX_JOB_HISTORY]:
        try:
            path.unlink()
        except OSError:
            pass


def _budget_usage_locked() -> tuple[float, float]:
    cutoff = time.time() - 86400
    spent = 0.0
    reserved = 0.0
    for job in JOBS.values():
        if float(job.get("created_epoch", 0)) < cutoff:
            continue
        cost = (job.get("metadata") or {}).get("total_cost_usd")
        if isinstance(cost, (int, float)):
            spent += float(cost)
        if job.get("phase") not in TERMINAL_PHASES:
            reserved += MAX_BUDGET_USD
    return spent, reserved


def _write_bounded_log(path: Path, tail: deque[bytes], truncated: bool) -> None:
    with path.open("wb") as handle:
        if truncated:
            handle.write(b"[earlier stream output truncated]\n")
        for line in tail:
            handle.write(line)


def _reader(stdout, output_queue: queue.Queue) -> None:
    try:
        while True:
            line = stdout.readline()
            if not line:
                break
            output_queue.put(line)
    finally:
        output_queue.put(None)


def _run_job(job: dict, task: str, cwd: Path, artifacts: list[str]) -> None:
    acquired = False
    process = None
    try:
        JOB_SEMAPHORE.acquire()
        acquired = True
        if job["cancel_requested"].is_set():
            with JOBS_LOCK:
                job["phase"] = "cancelled"
                job["finished_at"] = _utc_now()
                _persist_job(job)
            return
        with JOBS_LOCK:
            job["phase"] = "starting_claude"
            job["started_at"] = _utc_now()
            _persist_job(job)

        flags = 0
        popen_kwargs: dict = {}
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(
            _claude_command(), cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, creationflags=flags, **popen_kwargs,
        )
        with JOBS_LOCK:
            job["process"] = process
            job["phase"] = "running"
            _persist_job(job)
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(_delegated_prompt(task, artifacts).encode("utf-8"))
        process.stdin.close()

        output_queue: queue.Queue = queue.Queue(maxsize=256)
        reader = threading.Thread(target=_reader, args=(process.stdout, output_queue), daemon=True)
        reader.start()
        accumulator = EventAccumulator()
        tail: deque[bytes] = deque()
        tail_bytes = 0
        truncated = False
        deadline = time.monotonic() + HARD_TIMEOUT_SEC
        stream_finished = False
        while not stream_finished:
            if job["cancel_requested"].is_set():
                _terminate_process_tree(process)
                break
            if time.monotonic() >= deadline:
                _terminate_process_tree(process)
                with JOBS_LOCK:
                    job["phase"] = "timed_out"
                    job["error"] = f"Claude exceeded the {HARD_TIMEOUT_SEC}s hard timeout"
                break
            try:
                line = output_queue.get(timeout=0.2)
            except queue.Empty:
                if process.poll() is not None and not reader.is_alive():
                    break
                continue
            if line is None:
                stream_finished = True
                continue
            accumulator.consume(line)
            tail.append(line)
            tail_bytes += len(line)
            while tail and tail_bytes > MAX_LOG_BYTES:
                tail_bytes -= len(tail.popleft())
                truncated = True
        reader.join(timeout=2)
        returncode = process.wait(timeout=5)
        _write_bounded_log(STATE_DIR / f"{job['job_id']}.stream.log", tail, truncated)

        result = accumulator.result()
        metadata = accumulator.metadata
        if truncated:
            metadata["stream_log_truncated"] = True
        if metadata.get("models") and not metadata.get("model_recognized"):
            metadata["model_warning"] = "Claude Code reported a non-standard model identifier"
        cancelled = job["cancel_requested"].is_set()
        failed = returncode != 0 or metadata.get("is_error") or not accumulator.final_result
        with JOBS_LOCK:
            if cancelled:
                job["phase"] = "cancelled"
                job["error"] = "Cancelled by caller"
            elif job.get("phase") != "timed_out":
                job["phase"] = "failed" if failed else "completed"
                if failed:
                    details = [f"exit={returncode}"]
                    for key in ("subtype", "terminal_reason", "api_error_status", "num_turns", "models"):
                        if metadata.get(key) not in (None, [], ""):
                            details.append(f"{key}={metadata[key]}")
                    if result:
                        details.append(f"result={_bounded_text(result, 200)!r}")
                    job["error"] = "Claude review failed (" + ", ".join(details) + ")"
            job["result"] = result
            job["metadata"] = metadata
            job["diagnostics"] = accumulator.diagnostics
            job["finished_at"] = _utc_now()
            _persist_job(job)
    except Exception as error:
        if process is not None:
            _terminate_process_tree(process)
        with JOBS_LOCK:
            job["phase"] = "failed"
            job["error"] = _bounded_text(f"{type(error).__name__}: {error}", 2000)
            job["diagnostics"] = [_bounded_text(traceback.format_exc(), 4000)]
            job["finished_at"] = _utc_now()
            _persist_job(job)
    finally:
        with JOBS_LOCK:
            job.pop("process", None)
            job["done"].set()
        if acquired:
            JOB_SEMAPHORE.release()
        _cleanup_state()


def _start_job(arguments: dict) -> dict:
    if not STARTUP_CHECK["ready"]:
        raise ValueError("server self-check failed: " + "; ".join(STARTUP_CHECK["warnings"]))
    task, cwd, artifacts, scope = _validate_request(arguments)
    with JOBS_LOCK:
        active = sum(1 for job in JOBS.values() if job.get("phase") not in TERMINAL_PHASES)
        if active >= MAX_QUEUED_JOBS:
            raise ValueError(f"job queue is full ({MAX_QUEUED_JOBS})")
        spent, reserved = _budget_usage_locked()
        if spent + reserved + MAX_BUDGET_USD > DAILY_BUDGET_USD + 1e-9:
            raise ValueError(
                f"24-hour budget would be exceeded: spent={spent:.4f}, reserved={reserved:.4f}, "
                f"next_limit={MAX_BUDGET_USD:.4f}, daily_limit={DAILY_BUDGET_USD:.4f}"
            )
        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id, "phase": "queued", "created_at": _utc_now(), "created_epoch": time.time(),
            "working_directory": str(cwd), "artifacts": artifacts, "scope": scope,
            "task_sha256": hashlib.sha256(task.encode("utf-8")).hexdigest(),
            "done": threading.Event(), "cancel_requested": threading.Event(),
            "result": "", "metadata": {}, "diagnostics": [], "error": "",
        }
        JOBS[job_id] = job
        _persist_job(job)
    threading.Thread(
        target=_run_job, args=(job, task, cwd, artifacts),
        name=f"claude-review-{job_id[:8]}", daemon=True,
    ).start()
    return job


def _cancel_job(job_id: str) -> dict:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise ValueError(f"unknown job_id: {job_id}")
        if job["phase"] in TERMINAL_PHASES:
            return _job_public(job)
        job["cancel_requested"].set()
        process = job.get("process")
    if process is not None:
        _terminate_process_tree(process)
    return _job_public(job)


def _benchmark_gate() -> dict:
    path = STATE_DIR / "benchmark-latest.json"
    if not path.exists():
        return {"passed": False, "reason": "No 50-run benchmark result is recorded"}
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        passed = int(result.get("runs", 0)) >= 50 and float(result.get("success_rate", 0)) >= 0.95
        return {"passed": passed, **result}
    except Exception as error:
        return {"passed": False, "reason": f"Invalid benchmark record: {error}"}


TOOLS = [
    {
        "name": "review",
        "description": "Run one bounded read-only Claude review. Artifacts are mandatory; broad workspace scans are rejected.",
        "inputSchema": {"type": "object", "properties": {"task": {"type": "string"}, "working_directory": {"type": "string"}, "artifacts": {"type": "array", "items": {"type": "string"}}}, "required": ["task", "working_directory", "artifacts"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "start_review",
        "description": "Queue a bounded read-only Claude review and return its job id.",
        "inputSchema": {"type": "object", "properties": {"task": {"type": "string"}, "working_directory": {"type": "string"}, "artifacts": {"type": "array", "items": {"type": "string"}}}, "required": ["task", "working_directory", "artifacts"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "review_status",
        "description": "Read a persisted job status and optional final result.",
        "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string"}, "wait_seconds": {"type": "integer", "minimum": 0, "maximum": 30, "default": 0}}, "required": ["job_id"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "cancel_review",
        "description": "Cancel a queued or running Claude review and terminate its process tree.",
        "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
    {
        "name": "list_reviews",
        "description": "List recent persisted jobs, including cost and failure metadata.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "server_status",
        "description": "Show startup self-check, limits, 24-hour budget use, and automatic-delegation benchmark gate.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
]


def _tool_result(payload: dict, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}], "structuredContent": payload, "isError": is_error}


def _call_tool(name: str, arguments: dict) -> dict:
    if name == "review":
        job = _start_job(arguments)
        job["done"].wait(timeout=SYNC_TIMEOUT_SEC)
        with JOBS_LOCK:
            snapshot = _job_public(job)
        if not job["done"].is_set():
            snapshot["message"] = "Foreground wait elapsed; use review_status with this job_id."
        return _tool_result(snapshot, snapshot["phase"] in {"failed", "timed_out", "cancelled", "interrupted"})
    if name == "start_review":
        return _tool_result(_job_public(_start_job(arguments), include_result=False))
    if name == "review_status":
        job_id = str(arguments.get("job_id", "")).strip()
        wait_seconds = max(0, min(int(arguments.get("wait_seconds", 0)), 30))
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if job is None:
            raise ValueError(f"unknown job_id: {job_id}")
        job["done"].wait(timeout=wait_seconds)
        snapshot = _job_public(job)
        return _tool_result(snapshot, snapshot["phase"] in {"failed", "timed_out", "cancelled", "interrupted"})
    if name == "cancel_review":
        return _tool_result(_cancel_job(str(arguments.get("job_id", "")).strip()))
    if name == "list_reviews":
        with JOBS_LOCK:
            jobs = list(JOBS.values())[-50:]
            return _tool_result({"jobs": [_job_public(job, include_result=False) for job in jobs]})
    if name == "server_status":
        with JOBS_LOCK:
            spent, reserved = _budget_usage_locked()
            active = sum(1 for job in JOBS.values() if job.get("phase") not in TERMINAL_PHASES)
        return _tool_result({
            "server": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "startup_check": STARTUP_CHECK,
            "limits": {"max_turns": MAX_TURNS, "per_run_budget_usd": MAX_BUDGET_USD, "daily_budget_usd": DAILY_BUDGET_USD, "max_concurrent_jobs": MAX_CONCURRENT_JOBS, "max_queued_jobs": MAX_QUEUED_JOBS, "max_log_bytes": MAX_LOG_BYTES},
            "usage_24h": {"spent_usd": round(spent, 6), "reserved_usd": round(reserved, 6), "active_jobs": active},
            "automatic_delegation_gate": _benchmark_gate(),
        })
    raise ValueError(f"unknown tool: {name}")


def _send(message: dict) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _handle_request(request: dict) -> None:
    request_id = request.get("id")
    if request_id is None:
        return
    try:
        method = request.get("method")
        if method == "initialize":
            client_version = (request.get("params") or {}).get("protocolVersion")
            result = {"protocolVersion": client_version or "2025-06-18", "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}, "instructions": "Use only for artifact-scoped, low-reasoning, read-only evidence work. Automatic delegation requires a passing 50-run benchmark gate."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = request.get("params") or {}
            result = _call_tool(str(params.get("name", "")), params.get("arguments") or {})
        else:
            _send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})
            return
        _send({"jsonrpc": "2.0", "id": request_id, "result": result})
    except Exception as error:
        _send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602 if isinstance(error, ValueError) else -32603, "message": _bounded_text(f"{type(error).__name__}: {error}", 2000)}})


def main() -> int:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    _cleanup_state()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(request, dict):
            _handle_request(request)
    return 0


_load_jobs()

if __name__ == "__main__":
    raise SystemExit(main())
