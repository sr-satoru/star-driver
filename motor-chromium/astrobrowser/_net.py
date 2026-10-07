"""Minimal HTTP(S) client that can go through an HTTP or SOCKS5 proxy, stdlib only.

Two SDK paths need to reach the network THROUGH the caller's proxy rather than from the host:

* geoip -- the exit IP must be the proxy's, or the timezone/locale describe the wrong place.
  Before this existed a SOCKS proxy could not be used for the lookup at all, and geoip was
  silently skipped for every SOCKS user.
* license_through_proxy -- lease checkout/heartbeat/checkin normally go direct, which reveals the
  host's real address to the licence server.

urllib cannot speak SOCKS, so this opens the tunnel itself: HTTP ``CONNECT`` (or absolute-form for
plain-http targets) or a SOCKS5 CONNECT with RFC 1929 credentials, then speaks HTTP/1.1 over the
socket with ``Connection: close``. One request per socket, buffered body. Mirrors the Node SDK's
``net.ts``.
"""
from __future__ import annotations

import base64
import json
import socket
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import unquote, urlsplit

__all__ = ["SimpleResponse", "to_proxy_spec", "proxied_request"]


class SimpleResponse:
    """The subset of a response both callers use."""

    def __init__(self, status: int, headers: dict, body: bytes):
        self.status = status
        self.ok = 200 <= status < 300
        self.headers = headers  # lower-cased names
        self._body = body

    def text(self) -> str:
        return self._body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text())


# The proxy schemes proxied_request can tunnel through.
PROXIED_REQUEST_SCHEMES = frozenset(("http", "https", "socks5", "socks5h"))


def _default_proxy_port(scheme: str) -> int:
    if scheme.startswith("socks"):
        return 1080
    if scheme == "https":
        return 443
    return 80


def to_proxy_spec(proxy):
    """Normalise a proxy given as a URL string (credentials inline) or a Playwright-style dict.

    Returns ``{"server": "scheme://host:port", "username"?, "password"?}`` or None. Raises
    ValueError for a value that is not a usable proxy URL.
    """
    if not proxy:
        return None
    if isinstance(proxy, dict):
        raw = proxy.get("server")
    else:
        raw = str(proxy)
    if not raw:
        return None
    raw = str(raw).strip()
    with_scheme = raw if "://" in raw else "http://" + raw
    u = urlsplit(with_scheme)
    scheme = (u.scheme or "").lower()
    try:
        host = u.hostname
        port = u.port
    except ValueError as exc:  # bad port
        raise ValueError(f"invalid proxy URL {raw!r}: {exc}") from None
    if not scheme or not host:
        raise ValueError(f"invalid proxy URL {raw!r}")
    username = (proxy.get("username") if isinstance(proxy, dict) else None) or unquote(u.username or "")
    password = (proxy.get("password") if isinstance(proxy, dict) else None) or unquote(u.password or "")
    host_part = f"[{host}]" if ":" in host else host
    spec = {"server": f"{scheme}://{host_part}:{port or _default_proxy_port(scheme)}"}
    if username:
        spec["username"] = username
    if password:
        spec["password"] = password
    return spec


class _Deadline:
    def __init__(self, seconds: float):
        self.end = time.monotonic() + seconds

    def left(self) -> float:
        return max(0.001, self.end - time.monotonic())

    def expired(self) -> bool:
        return time.monotonic() >= self.end


def _recv_until(sock, predicate, deadline: _Deadline, what="proxy handshake"):
    """Read until ``predicate(buf)`` returns an end offset >= 0. Returns (head, rest)."""
    buf = b""
    while True:
        n = predicate(buf)
        if n >= 0:
            return buf[:n], buf[n:]
        if deadline.expired():
            raise TimeoutError(f"{what} timed out")
        sock.settimeout(deadline.left())
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            raise TimeoutError(f"{what} timed out") from None
        if not chunk:
            raise ConnectionError("proxy closed the connection during the handshake")
        buf += chunk


