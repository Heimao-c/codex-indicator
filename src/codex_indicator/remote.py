from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from codex_indicator.models import SessionStatus


LOG = logging.getLogger(__name__)


# This probe is sent over an already-authenticated SSH host entry. It is read-only:
# it inspects live Codex/Claude TTYs, their open rollout/transcript files, and the
# public thread metadata.
REMOTE_PROBE = r'''
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


def tail_lines(path, maximum_bytes=524288):
    try:
        with open(path, "rb") as source:
            source.seek(0, os.SEEK_END)
            size = source.tell()
            source.seek(max(0, size - maximum_bytes))
            data = source.read()
    except OSError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[1:] if size > maximum_bytes and lines else lines


def infer_status(path):
    meaningful = "working"
    pending = None
    completed = set()
    for line in tail_lines(path):
        try:
            record = json.loads(line)
        except Exception:
            continue
        payload = record.get("payload") if isinstance(record, dict) else None
        if not isinstance(payload, dict):
            continue
        payload_type = str(payload.get("type") or "")
        if record.get("type") == "event_msg":
            if payload_type in {"task_complete", "turn_complete", "turn_completed", "turn_aborted", "task_cancelled"}:
                meaningful = "done"
                pending = None
            elif payload_type in {"task_started", "turn_started", "user_message"}:
                meaningful = "working"
                pending = None
        elif record.get("type") == "response_item":
            if payload_type in {"custom_tool_call", "function_call"}:
                meaningful = "working"
                if str(payload.get("name") or "") == "request_user_input":
                    pending = str(payload.get("call_id") or payload.get("id") or "pending")
            elif payload_type in {"custom_tool_call_output", "function_call_output"}:
                call_id = str(payload.get("call_id") or "")
                if call_id:
                    completed.add(call_id)
                if pending and (pending == "pending" or pending in completed):
                    pending = None
                    meaningful = "working"
    return "attention" if pending else meaningful


def login_ttys():
    source_ip = (os.environ.get("SSH_CONNECTION") or "").split()
    source_ip = source_ip[0] if source_ip else ""
    try:
        output = subprocess.run(["who", "-u"], capture_output=True, text=True, timeout=1).stdout
    except Exception:
        return {}, set()
    by_source_port = {}
    visible = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        origin = fields[-1].strip("()") if fields[-1].startswith("(") else ""
        if source_ip and origin and origin != source_ip:
            continue
        tty = fields[1]
        visible.add(tty)
        login_pid = next((int(value) for value in reversed(fields[2:]) if value.isdigit()), 0)
        if not login_pid:
            continue
        try:
            environment = (Path("/proc") / str(login_pid) / "environ").read_bytes().split(b"\0")
            values = {}
            for item in environment:
                key, separator, value = item.partition(b"=")
                if separator:
                    values[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
            connection = values.get("SSH_CONNECTION", "").split()
            login_tty = values.get("SSH_TTY", "").removeprefix("/dev/") or tty
            if len(connection) >= 2:
                by_source_port[int(connection[1])] = login_tty
        except Exception:
            continue
    return by_source_port, visible


def process_started(pid_root):
    try:
        fields = (pid_root / "stat").read_text().split()
        started_ticks = int(fields[21])
        uptime = float(Path("/proc/uptime").read_text().split()[0])
        return time.time() - uptime + started_ticks / os.sysconf("SC_CLK_TCK")
    except Exception:
        return time.time()


def rollout_for(pid_root):
    candidates = []
    try:
        names = os.listdir(pid_root / "fd")
    except OSError:
        return None
    for name in names:
        try:
            target = Path(os.readlink(pid_root / "fd" / name))
            stat = target.stat()
        except OSError:
            continue
        if target.suffix != ".jsonl" or "sessions" not in target.parts:
            continue
        stem = target.stem
        session_id = stem[-36:]
        if len(session_id) == 36 and session_id.count("-") == 4:
            is_root = True
            try:
                with open(target, "r", encoding="utf-8", errors="replace") as source:
                    record = json.loads(source.readline())
                payload = record.get("payload") if isinstance(record, dict) else None
                if isinstance(payload, dict):
                    origin = payload.get("source")
                    is_root = not payload.get("parent_thread_id") and not (
                        isinstance(origin, dict) and origin.get("subagent")
                    )
            except Exception:
                pass
            candidates.append((stat.st_mtime, target, session_id, is_root))
    roots = [item for item in candidates if item[3]]
    return max(roots or candidates, default=None, key=lambda item: item[0])


def clean(value):
    return " ".join(str(value or "").split())


def project_name(cwd):
    current = Path(cwd).expanduser()
    project = current.name or str(current)
    candidate = current
    while True:
        if (candidate / ".git").exists():
            return candidate.name or str(candidate)
        if candidate.parent == candidate:
            return project
        candidate = candidate.parent


def infer_claude_status(path, now):
    try:
        modified = os.stat(path).st_mtime
    except OSError:
        return "done"
    if now - modified <= 20.0:
        return "working"
    meaningful = "done"
    for line in tail_lines(path):
        try:
            record = json.loads(line)
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        record_type = str(record.get("type") or "")
        if record_type == "user" and not record.get("isMeta"):
            meaningful = "working"
        elif record_type == "assistant":
            message = record.get("message")
            stop_reason = message.get("stop_reason") if isinstance(message, dict) else None
            meaningful = "working" if stop_reason == "tool_use" else "done"
    return meaningful


def claude_title(path):
    for line in tail_lines(path):
        try:
            record = json.loads(line)
        except Exception:
            continue
        if not isinstance(record, dict) or record.get("type") != "user" or record.get("isMeta"):
            continue
        content = record.get("message", {}).get("content")
        if isinstance(content, str):
            return clean(strip_ui_markup(content))
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            if parts:
                return clean(strip_ui_markup(" ".join(parts)))
    return ""


def strip_ui_markup(value):
    return re.sub(
        r"<(command-name|command-message|command-args|local-command-stdout)[^>]*>.*?</\1>",
        " ",
        value,
        flags=re.DOTALL,
    )


def claude_config_dir(pid_root=None):
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if pid_root is not None:
        try:
            environ = (pid_root / "environ").read_bytes().split(b"\0")
            for item in environ:
                key, separator, value = item.partition(b"=")
                if separator and key.decode("utf-8", "replace") == "CLAUDE_CONFIG_DIR":
                    return Path(value.decode("utf-8", "replace"))
        except Exception:
            pass
    return Path(override) if override else Path.home() / ".claude"


def claude_transcript_for(cwd, started, now, config_dir):
    """Newest transcript of one live Claude terminal (created after start)."""
    encoded = cwd.replace("/", "-")
    candidates = []
    try:
        paths = (config_dir / "projects" / encoded).glob("*.jsonl")
    except OSError:
        return None
    for path in paths:
        stem = path.stem
        if len(stem) != 36 or stem.count("-") != 4:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_ctime + 60.0 < started:
            continue
        candidates.append((path, stat))
    if not candidates:
        return None
    best = max(
        candidates,
        key=lambda item: (now - item[1].st_mtime <= 20.0, item[1].st_mtime, item[1].st_ctime),
    )
    return best[0]


def codex_activity(cwd):
    """(updated_at_ms, first_user_message) of the most recently touched thread."""
    databases = sorted(Path.home().glob(".codex/state_*.sqlite"), reverse=True)
    for database in databases:
        try:
            connection = sqlite3.connect("file:%s?mode=ro" % database, uri=True, timeout=0.2)
            row = connection.execute(
                "SELECT updated_at_ms, first_user_message FROM threads"
                " WHERE cwd = ? ORDER BY updated_at_ms DESC LIMIT 1",
                (cwd,),
            ).fetchone()
            connection.close()
        except Exception:
            continue
        if row and row[0]:
            return int(row[0]), clean(row[1])
    return 0, ""


def metadata(home, session_id, fallback_cwd):
    title = ""
    stored_cwd = ""
    databases = sorted(home.glob("state_*.sqlite"), reverse=True)
    for database in databases:
        try:
            connection = sqlite3.connect("file:%s?mode=ro" % database, uri=True, timeout=0.2)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
            wanted = [name for name in ("name", "title", "first_user_message", "cwd") if name in columns]
            row = connection.execute(
                "SELECT %s FROM threads WHERE id = ? LIMIT 1" % ", ".join(wanted),
                (session_id,),
            ).fetchone() if wanted else None
            connection.close()
        except Exception:
            continue
        if row:
            values = dict(zip(wanted, row))
            title = next((clean(values.get(key)) for key in ("name", "title", "first_user_message") if values.get(key)), "")
            stored_cwd = str(values.get("cwd") or "")
            break
    if not title:
        try:
            for line in (home / "session_index.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
                value = json.loads(line)
                if str(value.get("id")) == session_id:
                    title = clean(value.get("thread_name"))
        except Exception:
            pass
    cwd = stored_cwd or fallback_cwd
    return title or ("Session " + session_id[:8]), project_name(cwd), cwd


try:
    requested_ports = {int(key): value for key, value in json.loads(sys.argv[1]).items()}
except Exception:
    requested_ports = {}
port_ttys, logged_ttys = login_ttys()
remote_to_local = {
    remote_tty: requested_ports[source_port]
    for source_port, remote_tty in port_ttys.items()
    if source_port in requested_ports
}
home = Path.home() / ".codex"
now = time.time()
agent_comms = {"codex", "codex.exe", "codex-cli", "codex-cli.exe", "claude", "claude.exe"}
by_tty = {}
claude_config_dirs = set()
for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    root = Path("/proc") / name
    try:
        comm = (root / "comm").read_text(errors="replace").strip().lower()
        if comm not in agent_comms:
            continue
        tty_path = os.readlink(root / "fd" / "0")
        if not tty_path.startswith("/dev/pts/"):
            continue
        tty = tty_path.removeprefix("/dev/")
        if remote_to_local and tty not in remote_to_local:
            continue
        if not remote_to_local and logged_ttys and tty not in logged_ttys:
            continue
        cwd = os.readlink(root / "cwd")
        started = process_started(root)
    except OSError:
        continue
    if comm in {"claude", "claude.exe"}:
        config_dir = claude_config_dir(root)
        claude_config_dirs.add(config_dir)
        transcript = claude_transcript_for(cwd, started, now, config_dir)
        if transcript:
            stat = transcript.stat()
            session_id = transcript.stem
            title = claude_title(transcript) or ("Session " + session_id[:8])
            item = {
                "session_id": session_id,
                "pid": int(name),
                "tty": tty,
                "cwd": cwd,
                "rollout_path": str(transcript),
                "status": infer_claude_status(transcript, now),
                "updated_at": stat.st_mtime,
                "title": title,
                "project": project_name(cwd),
                "local_tty": remote_to_local.get(tty, ""),
                "manageable": True,
                "tool": "claude",
            }
        else:
            item = {
                "session_id": "process-%s-%s" % (name, tty.replace("/", "-")),
                "pid": int(name),
                "tty": tty,
                "cwd": cwd,
                "rollout_path": "",
                "status": "done",
                "updated_at": started,
                "title": "Claude terminal " + tty,
                "project": project_name(cwd),
                "local_tty": remote_to_local.get(tty, ""),
                "manageable": False,
                "tool": "claude",
            }
    else:
        rollout = rollout_for(root)
        placeholder = rollout is None
        if rollout:
            updated_at, rollout_path, session_id, _is_root = rollout
            title, project, resolved_cwd = metadata(home, session_id, cwd)
            status = infer_status(rollout_path)
        else:
            updated_at = started
            rollout_path = ""
            session_id = "process-%s-%s" % (name, tty.replace("/", "-"))
            # Newer Codex versions keep their conversation state in the
            # app-server database; a recently touched thread means the TUI is
            # busy even without an exposed rollout file.
            activity_ms, activity_title = codex_activity(cwd)
            if now * 1000.0 - activity_ms <= 120000.0:
                status = "working"
            else:
                status = "done"
            title = activity_title or ("Codex terminal " + tty)
            project = project_name(cwd)
            resolved_cwd = cwd
        item = {
            "session_id": session_id,
            "pid": int(name),
            "tty": tty,
            "cwd": resolved_cwd,
            "rollout_path": str(rollout_path),
            "status": status,
            "updated_at": updated_at,
            "title": title,
            "project": project,
            "local_tty": remote_to_local.get(tty, ""),
            "manageable": not placeholder,
            "tool": "codex",
        }
    if tty not in by_tty or item["updated_at"] > by_tty[tty]["updated_at"]:
        by_tty[tty] = item
claude_settings = str((next(iter(claude_config_dirs), None) or claude_config_dir()) / "settings.json")
claude_info = {"settings_path": claude_settings, "exists": False, "bypass": False}
try:
    document = json.load(open(claude_settings, encoding="utf-8"))
    claude_info["exists"] = True
    claude_info["bypass"] = bool((document.get("permissions") or {}).get("defaultMode") == "bypassPermissions")
except Exception:
    pass
print(json.dumps({"sessions": list(by_tty.values()), "claude": claude_info}, ensure_ascii=False))
'''


