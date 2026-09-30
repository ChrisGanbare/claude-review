# Contributing

Contributions should preserve the plugin's narrow scope: bounded, read-only evidence collection for Codex.

Before opening a pull request:

```bash
python scripts/release_audit.py
python -m py_compile scripts/server.py scripts/benchmark.py tests/test_server.py
python -m unittest discover -s tests -p "test_*.py" -v
```

Tests and CI must not invoke a real model or require provider credentials. Real benchmarks are maintainer-only release evidence and must always set an explicit total cost ceiling.

Do not weaken tool restrictions, permission handling, budget limits, process-tree cleanup, persistence, or privacy guidance merely to make a test pass.
