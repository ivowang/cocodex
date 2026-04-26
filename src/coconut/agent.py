from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from .config import CoconutConfig
from .protocol import ProtocolError, decode_message
from .state import SessionRecord
from .transport import send_message, serve_forever


def control_socket_path(repo: Path, config: CoconutConfig, session: str) -> Path:
    return repo / ".coconut" / "sessions" / f"{session}.sock"


class SessionAgent:
    def __init__(
        self,
        *,
        repo: Path,
        config: CoconutConfig,
        record: SessionRecord,
        command: list[str],
        stop_event: threading.Event | None = None,
        heartbeat_interval: float = 2.0,
    ) -> None:
        self.repo = repo
        self.config = config
        self.record = record
        self.command = command
        self.stop_event = stop_event or threading.Event()
        self.heartbeat_interval = heartbeat_interval
        self.control_socket = control_socket_path(repo, config, record.name)

    def start_control_server(self, *, wait: bool = False, timeout: float = 2.0) -> threading.Thread:
        thread = serve_forever(self.control_socket, self.handle_command, stop_event=self.stop_event)
        thread.start()
        try:
            if wait:
                wait_for_control_socket(self.control_socket, self.record.name, timeout=timeout)
        except Exception:
            self.stop_event.set()
            thread.join(timeout=2)
            raise
        return thread

    def start_heartbeat(self) -> threading.Thread:
        thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        thread.start()
        return thread

    def run(self, *, control_thread: threading.Thread | None = None) -> int:
        control_thread = control_thread or self.start_control_server()
        heartbeat_thread = self.start_heartbeat()
        try:
            if not self.command:
                print(str(Path(self.record.worktree)))
                return 0
            return subprocess.call(self.command, cwd=self.record.worktree)
        finally:
            self.stop_event.set()
            self._send_daemon({"type": "shutdown", "session": self.record.name})
            control_thread.join(timeout=2)
            heartbeat_thread.join(timeout=2)

    def handle_command(self, message: dict) -> dict:
        task_id = message.get("task_id")
        message_type = message.get("type")
        if message_type == "freeze":
            if self.stop_event.is_set():
                return {
                    "type": "freeze_busy",
                    "session": self.record.name,
                    "task_id": task_id,
                    "reason": "agent stopping",
                }
            return {"type": "freeze_ack", "session": self.record.name, "task_id": task_id}
        if message_type == "start_fusion":
            task_file = message["task_file"]
            print(f"Coconut task for {self.record.name}: {task_file}", flush=True)
            return {"type": "ack", "session": self.record.name, "task_id": task_id}
        if message_type == "main_updated":
            return {
                "type": "ack",
                "session": self.record.name,
                "main_commit": message.get("main_commit"),
            }
        if message_type == "shutdown":
            self.stop_event.set()
            return {"type": "ack", "session": self.record.name}
        return {"type": "ack", "session": self.record.name}

    def _heartbeat_loop(self) -> None:
        while not self.stop_event.wait(self.heartbeat_interval):
            self._send_daemon({"type": "heartbeat", "session": self.record.name})

    def _send_daemon(self, message: dict) -> dict | None:
        socket_path = self.repo / self.config.socket_path
        if not socket_path.exists():
            return None
        try:
            raw = send_message(socket_path, message, timeout=2)
            return decode_message(raw)
        except (OSError, TimeoutError, ProtocolError):
            return None


def wait_for_control_socket(socket_path: Path, session: str, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    message = {"type": "freeze", "session": session, "task_id": "control-ready"}
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            decode_message(send_message(socket_path, message, timeout=0.05))
            return
        except (OSError, TimeoutError, ProtocolError) as exc:
            last_error = exc
            time.sleep(0.01)
    raise TimeoutError(f"control socket did not become ready: {socket_path}") from last_error


def run_agent(
    repo: Path,
    config: CoconutConfig,
    record: SessionRecord,
    command: list[str],
    *,
    agent: SessionAgent | None = None,
    control_thread: threading.Thread | None = None,
) -> int:
    agent = agent or SessionAgent(repo=repo, config=config, record=record, command=command)
    return agent.run(control_thread=control_thread)