def _socks5_connect(sock, proxy, host, port, deadline):
    user = proxy.get("username") or ""
    pw = proxy.get("password") or ""
    creds = bool(user or pw)
    sock.sendall(bytes([5, 2, 0, 2]) if creds else bytes([5, 1, 0]))
    hello, rest = _recv_until(sock, lambda b: 2 if len(b) >= 2 else -1, deadline)
    if hello[0] != 5:
        raise ConnectionError("SOCKS5 proxy: bad greeting reply")
    method = hello[1]
    if method == 2:
        u = user.encode("utf-8")
        p = pw.encode("utf-8")
        if len(u) > 255 or len(p) > 255:
            raise ConnectionError("SOCKS5 proxy: credentials longer than 255 bytes")
        sock.sendall(bytes([1, len(u)]) + u + bytes([len(p)]) + p)
        auth, rest = _recv_until(sock, lambda b: 2 if len(b) >= 2 else -1, deadline)
        if auth[1] != 0:
            raise ConnectionError("SOCKS5 proxy rejected the username/password")
    elif method != 0:
        raise ConnectionError(
            "SOCKS5 proxy accepted no offered authentication method" if method == 0xFF
            else f"SOCKS5 proxy chose unsupported method {method}")
    h = host.encode("utf-8")
    sock.sendall(bytes([5, 1, 0, 3, len(h)]) + h + bytes([(port >> 8) & 0xFF, port & 0xFF]))

    def reply_len(b):
        if len(b) < 5:
            return -1
        atyp = b[3]
        addr = 4 if atyp == 1 else 16 if atyp == 4 else (1 + b[4]) if atyp == 3 else -1
        if addr < 0:
            return 4
        total = 4 + addr + 2
        return total if len(b) >= total else -1

    reply, rest = _recv_until(sock, reply_len, deadline)
    if reply[1] != 0:
        raise ConnectionError(f"SOCKS5 proxy refused the connection (reply code {reply[1]})")
    return rest


def _basic_auth(proxy):
    if not proxy.get("username") and not proxy.get("password"):
        return None
    raw = f"{proxy.get('username') or ''}:{proxy.get('password') or ''}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _dechunk(body: bytes) -> bytes:
    """Decode a chunked body. A body without the terminating zero-size chunk (or with a short chunk)
    is truncated, and raises rather than being returned as if complete."""
    out = []
    i = 0
    while True:
        eol = body.find(b"\r\n", i)
        if eol < 0:
            raise ConnectionError("incomplete chunked response body")
        try:
            size = int(body[i:eol].split(b";")[0].strip(), 16)
        except ValueError:
            raise ConnectionError("malformed chunked response body") from None
        if size == 0:
            return b"".join(out)
        chunk = body[eol + 2:eol + 2 + size]
        if len(chunk) < size:
            raise ConnectionError("incomplete chunked response body")
        out.append(chunk)
        i = eol + 2 + size + 2


def _parse_response(raw: bytes) -> SimpleResponse:
    sep = raw.find(b"\r\n\r\n")
    if sep < 0:
        raise ConnectionError("malformed HTTP response (no header terminator)")
    lines = raw[:sep].decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
        raise ConnectionError(f"malformed HTTP status line: {lines[0]}")
    status = int(parts[1])
    headers = {}
    for line in lines[1:]:
        k, sep2, v = line.partition(":")
        if sep2 and k:
            headers[k.strip().lower()] = v.strip()
    body = raw[sep + 4:]
    if "chunked" in headers.get("transfer-encoding", "").lower():
        body = _dechunk(body)
    elif "content-length" in headers:
        try:
            expected = int(headers["content-length"])
        except ValueError:
            expected = None
        if expected is not None:
            if len(body) < expected:
                raise ConnectionError(
                    f"incomplete response body ({len(body)} of {expected} bytes)")
            body = body[:expected]
    return SimpleResponse(status, headers, body)


def _direct(url, method, headers, body, timeout):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return SimpleResponse(resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read())
    except urllib.error.HTTPError as e:
        return SimpleResponse(e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read() or b"")


