---
name: claude-token-delegation
description: Delegate high-token, low-reasoning, read-only local evidence work to Claude Code and verify its concise result. Use for bounded artifact reading, screenshot review, log classification, extraction, comparison, or repetitive evidence gathering. Do not use for architecture, production, security, permissions, finance, business rules, code edits, or other high-stakes judgment.
---

# Claude Token Delegation

Use the `claude-review` MCP tools only for tasks expensive mainly because of input volume rather than judgment.

## Reliability gate

1. Before automatic delegation, call `server_status`.
2. Automatic delegation is allowed only when startup self-check is ready and `automatic_delegation_gate.passed` is true (at least 50 real runs with at least 95% success).
3. If the gate is not passed, use the plugin only when the user explicitly asks for it, and verify every material claim yourself.
4. Never raise concurrency, per-run budget, or daily budget merely to make a task pass.

## Delegation contract

1. Provide one self-contained task, an absolute working directory, and a small explicit `artifacts` list. Artifact scope is mandatory; broad workspace scans are rejected before spending.
2. Use `review` for short work. Use `start_review`, `review_status`, and `cancel_review` for longer work.
3. Claude is an evidence worker. Keep architecture, root-cause judgment, production, security, identity, permissions, money, business rules, irreversible actions, and all writes with Codex.
4. Treat `DELEGATION_UNSUITABLE:` as a hand-back, not a failure.
5. Verify load-bearing findings against source, runtime state, or tests.
6. Return only useful findings. Do not forward raw streams or internal reasoning.

## Scope and privacy

Claude Code is started in safe mode with only Read, Glob, and Grep. The stricter Claude `--restricted` mode is intentionally not used because it ignores the user's configured authentication/model provider. The artifact list is an enforced request-size boundary and an instruction boundary, but it is not a separate operating-system sandbox inside the working directory. Do not place out-of-scope secrets in a delegated working directory.

Delegation transmits inspected content to the provider configured in Claude Code. `server_status` shows whether the model is explicitly selected or inherited. Do not delegate real credentials, payment data, production secrets, regulated data, or real personal data without explicit user confirmation naming that data and destination.

The server enforces per-run and rolling 24-hour budgets, bounded concurrency and queue size, hard timeout, bounded logs with retention, persisted job status, cancellation, and an explicit model startup check.