# Companion write script for the Claude auto-approve toggle: sets or removes
# permissions.defaultMode=bypassPermissions in the remote Claude Code settings,
# preserving everything else. It only ever touches that one key.
REMOTE_CLAUDE_TOGGLE = r'''
import json
import os
import sys
import tempfile
from pathlib import Path


def settings_path():
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override) / "settings.json"
    default = Path.home() / ".claude" / "settings.json"
    try:
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            root = Path("/proc") / name
            try:
                if (root / "comm").read_text(errors="replace").strip().lower() != "claude":
                    continue
                environ = (root / "environ").read_bytes().split(b"\0")
            except OSError:
                continue
            for item in environ:
                key, separator, value = item.partition(b"=")
                if separator and key.decode("utf-8", "replace") == "CLAUDE_CONFIG_DIR":
                    return Path(value.decode("utf-8", "replace")) / "settings.json"
    except Exception:
        pass
    return default


def main():
    enabled = sys.argv[1] == "on"
    path = settings_path()
    document = {}
    if path.exists():
        document = json.loads(path.read_text(encoding="utf-8"))
    permissions = document.setdefault("permissions", {})
    if enabled:
        permissions["defaultMode"] = "bypassPermissions"
    else:
        permissions.pop("defaultMode", None)
        if not permissions:
            document.pop("permissions", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(document, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    print("ok")


main()
'''


