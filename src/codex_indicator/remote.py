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
# it inspects live Codex TTYs, their open rollout file, and the public thread metadata.
REMOTE_PROBE = r'''
import json
import os
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
by_tty = {}
for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    root = Path("/proc") / name
    try:
        if (root / "comm").read_text(errors="replace").strip().lower() not in {"codex", "codex.exe", "codex-cli", "codex-cli.exe"}:
            continue
        tty_path = os.readlink(root / "fd" / "0")
        if not tty_path.startswith("/dev/pts/"):
            continue
        tty = tty_path.removeprefix("/dev/")
        if remote_to_local and tty not in remote_to_local:
            continue
        if not remote_to_local and logged_ttys and tty not in logged_ttys:
            continue
        rollout = rollout_for(root)
        cwd = os.readlink(root / "cwd")
    except OSError:
        continue
    placeholder = rollout is None
    if rollout:
        updated_at, rollout_path, session_id, _is_root = rollout
        title, project, resolved_cwd = metadata(home, session_id, cwd)
        status = infer_status(rollout_path)
    else:
        updated_at = process_started(root)
        rollout_path = ""
        session_id = "process-%s-%s" % (name, tty.replace("/", "-"))
        title = "Codex terminal " + tty
        project = project_name(cwd)
        resolved_cwd = cwd
        status = "done"
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
    }
    if tty not in by_tty or item["updated_at"] > by_tty[tty]["updated_at"]:
        by_tty[tty] = item
print(json.dumps(list(by_tty.values()), ensure_ascii=False))
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


class LinuxRemoteScanner:
    def __init__(self, proc_root: Path = Path("/proc"), cache_seconds: float = 5.0) -> None:
        self.proc_root = proc_root
        self.cache_seconds = cache_seconds
        self._cached_at = 0.0
        self._cached: list[RemoteSession] = []

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

    @staticmethod
    def _probe(host: str, connections: list[SshConnection]) -> list[RemoteSession]:
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
            LOG.debug("Remote Codex query returned invalid JSON from %s", host)
            return []
        sessions: list[RemoteSession] = []
        fallback_local_tty = connections[0].local_tty if len(connections) == 1 else "unknown"
        for value in payload if isinstance(payload, list) else []:
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
