from __future__ import annotations

import json
import logging
import os
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


def active_ttys():
    source_ip = (os.environ.get("SSH_CONNECTION") or "").split()
    source_ip = source_ip[0] if source_ip else ""
    try:
        output = subprocess.run(["who"], capture_output=True, text=True, timeout=1).stdout
    except Exception:
        return set()
    result = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        origin = fields[-1].strip("()") if fields[-1].startswith("(") else ""
        if source_ip and origin and origin != source_ip:
            continue
        result.add(fields[1])
    return result


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
    current = Path(cwd).expanduser()
    project = current.name or str(current)
    candidate = current
    while True:
        if (candidate / ".git").exists():
            project = candidate.name or str(candidate)
            break
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return title or ("Session " + session_id[:8]), project, cwd


logged_ttys = active_ttys()
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
        if logged_ttys and tty not in logged_ttys:
            continue
        rollout = rollout_for(root)
        if not rollout:
            continue
        updated_at, rollout_path, session_id, _is_root = rollout
        cwd = os.readlink(root / "cwd")
    except OSError:
        continue
    title, project, resolved_cwd = metadata(home, session_id, cwd)
    item = {
        "session_id": session_id,
        "pid": int(name),
        "tty": tty,
        "cwd": resolved_cwd,
        "rollout_path": str(rollout_path),
        "status": infer_status(rollout_path),
        "updated_at": updated_at,
        "title": title,
        "project": project,
    }
    if tty not in by_tty or item["updated_at"] > by_tty[tty]["updated_at"]:
        by_tty[tty] = item
print(json.dumps(list(by_tty.values()), ensure_ascii=False))
'''


@dataclass(frozen=True)
class SshConnection:
    host: str
    local_tty: str


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


SSH_OPTIONS_WITH_VALUE = {
    "-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J", "-L",
    "-l", "-m", "-O", "-o", "-p", "-Q", "-R", "-S", "-W", "-w",
}


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
            connection = SshConnection(host=host, local_tty=tty)
            found[(host, tty)] = connection
        return list(found.values())

    @staticmethod
    def _probe(connection: SshConnection) -> list[RemoteSession]:
        remote_command = "python3 -c " + shlex.quote(REMOTE_PROBE)
        try:
            result = subprocess.run(
                [
                    "ssh", "-T", "-o", "ClearAllForwardings=yes", "-o", "BatchMode=yes",
                    "-o", "ConnectTimeout=3", "-o", "LogLevel=ERROR", connection.host,
                    remote_command,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            LOG.debug("Could not query remote Codex sessions on %s", connection.host, exc_info=True)
            return []
        if result.returncode != 0:
            LOG.debug("Remote Codex query failed on %s: %s", connection.host, result.stderr.strip())
            return []
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            LOG.debug("Remote Codex query returned invalid JSON from %s", connection.host)
            return []
        sessions: list[RemoteSession] = []
        for value in payload if isinstance(payload, list) else []:
            try:
                status = SessionStatus(str(value["status"]))
                remote_tty = str(value["tty"])
                sessions.append(
                    RemoteSession(
                        session_id=str(value["session_id"]),
                        pid=int(value["pid"]),
                        cwd=str(value.get("cwd") or ""),
                        terminal_id=f"SSH:{connection.local_tty}:{connection.host}:{remote_tty}",
                        status=status,
                        updated_at=float(value.get("updated_at") or time.time()),
                        title=str(value.get("title") or ""),
                        project=str(value.get("project") or "—"),
                        host=connection.host,
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
        probed_hosts: set[str] = set()
        for connection in self.connections():
            if connection.host in probed_hosts:
                continue
            probed_hosts.add(connection.host)
            for session in self._probe(connection):
                key = (session.host, session.session_id)
                previous = sessions.get(key)
                if not previous or session.updated_at > previous.updated_at:
                    sessions[key] = session
        self._cached = list(sessions.values())
        self._cached_at = now
        return list(self._cached)