@dataclass(frozen=True)
class SshConnection:
    host: str
    local_tty: str
    source_port: int | None = None


@dataclass(frozen=True)
class RemoteSession:
    session_id: str
    pid: int
    cwd: str
    terminal_id: str
    status: SessionStatus
    updated_at: float
    title: str
    project: str
    host: str
    manageable: bool = True
    tool: str = "codex"


SSH_OPTIONS_WITH_VALUE = {
    "-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J", "-L",
    "-l", "-m", "-O", "-o", "-p", "-Q", "-R", "-S", "-W", "-w",
}


def _socket_source_port(process_root: Path) -> int | None:
    inodes: set[str] = set()
    try:
        descriptors = list((process_root / "fd").iterdir())
    except OSError:
        return None
    for descriptor in descriptors:
        try:
            target = os.readlink(descriptor)
        except OSError:
            continue
        match = re.fullmatch(r"socket:\[(\d+)\]", target)
        if match:
            inodes.add(match.group(1))
    for table in (process_root / "net" / "tcp", process_root / "net" / "tcp6"):
        try:
            lines = table.read_text(encoding="utf-8", errors="replace").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "01" or fields[9] not in inodes:
                continue
            try:
                return int(fields[1].rsplit(":", 1)[1], 16)
            except (IndexError, ValueError):
                continue
    return None


def ssh_target(argv: list[str]) -> str | None:
    index = 1
    while index < len(argv):
        value = argv[index]
        if value == "--":
            return argv[index + 1] if index + 1 < len(argv) else None
        if value in SSH_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if value.startswith("-"):
            index += 1
            continue
        return value
    return None


def set_claude_bypass(host: str, enabled: bool) -> bool:
    """Set/remove permissions.defaultMode on a connected remote host."""
    command = " ".join(
        (
            "python3 -c",
            shlex.quote(REMOTE_CLAUDE_TOGGLE),
            "on" if enabled else "off",
        )
    )
    try:
        result = subprocess.run(
            [
                "ssh", "-T", "-o", "ClearAllForwardings=yes", "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=3", "-o", "LogLevel=ERROR", host,
                command,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        LOG.debug("Could not toggle Claude auto-approve on %s", host, exc_info=True)
        return False
    if result.returncode != 0:
        LOG.debug("Claude auto-approve toggle failed on %s: %s", host, result.stderr.strip())
        return False
    return True


class LinuxRemoteScanner:
    def __init__(self, proc_root: Path = Path("/proc"), cache_seconds: float = 5.0) -> None:
        self.proc_root = proc_root
        self.cache_seconds = cache_seconds
        self._cached_at = 0.0
        self._cached: list[RemoteSession] = []
        self._claude_info: dict[str, dict] = {}

    def claude_bypass_state(self) -> dict[str, bool]:
        """Host -> whether its Claude settings already have auto-approve on."""
        return {
            host: bool(info.get("bypass"))
            for host, info in self._claude_info.items()
            if info.get("exists")
        }

    def claude_hosts(self) -> list[str]:
        return sorted(self._claude_info)

    def set_claude_allow_all(self, enabled: bool) -> int:
        """Apply the Claude auto-approve toggle to every connected remote host."""
        updated = 0
        for host in self.claude_hosts():
            if set_claude_bypass(host, enabled):
                updated += 1
                info = self._claude_info.get(host)
                if info:
                    info["bypass"] = bool(enabled)
        return updated

    def connections(self) -> list[SshConnection]:
        found: dict[tuple[str, str], SshConnection] = {}
        try:
            roots = list(self.proc_root.iterdir())
        except OSError:
            return []
        for root in roots:
            if not root.name.isdigit():
                continue
            try:
                comm = (root / "comm").read_text(encoding="utf-8", errors="replace").strip()
                if comm not in {"ssh", "ssh.exe"}:
                    continue
                tty = os.readlink(root / "fd" / "0")
                if not tty.startswith("/dev/pts/"):
                    continue
                argv = [part.decode("utf-8", "replace") for part in (root / "cmdline").read_bytes().split(b"\0") if part]
            except OSError:
                continue
            host = ssh_target(argv)
            if not host:
                continue
            connection = SshConnection(
                host=host,
                local_tty=tty,
                source_port=_socket_source_port(root),
            )
            found[(host, tty)] = connection
        return list(found.values())

    def _probe(self, host: str, connections: list[SshConnection]) -> list[RemoteSession]:
        port_ttys = {
            str(connection.source_port): connection.local_tty
            for connection in connections
            if connection.source_port is not None
        }
        remote_command = " ".join(
            (
                "python3 -c",
                shlex.quote(REMOTE_PROBE),
                shlex.quote(json.dumps(port_ttys, ensure_ascii=True)),
            )
        )
        try:
            result = subprocess.run(
                [
                    "ssh", "-T", "-o", "ClearAllForwardings=yes", "-o", "BatchMode=yes",
                    "-o", "ConnectTimeout=3", "-o", "LogLevel=ERROR", host,
                    remote_command,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            LOG.debug("Could not query remote Codex sessions on %s", host, exc_info=True)
            return []
        if result.returncode != 0:
            LOG.debug("Remote Codex query failed on %s: %s", host, result.stderr.strip())
            return []
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            LOG.debug("Remote agent query returned invalid JSON from %s", host)
            return []
        sessions: list[RemoteSession] = []
        fallback_local_tty = connections[0].local_tty if len(connections) == 1 else "unknown"
        claude_info = payload.get("claude") if isinstance(payload, dict) else None
        if isinstance(claude_info, dict):
            self._claude_info[host] = claude_info
        elif host in self._claude_info:
            del self._claude_info[host]
        values = payload.get("sessions") if isinstance(payload, dict) else payload
        for value in values if isinstance(values, list) else []:
            try:
                status = SessionStatus(str(value["status"]))
                remote_tty = str(value["tty"])
                local_tty = str(value.get("local_tty") or fallback_local_tty)
                sessions.append(
                    RemoteSession(
                        session_id=str(value["session_id"]),
                        pid=int(value["pid"]),
                        cwd=str(value.get("cwd") or ""),
                        terminal_id=f"SSH:{local_tty}:{host}:{remote_tty}",
                        status=status,
                        updated_at=float(value.get("updated_at") or time.time()),
                        title=str(value.get("title") or ""),
                        project=str(value.get("project") or "—"),
                        host=host,
                        manageable=bool(value.get("manageable", True)),
                        tool=str(value.get("tool") or "codex"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return sessions

    def discover(self, force: bool = False) -> list[RemoteSession]:
        now = time.monotonic()
        if not force and now - self._cached_at < self.cache_seconds:
            return list(self._cached)
        sessions: dict[tuple[str, str], RemoteSession] = {}
        by_host: dict[str, list[SshConnection]] = {}
        for connection in self.connections():
            by_host.setdefault(connection.host, []).append(connection)
        for host, connections in by_host.items():
            for session in self._probe(host, connections):
                key = (session.host, session.session_id)
                previous = sessions.get(key)
                if not previous or session.updated_at > previous.updated_at:
                    sessions[key] = session
        self._cached = list(sessions.values())
        self._cached_at = now
        return list(self._cached)
