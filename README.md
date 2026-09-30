# Claude Review

[中文说明](README.zh-CN.md)

Claude Review is an unofficial local Codex plugin that delegates bounded, high-context, low-reasoning evidence work to Claude Code. Codex remains responsible for judgment, verification, and every write operation.

It is designed for repetitive read-only work such as log classification, large-file extraction, screenshot evidence review, and checklist comparison. It must not be used to delegate architecture, production operations, security or permission decisions, finance, business rules, or code changes.

## Safety and reliability

- Claude Code receives only the `Read`, `Glob`, and `Grep` tools.
- Safe mode disables Claude customizations, hooks, plugins, and MCP servers.
- Artifact paths are mandatory and validated inside the selected working directory.
- Per-run and rolling 24-hour cost limits are enforced before work starts.
- Concurrency, queue size, result size, raw logs, retention, and hard timeout are bounded.
- Jobs are persisted and can be inspected or cancelled.
- Automatic delegation is gated by a recorded benchmark of at least 50 real runs with at least 95% success.

The artifact list is a validated size and instruction boundary, not an operating-system sandbox within the working directory. Do not select a working directory containing out-of-scope secrets.

## Requirements

- Python 3.11 or newer, available as `python` on `PATH`.
- Claude Code CLI 2.1 or newer, available as `claude` on `PATH` and already authenticated.
- A local Codex or ChatGPT desktop environment that supports local marketplace plugins and stdio MCP servers.

This repository distributes a local plugin. It is not a submission to the universal public Plugins Directory; that directory requires a reviewed public HTTPS MCP service for this architecture.

## Installation

Clone this repository into the plugin directory of a local marketplace. For the default personal marketplace layout:

```text
~/.agents/plugins/
├── marketplace.json
└── plugins/
    └── claude-review/
```

Use this marketplace entry:

```json
{
  "name": "claude-review",
  "source": {
    "source": "local",
    "path": "./plugins/claude-review"
  },
  "policy": {
    "installation": "AVAILABLE",
    "authentication": "ON_INSTALL"
  },
  "category": "Productivity"
}
```

Then install it from the marketplace name configured in `marketplace.json`:

```bash
codex plugin add claude-review@personal
```

Start a new Codex chat after installation so the tools and skill are reloaded.

## Configuration

The server uses portable defaults and accepts these optional environment variables through `.mcp.json`:

| Variable | Default | Purpose |
| --- | ---: | --- |
| `CLAUDE_REVIEW_CLAUDE_BIN` | `claude` | Claude Code executable |
| `CLAUDE_REVIEW_MODEL` | inherited | Explicit Claude Code model or alias |
| `CLAUDE_REVIEW_ALLOW_CUSTOM_MODEL` | `false` | Permit a non-standard model identifier |
| `CLAUDE_REVIEW_MAX_TURNS` | `8` | Per-run turn limit |
| `CLAUDE_REVIEW_MAX_BUDGET_USD` | `0.50` | Per-run cost ceiling |
| `CLAUDE_REVIEW_DAILY_BUDGET_USD` | `3.00` | Rolling 24-hour cost ceiling |
| `CLAUDE_REVIEW_MAX_CONCURRENT_JOBS` | `2` | Concurrent process limit |
| `CLAUDE_REVIEW_MAX_QUEUED_JOBS` | `8` | Active and queued job limit |
| `CLAUDE_REVIEW_HARD_TIMEOUT_SEC` | `600` | Process timeout |
| `CLAUDE_REVIEW_STATE_DIR` | system temp | Job and bounded-log storage |

Additional size and retention variables are documented in `.mcp.json` and surfaced by `server_status`.

## Tools

- `review`: run a foreground review.
- `start_review`: queue a background review.
- `review_status`: inspect persisted status and result.
- `cancel_review`: stop a queued or running review.
- `list_reviews`: list recent jobs with cost metadata.
- `server_status`: inspect readiness, limits, budget use, and benchmark gate.

## Validation

Offline checks do not call Claude or spend model credits:

```bash
python scripts/release_audit.py
python -m py_compile scripts/server.py scripts/benchmark.py tests/test_server.py
python -m unittest discover -s tests -p "test_*.py" -v
```

A real benchmark spends provider credits and therefore requires an explicit cost ceiling:

```bash
CLAUDE_REVIEW_MAX_BUDGET_USD=0.05 \
CLAUDE_REVIEW_DAILY_BUDGET_USD=3.00 \
python scripts/benchmark.py --runs 50 --min-success-rate 0.95 --max-total-cost 2.50
```

PowerShell users can set the same environment variables with `$env:NAME='value'` before running the command.

## Privacy and cost

Files inspected by Claude Code are transmitted to the provider configured in the user's Claude Code installation. The plugin never bundles credentials, but it cannot change the provider's retention or billing policy. Review [PRIVACY.md](PRIVACY.md) and the configured provider terms before delegating sensitive material.

## Status

Version `0.2.0` has passed the repository's offline tests and a 50-run real smoke benchmark on the maintainer's Windows environment. That result does not guarantee identical behavior with another provider, model, operating system, or Claude Code version.

## License and independence

Released under the MIT License. This is an independent, unofficial project and is not affiliated with, endorsed by, or sponsored by OpenAI or Anthropic. Claude, Claude Code, ChatGPT, and Codex are trademarks of their respective owners.
