# Security policy

## Data boundary

CC Indicator is local-only. It does not provide a network listener and does not upload Codex data.

The hook receives Codex lifecycle JSON on standard input. Only the session ID, status, working directory, event name, timestamps, and optional local process/terminal identifiers are persisted. Full prompts, model replies, tool inputs, tool outputs, credentials, and source files are not persisted by this project.

## Hook safety

The installer merges its handlers into `~/.codex/hooks.json`, preserves unrelated handlers, and writes a timestamped backup before changing an existing file. Invalid JSON is never overwritten.

Review the installed command with `/hooks` in Codex before trusting it.

## Reporting

Please report security issues privately through GitHub's security advisory interface instead of opening a public issue.