def proxied_request(url, method="GET", headers=None, body=None, timeout=30.0, proxy=None):
    """Make one HTTP(S) request, through ``proxy`` when given (HTTP CONNECT / absolute-form, or
    SOCKS5), otherwise directly. Raises on network/proxy failure; HTTP error statuses return a
    response with ``ok`` False. ``timeout`` (seconds) bounds the whole request."""
    headers = dict(headers or {})
    data = body.encode("utf-8") if isinstance(body, str) else body
    spec = to_proxy_spec(proxy) if proxy else None
    if not spec:
        return _direct(url, method, headers, data, timeout)

    deadline = _Deadline(timeout)
    target = urlsplit(url)
    is_https = target.scheme == "https"
    host = target.hostname or ""
    authority = f"[{host}]" if ":" in host else host  # IPv6 literals are bracketed on the wire
    port = target.port or (443 if is_https else 80)
    p = urlsplit(spec["server"])
    scheme = p.scheme.lower()
    try:
        sock = socket.create_connection((p.hostname, p.port), timeout=deadline.left())
    except socket.timeout:
        raise TimeoutError(f"connect to {p.hostname}:{p.port} timed out") from None
    stream = sock
    try:
        absolute_form = False
        leftover = b""
        if scheme in ("socks5", "socks5h"):
            leftover = _socks5_connect(sock, spec, host, port, deadline)
        elif scheme in ("http", "https"):
            if scheme == "https":
                stream = ssl.create_default_context().wrap_socket(sock, server_hostname=p.hostname)
            if is_https:
                auth = _basic_auth(spec)
                head = f"CONNECT {authority}:{port} HTTP/1.1\r\nHost: {authority}:{port}\r\n"
                if auth:
                    head += f"Proxy-Authorization: {auth}\r\n"
                stream.sendall((head + "\r\n").encode("latin-1"))
                resp_head, leftover = _recv_until(
                    stream, lambda b: (b.find(b"\r\n\r\n") + 4) if b"\r\n\r\n" in b else -1, deadline)
                parts = resp_head.split(b" ")
                code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                if code != 200:
                    raise ConnectionError(
                        "HTTP proxy requires (different) credentials (407)" if code == 407
                        else f"HTTP proxy refused CONNECT ({code or 'bad reply'})")
            else:
                absolute_form = True
        else:
            raise ValueError(f"unsupported proxy scheme '{scheme}' (use http://, https:// or socks5://)")
        if leftover and is_https:
            raise ConnectionError("proxy sent unexpected data after the handshake")

        if is_https:
            stream = ssl.create_default_context().wrap_socket(stream, server_hostname=host)
        path = (target.path or "/") + (("?" + target.query) if target.query else "")
        req_headers = {
            "Host": f"{authority}:{target.port}" if target.port else authority,
            "User-Agent": "clearcote-sdk",
            "Accept": "*/*",
            "Connection": "close",
        }
        req_headers.update(headers)
        if absolute_form:
            auth = _basic_auth(spec)
            if auth:
                req_headers["Proxy-Authorization"] = auth
        if data is not None:
            req_headers["Content-Length"] = str(len(data))
        head = f"{method} {url if absolute_form else path} HTTP/1.1\r\n"
        head += "".join(f"{k}: {v}\r\n" for k, v in req_headers.items()) + "\r\n"
        stream.sendall(head.encode("latin-1") + (data or b""))

        chunks = [leftover] if leftover else []
        while True:
            if deadline.expired():
                raise TimeoutError(f"request to {url} timed out")
            stream.settimeout(deadline.left())
            try:
                chunk = stream.recv(65536)
            except socket.timeout:
                raise TimeoutError(f"request to {url} timed out") from None
            except (ssl.SSLError, ConnectionResetError):
                if chunks:
                    break
                raise
            if not chunk:
                break
            chunks.append(chunk)
        return _parse_response(b"".join(chunks))
    finally:
        try:
            stream.close()
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass
