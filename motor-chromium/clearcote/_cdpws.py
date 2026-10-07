"""A minimal, synchronous CDP client over a WebSocket, on the standard library only.

``serve()`` launches the engine itself, so there is no Playwright connection to borrow, and the SDK
depends on nothing that speaks WebSocket. What the headless window fit needs is small: a handful of
request/response calls on the browser endpoint (flat sessions for the one page it reads), text
frames only, never a subscription. That is all this implements — deliberately not a general client.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import time
import urllib.parse


class CdpError(Exception):
    pass


class CdpConnection:
    """One browser-level CDP WebSocket. ``send()`` blocks for its own reply; events are dropped."""

    def __init__(self, ws_url, timeout=5.0):
        u = urllib.parse.urlparse(ws_url)
        if u.scheme != "ws" or not u.hostname:
            raise CdpError("expected a ws:// CDP URL, got %r" % (ws_url,))
        self._timeout = timeout
        self._sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        self._buf = b""
        self._next = 0
        try:
            self._handshake(u)
        except Exception:
            self.close()
            raise

    # -- the WebSocket layer (RFC 6455, client side) ---------------------------------------------
    def _handshake(self, u):
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        host = "%s:%d" % (u.hostname, u.port or 80)
        self._sock.sendall((
            "GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, host, key)
        ).encode("ascii"))
        while b"\r\n\r\n" not in self._buf:
            self._fill()
        head, self._buf = self._buf.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status + b" ":
            raise CdpError("CDP WebSocket upgrade refused: %s" % status.decode("latin-1"))

    def _fill(self):
        chunk = self._sock.recv(65536)
        if not chunk:
            raise CdpError("CDP connection closed")
        self._buf += chunk

    def _take(self, n):
        while len(self._buf) < n:
            self._fill()
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_frame(self, opcode, payload):
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(head + mask + masked)

    def _recv_message(self):
        """The next complete text message (fragments joined; pings answered)."""
        parts = []
        while True:
            b0, b1 = self._take(2)
            fin, opcode, n = b0 & 0x80, b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._take(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._take(8))[0]
            mask = self._take(4) if b1 & 0x80 else None
            payload = self._take(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise CdpError("CDP connection closed")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            parts.append(payload)
            if fin:
                return b"".join(parts).decode("utf-8")

    # -- the CDP layer ------------------------------------------------------------------------------
    def send(self, method, params=None, session_id=None):
        """Call ``method`` and return its result; raises :class:`CdpError` on a protocol error."""
        self._next += 1
        msg_id = self._next
        msg = {"id": msg_id, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        self._send_frame(0x1, json.dumps(msg).encode("utf-8"))
        deadline = time.monotonic() + self._timeout
        while True:
            if time.monotonic() > deadline:
                raise CdpError("%s timed out" % method)
            reply = json.loads(self._recv_message())
            if reply.get("id") != msg_id:
                continue  # an event, or a stale reply: this client subscribes to nothing
            if "error" in reply:
                raise CdpError(reply["error"].get("message", str(reply["error"])))
            return reply.get("result") or {}

    def close(self):
        try:
            self._send_frame(0x8, b"")
        except Exception:  # noqa: BLE001
            pass
        try:
            self._sock.close()
        except Exception:  # noqa: BLE001
            pass
