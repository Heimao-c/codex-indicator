# Codex Indicator

Codex Indicator is a deliberately small system-tray monitor for multiple local Codex CLI sessions.

It shows only three pieces of information:

- current state (`working`, `needs attention`, `idle`, or `done`)
- project name
- Codex conversation title

It does not manage terminals, create worktrees, mirror full transcripts, or require tmux, Docker, Electron, or a cloud service.

## Platforms

- Ubuntu 22.04+: native Ayatana AppIndicator with compact counts in the top panel
- Windows: notification-area tray app
- macOS: menu-bar app

Download packages from [Releases](https://github.com/Heimao-c/codex-indicator/releases), or follow the complete installation, privacy, hook trust, and development documentation in the [Chinese README](README.md).

## Privacy

All processing is local. The app does not transmit source code, prompts, responses, API keys, or Codex credentials. Its own cache contains only session identity, state, working directory, timestamps, and optional process/terminal identifiers.

## Test

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## License

[MIT](LICENSE)
