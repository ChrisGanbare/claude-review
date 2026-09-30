#!/usr/bin/env python3
"""Offline public-release audit for the Claude Review repository."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    ".codex-plugin/plugin.json",
    ".mcp.json",
    "README.md",
    "README.zh-CN.md",
    "LICENSE",
    "SECURITY.md",
    "PRIVACY.md",
    "scripts/server.py",
    "tests/test_server.py",
}
TEXT_SUFFIXES = {"", ".json", ".md", ".py", ".txt", ".yaml", ".yml"}
IGNORED_PARTS = {".git", ".test-state", ".runtime-state", "__pycache__", ".pytest_cache", "dist", "build"}
SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|password)\b\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
]
ABSOLUTE_PATH_PATTERNS = [
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"/(?:Users|home)/[^/\s]+/"),
]
PORTABILITY_MARKERS = ["deepseek-flash"]


def repository_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode == 0 and completed.stdout.strip():
        return [ROOT / line for line in completed.stdout.splitlines() if line.strip()]
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file() and not any(part in IGNORED_PARTS or part.startswith(".smoke-state") for part in path.parts)
    ]


def historical_texts() -> list[tuple[str, str, str]]:
    """Return text files from every reachable commit without reading reflog-only objects."""
    commits = subprocess.run(
        ["git", "-C", str(ROOT), "rev-list", "--all"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if commits.returncode != 0:
        return []
    results: list[tuple[str, str, str]] = []
    for commit in commits.stdout.splitlines():
        listed = subprocess.run(
            ["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", commit],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            check=False,
        )
        for relative in listed.stdout.splitlines():
            path = Path(relative)
            if path.suffix.lower() not in TEXT_SUFFIXES or relative == "scripts/release_audit.py":
                continue
            shown = subprocess.run(
                ["git", "-C", str(ROOT), "show", f"{commit}:{relative}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if shown.returncode != 0:
                continue
            try:
                text = shown.stdout.decode("utf-8")
            except UnicodeDecodeError:
                continue
            results.append((commit[:12], relative, text))
    return results


def main() -> int:
    errors: list[str] = []
    missing = sorted(path for path in REQUIRED if not (ROOT / path).is_file())
    if missing:
        errors.append("missing required files: " + ", ".join(missing))

    try:
        manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        if manifest.get("name") != "claude-review":
            errors.append("plugin name must be claude-review")
        version = str(manifest.get("version", ""))
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version):
            errors.append("manifest version must be release SemVer without local cache metadata")
    except Exception as error:
        errors.append(f"invalid plugin manifest: {error}")

    try:
        mcp = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        config = mcp["mcpServers"]["claude-review"]
        if config.get("command") not in {"python", "python3"}:
            errors.append("MCP command must be portable python/python3 lookup")
        if config.get("cwd") != ".":
            errors.append("MCP cwd must be relative to the plugin root")
        if config.get("args") != ["./scripts/server.py"]:
            errors.append("MCP server path must be plugin-root relative")
        if config.get("env"):
            errors.append("MCP env must not embed machine-specific configuration")
    except Exception as error:
        errors.append(f"invalid MCP manifest: {error}")

    for path in repository_files():
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(ROOT).as_posix()
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                errors.append(f"possible secret in {relative}: {pattern.pattern}")
        if relative not in {"scripts/release_audit.py"}:
            for pattern in ABSOLUTE_PATH_PATTERNS:
                if pattern.search(text):
                    errors.append(f"machine-specific absolute path in {relative}")

    for commit, relative, text in historical_texts():
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                errors.append(f"possible secret in reachable history {commit}:{relative}")
        for pattern in ABSOLUTE_PATH_PATTERNS:
            if pattern.search(text):
                errors.append(f"machine-specific path in reachable history {commit}:{relative}")
        for marker in PORTABILITY_MARKERS:
            if marker in text:
                errors.append(f"machine-specific model in reachable history {commit}:{relative}")

    if errors:
        print("Release audit failed:")
        for error in sorted(set(errors)):
            print(f"- {error}")
        return 1
    print("Release audit passed: portable manifests, required documentation, and tracked-text scans are clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
