"""Threaded JSON-over-socket client for the xtu-linux daemon.

Socket I/O runs on a worker QThread so the UI thread is never blocked. Every
request is enqueued and handled in-order by the worker; responses are emitted
back on the Qt main thread via signals.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal, QThread

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import protocol  # noqa: E402


class DaemonWorker(QThread):
    """Owns the socket connection and processes a FIFO request queue."""

    response_ready = Signal(object)   # dict response
    connection_error = Signal(str)    # message

    def __init__(self, socket_path: str, tcp_port: int | None = None, parent=None):
        super().__init__(parent)
        self.socket_path = socket_path
        self.tcp_port = tcp_port
        self._queue = []
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._connected = False
        self._conn = None

    # -- public API (call from UI thread) -------------------------------------
    def request(self, payload: dict) -> None:
        """Enqueue a request (e.g. {"cmd": "get_status"})."""
        self._queue.append(payload)
        self._wake.set()

    def stop(self) -> None:
        """Ask the worker thread to terminate after draining the queue."""
        self._stop.set()
        self._wake.set()

    # -- thread body ----------------------------------------------------------
    def _connect(self) -> bool:
        try:
            if self.tcp_port is not None:
                conn = socket.create_connection(("127.0.0.1", self.tcp_port))
            else:
                conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                conn.connect(self.socket_path)
            self._conn = conn
            self._connected = True
            return True
        except (OSError, AttributeError) as exc:
            self.connection_error.emit(f"Cannot connect to daemon: {exc}")
            return False

    def _send_receive(self, payload: dict):
        if not self._connected and not self._connect():
            return
        try:
            self._conn.sendall(json.dumps(payload).encode("utf-8") + b"\n")
            data = b""
            while b"\n" not in data:
                chunk = self._conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            response = json.loads(data.decode("utf-8"))
            response["_cmd"] = payload.get("cmd")
            self.response_ready.emit(response)
        except (OSError, ValueError) as exc:
            self._connected = False
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None
            self.connection_error.emit(f"Daemon connection lost: {exc}")

    def run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            self._wake.clear()
            while self._queue and not self._stop.is_set():
                payload = self._queue.pop(0)
                if payload:
                    self._send_receive(payload)
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass


class DaemonClient(QObject):
    """Thin wrapper presenting the worker's signals to the UI."""

    response_ready = Signal(object)
    connection_error = Signal(str)

    def __init__(self, socket_path: str = "/run/xtu-linux.sock",
                 tcp_port: int | None = None, parent=None):
        super().__init__(parent)
        self.socket_path = socket_path
        self.worker = DaemonWorker(socket_path, tcp_port=tcp_port, parent=self)
        self.worker.response_ready.connect(self.response_ready)
        self.worker.connection_error.connect(self.connection_error)
        self.worker.start()

    def request(self, cmd: str, **kwargs) -> None:
        payload = {"cmd": cmd}
        payload.update(kwargs)
        self.worker.request(payload)

    def close(self) -> None:
        self.worker.stop()
