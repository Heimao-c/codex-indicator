# CC Indicator

[English](README.en.md) · [中文](README.md)

A small system-tray monitor for seeing multiple Codex CLI and Claude Code CLI terminals at a glance.

![CC Indicator running](docs/cc-indicator-menu.png)

## What you see

- Session state: working, needs attention, or done
- A `[Codex]`/`[Claude]` tag next to the project name, plus a short conversation title
- Local terminals and, on Ubuntu, Codex and Claude terminals in existing SSH sessions
- Click a session to focus its terminal
- Rename or archive conversations, and open a new Codex or Claude terminal
- Colored status counts in the panel or system tray

It does not mirror full transcripts, create worktrees, require tmux/Docker/Electron, or use a cloud service. Data stays on the local machine.

## Platforms

- Ubuntu 22.04+: native top-panel AppIndicator, including SSH session discovery
- Windows: notification-area tray
- macOS: menu-bar app

## Install

Download a platform package from [Releases](https://github.com/Heimao-c/cc-indicator/releases).

For a user-level Ubuntu install from source:

```bash
git clone https://github.com/Heimao-c/cc-indicator.git
cd cc-indicator
sh scripts/install-linux.sh
```

After the first launch, install Codex + Claude hooks from the tray menu. The Codex hook is written to `~/.codex/hooks.json`; the Claude Code hook to `~/.claude/settings.json` (your existing settings are preserved, and Claude Code hot-reloads it). In each Codex CLI, open `/hooks` and trust the hook that points to this local application. Without trust, only partially discovered state is available.

## Claude Code support

- Claude Code terminal sessions appear merged with Codex in the same tray menu, using the exact same status semantics
- A permission prompt shows as "needs attention" (`◐`): it is sensed through the `Notification` hook event, which only reads and can never block or deny a request
- Claude Code has no management API, so rename applies locally (the indicator's own display only) and archive hides the conversation locally
- A tray toggle "Auto-approve all Claude operations" writes `permissions.defaultMode=bypassPermissions` to Claude Code's settings, locally and on every connected SSH host with Claude settings; every new request is auto-approved while your existing deny rules for dangerous commands still apply. Turning it off restores per-request prompts. Already-running Claude sessions keep their in-memory permission mode, so restart the session (or `/clear`) for the change to take effect
- Requires Claude Code 2.x; conversation titles come from the first user message in `~/.claude/projects/` transcripts

## Status model

| Status | Meaning |
| --- | --- |
| `● Working` | Processing a prompt, using a tool, compacting context, or running a subagent |
| `◐ Needs attention` | Waiting for permission or a `request_user_input` answer |
| `✓ Done` | The current turn finished; the terminal remains available |

Legacy `idle` cache entries are migrated to `done` automatically.

## Privacy and permissions

CC Indicator has no service of its own and does not upload source files, prompts, responses, API keys, or Codex credentials. Its cache contains only session IDs, state, directories, timestamps, process/terminal identifiers, and the minimum title/project metadata needed for the menu.

On Ubuntu, bulk approval reads the visible terminal screen only to verify a Codex approval pane and sends one confirmation key. Ordinary approvals are handled directly; destructive operations such as wiping disks or recursive system-directory deletion still require a separate confirmation. Screens are not saved.

SSH discovery uses existing local SSH processes and read-only probes; it does not store keys or passwords. Remote Claude sessions are found by their transcript directory and their status inferred from transcript freshness. If a newer remote Codex does not expose a rollout file, the app shows one placeholder per SSH TTY (status inferred from the remote app-server database); placeholder terminals can still be renamed or archived locally (display-only changes). Remote approval panes cannot be read from the local screen, so a remote session waiting for approval stays "working" rather than "needs attention".

## CLI and development

```bash
cc-indicator --install-hooks
cc-indicator --install-autostart
cc-indicator --dump-status
cc-indicator --doctor
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
