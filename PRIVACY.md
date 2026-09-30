# Privacy and data handling

Claude Review runs locally and does not operate its own remote service. When a review starts, Claude Code may transmit prompts and inspected file content to the provider configured in the user's Claude Code installation.

The plugin stores bounded job metadata, final results, and bounded raw event logs in `CLAUDE_REVIEW_STATE_DIR` or the operating system's temporary directory. Default retention is seven days, subject to a total log-size cap. Users may select a dedicated state directory and remove it according to their own retention policy.

Do not delegate credentials, payment information, production secrets, regulated data, or personal data unless the user has explicitly approved the exact data and destination and the configured provider terms are acceptable.

The repository contains no API keys or provider credentials. Authentication remains owned by the user's Claude Code installation.
