from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path

from .config import CoconutConfig
from .git import GitError, create_worktree, current_head, run_git
from .protocol import ProtocolError, decode_message
from .state import SessionRecord, get_session, register_session
from .transport import send_message


SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def ensure_session_worktree(
    repo: Path,
    config: CoconutConfig,
    db: sqlite3.Connection,
    session: str,
) -> SessionRecord:
    validate_session_name(session)
    branch = f"coconut/{session}"
    worktree = repo / config.worktree_root / session
    create_worktree(repo, branch=branch, worktree=worktree, start_point=config.main_branch)
    _validate_worktree(worktree, branch)

    existing = get_session(db, session)
    if existing is not None:
        if existing.branch != branch:
            raise ValueError(
                f"Existing session {session!r} uses branch {existing.branch!r}, expected {branch!r}"
            )
        if existing.worktree != str(worktree):
            raise ValueError(
                f"Existing session {session!r} uses worktree {existing.worktree!r}, "
                f"expected {str(worktree)!r}"
            )
        return existing

    record = SessionRecord(
        name=session,
        branch=branch,
        worktree=str(worktree),
        state="clean",
        last_seen_main=current_head(repo, config.main_branch),
        active_task=None,
        blocked_reason=None,
    )
    register_session(db, record)
    return record


def validate_session_name(session: str) -> None:
    if not SESSION_NAME_RE.fullmatch(session):
        raise ValueError(
            "Invalid session name: use letters, digits, underscores, or hyphens, "
            "and start with a letter or digit"
        )


def _validate_worktree(worktree: Path, branch: str) -> None:
    try:
        top_level = Path(run_git(worktree, ["rev-parse", "--show-toplevel"])).resolve()
        actual_branch = run_git(worktree, ["rev-parse", "--abbrev-ref", "HEAD"])
    except GitError as exc:
        raise RuntimeError(f"{worktree} is not a Git worktree") from exc
    if top_level != worktree.resolve():
        raise RuntimeError(f"{worktree} is not a Git worktree")
    if actual_branch != branch:
        raise RuntimeError(
            f"{worktree} is on branch {actual_branch!r}, expected {branch!r}"
        )


def register_with_daemon(
    socket_path: Path,
    record: SessionRecord,
    pid: int,
    control_socket: str | None = None,
    *,
    timeout: float | None = 0.5,
) -> dict | None:
    if not socket_path.exists():
        return None
    message = {
        "type": "register",
        "session": record.name,
        "pid": pid,
        "branch": record.branch,
        "worktree": record.worktree,
    }
    if control_socket is not None:
        message["control_socket"] = control_socket
    try:
        raw = send_message(
            socket_path,
            message,
            timeout=timeout,
        )
        return decode_message(raw)
    except (OSError, TimeoutError, ProtocolError):
        return None


def send_completion(
    socket_path: Path,
    session: SessionRecord,
    *,
    blocked_reason: str | None = None,
) -> dict:
    if session.active_task is None:
        raise RuntimeError(f"Session {session.name} has no active task")
    message = {
        "type": "fusion_blocked" if blocked_reason else "fusion_done",
        "session": session.name,
        "task_id": session.active_task,
    }
    if blocked_reason:
        message["reason"] = blocked_reason
    raw = send_message(socket_path, message, timeout=5)
    return decode_message(raw)


def run_session_command(worktree: Path, command: list[str]) -> int:
    if not command:
        raise ValueError("join requires a command after --")
    return subprocess.call(command, cwd=worktree)
