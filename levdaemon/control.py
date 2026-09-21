"""Control-socket client. Protocol facts come from the daemon source:
crates/leviath-runtime/src/control_socket/mod.rs (newline-delimited JSON,
tagged by ``op`` / ``result``, snake_case). Verified live against
fixtures/control_welcome.json and fixtures/control_list.json.
"""

from __future__ import annotations

import json
import os
import socket
from typing import Any, Dict, Optional

DEFAULT_HOME = os.environ.get("LEVIATH_HOME", "/data/.leviath")


class DaemonError(RuntimeError):
    """The daemon answered with a ``result: error`` frame."""


class ControlUnavailable(RuntimeError):
    """No control socket or token is present."""


class ControlClient:
    """One line-delimited JSON conversation with the daemon control socket."""

    def __init__(self, home: str = DEFAULT_HOME, timeout: float = 15.0):
        self.home = home
        self.sock_path = os.path.join(home, "control.sock")
        self.token_path = os.path.join(home, "control.token")
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._file = None
        self.welcome: Optional[Dict[str, Any]] = None

    # -- lifecycle ---------------------------------------------------------
    @classmethod
    def available(cls, home: str = DEFAULT_HOME) -> bool:
        return os.path.exists(os.path.join(home, "control.sock")) and os.path.exists(
            os.path.join(home, "control.token")
        )

    def connect(self) -> "ControlClient":
        if not self.available(self.home):
            raise ControlUnavailable(f"no control socket/token under {self.home}")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self.sock_path)
        self._sock = s
        self._file = s.makefile("rwb")
        with open(self.token_path) as fh:
            token = fh.read().strip()
        self.welcome = self.call({"op": "authenticate", "token": token, "hello": True})
        return self

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            finally:
                self._file = None
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self) -> "ControlClient":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- protocol ----------------------------------------------------------
    def call(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Send one request, read one reply, raise on an error reply."""
        if self._file is None:
            raise ControlUnavailable("control socket is not connected")
        self._file.write((json.dumps(request) + "\n").encode())
        self._file.flush()
        line = self._file.readline()
        if not line:
            raise ControlUnavailable("control socket closed mid-request")
        reply = json.loads(line.decode())
        if reply.get("result") == "error":
            raise DaemonError(reply.get("message", "daemon error"))
        return reply

    # -- ops ---------------------------------------------------------------
    def list_raw(self) -> Dict[str, Any]:
        return self.call({"op": "list"})

    def status_raw(self, run_id: str) -> Dict[str, Any]:
        return self.call({"op": "status", "run_id": run_id})

    def interactions_raw(self) -> Dict[str, Any]:
        return self.call({"op": "list_interactions"})

    def cancel(self, run_id: str) -> Dict[str, Any]:
        return self.call({"op": "cancel", "run_id": run_id})

    def pause(self, run_id: str) -> Dict[str, Any]:
        return self.call({"op": "pause", "run_id": run_id})

    def resume(self, run_id: str) -> Dict[str, Any]:
        return self.call({"op": "resume", "run_id": run_id})
