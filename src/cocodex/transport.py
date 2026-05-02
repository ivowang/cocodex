from __future__ import annotations

import errno
import hashlib
import os
import socket
import stat
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .protocol import decode_message, encode_message


Handler = Callable[[dict], dict]
ACCEPTED_CONNECTION_TIMEOUT = 0.1
MAX_UNIX_SOCKET_PATH = 100
SOCKET_POINTER_HEADER = "cocodex-socket-v1"


@dataclass(frozen=True)
class SocketBinding:
    logical_path: Path
    bind_path: Path
    uses_pointer: bool


def _read_line(conn: socket.socket) -> bytes:
    chunks = bytearray()
    while True:
        try:
            chunk = conn.recv(4096)
        except socket.timeout as exc:
            raise TimeoutError("timed out waiting for socket response") from exc
        if not chunk:
            return bytes(chunks)
        newline = chunk.find(b"\n")
        if newline >= 0:
            chunks.extend(chunk[: newline + 1])
            return bytes(chunks)
        chunks.extend(chunk)


def send_message(socket_path: Path, message: dict, *, timeout: float | None = None) -> bytes:
    connect_path = resolve_socket_path(socket_path)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        if timeout is not None:
            client.settimeout(timeout)
        client.connect(str(connect_path))
        client.sendall(encode_message(message))
        client.shutdown(socket.SHUT_WR)
        return _read_line(client)


def serve_once(socket_path: Path, handler: Handler) -> threading.Thread:
    server, binding = _listening_socket(socket_path)
    return threading.Thread(target=_serve_once, args=(server, binding, handler), daemon=True)


def serve_forever(
    socket_path: Path,
    handler: Handler,
    *,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    event = threading.Event() if stop_event is None else stop_event
    server, binding = _listening_socket(socket_path)
    return threading.Thread(
        target=_serve_forever,
        args=(server, binding, handler, event),
        daemon=True,
    )


def _serve_once(server: socket.socket, binding: SocketBinding, handler: Handler) -> None:
    with server:
        try:
            conn, _ = server.accept()
            with conn:
                _handle_connection(conn, handler)
        finally:
            _unlink_binding(binding)


def _serve_forever(
    server: socket.socket,
    binding: SocketBinding,
    handler: Handler,
    stop_event: threading.Event,
) -> None:
    with server:
        server.settimeout(0.1)
        try:
            while not stop_event.is_set():
                try:
                    conn, _ = server.accept()
                except TimeoutError:
                    continue
                with conn:
                    conn.settimeout(ACCEPTED_CONNECTION_TIMEOUT)
                    try:
                        _handle_connection(conn, handler)
                    except Exception:
                        continue
        finally:
            _unlink_binding(binding)


def _listening_socket(socket_path: Path) -> tuple[socket.socket, SocketBinding]:
    binding = prepare_socket_path(socket_path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(binding.bind_path))
        server.listen()
        _publish_binding(binding)
        return server, binding
    except Exception:
        server.close()
        _unlink_binding(binding)
        raise


def _handle_connection(conn: socket.socket, handler: Handler) -> None:
    try:
        message = decode_message(_read_line(conn))
        response = handler(message)
        payload = encode_message(response)
    except Exception as exc:
        payload = encode_message(_error_response(exc))
    conn.sendall(payload)


def _error_response(exc: Exception) -> dict[str, str]:
    message = str(exc).strip().splitlines()[0] if str(exc).strip() else "request failed"
    return {"type": "error", "message": message[:200]}


def prepare_socket_path(socket_path: Path) -> SocketBinding:
    binding = _socket_binding(socket_path)
    binding.logical_path.parent.mkdir(parents=True, exist_ok=True)
    binding.bind_path.parent.mkdir(parents=True, exist_ok=True)
    if binding.uses_pointer:
        _unlink_stale_pointer(binding)
        _unlink_stale_socket(binding.bind_path)
    else:
        _unlink_stale_socket(binding.logical_path)
    return binding


def resolve_socket_path(socket_path: Path) -> Path:
    pointer = _read_socket_pointer(socket_path)
    if pointer is not None:
        return pointer
    binding = _socket_binding(socket_path)
    return binding.bind_path


def _unlink_stale_socket(socket_path: Path) -> None:
    try:
        mode = socket_path.stat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(mode):
        raise RuntimeError(f"socket path exists and is not a socket: {socket_path}")
    if _socket_accepts_connections(socket_path):
        raise RuntimeError(f"cocodex daemon is already running at {socket_path}")
    _unlink_socket(socket_path)


def _socket_accepts_connections(socket_path: Path) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.2)
            client.connect(str(socket_path))
            return True
    except (FileNotFoundError, ConnectionRefusedError, socket.timeout):
        return False
    except OSError as exc:
        if exc.errno in {errno.ENOENT, errno.ECONNREFUSED} or "AF_UNIX path too long" in str(exc):
            return False
        raise


def _unlink_socket(socket_path: Path) -> None:
    try:
        socket_path.unlink()
    except FileNotFoundError:
        pass


def _socket_binding(socket_path: Path) -> SocketBinding:
    logical_path = socket_path.resolve()
    if len(str(logical_path)) < MAX_UNIX_SOCKET_PATH:
        return SocketBinding(logical_path=logical_path, bind_path=logical_path, uses_pointer=False)
    return SocketBinding(
        logical_path=logical_path,
        bind_path=_runtime_socket_path(logical_path),
        uses_pointer=True,
    )


def _runtime_socket_path(logical_path: Path) -> Path:
    digest = hashlib.sha256(str(logical_path).encode("utf-8")).hexdigest()[:24]
    return Path(tempfile.gettempdir()) / f"cocodex-{os.getuid()}" / f"{digest}.sock"


def _publish_binding(binding: SocketBinding) -> None:
    if not binding.uses_pointer:
        return
    binding.logical_path.write_text(
        f"{SOCKET_POINTER_HEADER}\n{binding.bind_path}\n",
        encoding="utf-8",
    )


def _unlink_binding(binding: SocketBinding) -> None:
    _unlink_socket(binding.bind_path)
    if binding.uses_pointer:
        _unlink_socket(binding.logical_path)


def _unlink_stale_pointer(binding: SocketBinding) -> None:
    try:
        mode = binding.logical_path.stat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISSOCK(mode):
        _unlink_stale_socket(binding.logical_path)
        return
    pointer = _read_socket_pointer(binding.logical_path)
    if pointer is None:
        raise RuntimeError(f"socket path exists and is not a Cocodex socket pointer: {binding.logical_path}")
    if _socket_accepts_connections(pointer):
        raise RuntimeError(f"cocodex daemon is already running at {binding.logical_path}")
    _unlink_socket(binding.logical_path)
    _unlink_socket(pointer)


def _read_socket_pointer(socket_path: Path) -> Path | None:
    try:
        mode = socket_path.stat().st_mode
    except FileNotFoundError:
        return None
    if stat.S_ISSOCK(mode):
        return None
    if not stat.S_ISREG(mode):
        return None
    try:
        lines = socket_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if len(lines) < 2 or lines[0] != SOCKET_POINTER_HEADER:
        return None
    return Path(lines[1])
