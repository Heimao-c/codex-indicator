# Codex Indicator

[English](README.en.md) · [中文](README.md)

A small system-tray monitor for seeing multiple Codex CLI terminals at a glance.

![Codex Indicator running](docs/codex-indicator-menu.png)

## What you see

- Session state: working, needs attention, or done
- Project name and a short Codex conversation title
- Local terminals and, on Ubuntu, Codex terminals in existing SSH sessions
- Click a session to focus its terminal
- Rename or archive conversations, and open a new Codex terminal
- Colored status counts in the panel or system tray

It does not mirror full transcripts, create worktrees, require tmux/Docker/Electron, or use a cloud service. Data stays on the local machine.

## Platforms

- Ubuntu 22.04+: native top-panel AppIndicator, including SSH session discovery
- Windows: notification-area tray
- macOS: menu-bar app

## Install

Download a platform package from [Releases](https://github.com/Heimao-c/codex-indicator/releases).

For a user-level Ubuntu install from source:

```bash
git clone https://github.com/Heimao-c/codex-indicator.git
cd codex-indicator
sh scripts/install-linux.sh
```

After the first launch, install Codex Hooks from the tray menu. In each Codex CLI, open `/hooks` and trust the hook that points to this local application. Without trust, only partially discovered state is available.

## Status model

| Status | Meaning |
| --- | --- |
| `● Working` | Processing a prompt, using a tool, compacting context, or running a subagent |
| `◐ Needs attention` | Waiting for permission or a `request_user_input` answer |
| `✓ Done` | The current turn finished; the terminal remains available |

Legacy `idle` cache entries are migrated to `done` automatically.

## Privacy and permissions

Codex Indicator has no service of its own and does not upload source files, prompts, responses, API keys, or Codex credentials. Its cache contains only session IDs, state, directories, timestamps, process/terminal identifiers, and the minimum title/project metadata needed for the menu.

On Ubuntu, bulk approval reads the visible terminal screen only to verify a Codex approval pane and sends one confirmation key. Ordinary approvals are handled directly; destructive operations such as wiping disks or recursive system-directory deletion still require a separate confirmation. Screens are not saved.

SSH discovery uses existing local SSH processes and read-only probes; it does not store keys or passwords. If a newer remote Codex does not expose a rollout file, the app shows one placeholder per SSH TTY and disables unsupported rename/archive actions.

## CLI and development

```bash
codex-indicator --install-hooks
codex-indicator --install-autostart
codex-indicator --dump-status
codex-indicator --doctor
```

Run tests with:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

GitHub Actions tests Ubuntu, Windows, and macOS; pushing a `v*` tag builds packages for all three platforms. Packages are currently unsigned, so Windows SmartScreen and macOS Gatekeeper may require a first-run confirmation.

## Limitations

- Precise live state depends on trusted Codex Hooks; running Codex processes may need `/hooks` reopened.
- Terminal focusing currently targets Ubuntu GNOME Terminal, Windows Terminal, and macOS Terminal.
- Automatic SSH discovery and bulk approval are Ubuntu-only.

## License

[MIT](LICENSE)
