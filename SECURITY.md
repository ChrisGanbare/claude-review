# Security policy

## Supported version

Security fixes are applied to the latest release on the default branch.

## Reporting a vulnerability

Do not include credentials, private source code, production logs, or personal data in a public issue. Use GitHub private vulnerability reporting when it is enabled for the repository. If that channel is unavailable, open a minimal issue requesting a private contact channel without disclosing the vulnerability.

## Security boundary

Claude Review is a local orchestration layer, not an operating-system sandbox. It constrains Claude Code to read-only tools and validates requested artifact paths, budgets, process lifetime, and retained logs. It does not isolate files from other software running as the same operating-system user, and it does not control the configured model provider's retention or billing policy.

The plugin must not be used to delegate security decisions, permission decisions, production actions, financial decisions, or secrets.
