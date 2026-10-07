"""Real local servers for proxy tests: an HTTP origin, an HTTP proxy (absolute-form + CONNECT) and a
SOCKS5 proxy (optional RFC 1929 auth). Each records what it saw, so a test can prove a request
actually went THROUGH the proxy instead of trusting the client's word for it. Mirrors the Node SDK's
test/helpers/proxies.ts. Loopback only, no internet."""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Started:
    def __init__(self, port, log, closer):
        self.port = port
        self.log = log
        self._closer = closer

    def close(self):
        self._closer()


def _pipe(a, b):
    def run(src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    t1 = threading.Thread(target=run, args=(a, b), daemon=True)
    t2 = threading.Thread(target=run, args=(b, a), daemon=True)
    t1.start()
    t2.start()


class _TcpServer:
    """Accept loop on a thread; handler(conn) runs per connection on its own thread."""

    def __init__(self, handler):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(64)
        self.port = self.sock.getsockname()[1]
        self.conns = set()
        self._handler = handler
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.conns.add(conn)
            threading.Thread(target=self._safe, args=(conn,), daemon=True).start()

    def _safe(self, conn):
        try:
            self._handler(conn)
        except Exception:  # noqa: BLE001
            try:
                conn.close()
            except OSError:
                pass

    def close(self):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass
        for c in list(self.conns):
            try:
                c.close()
            except OSError:
                pass


def start_origin(handler):
    """handler(method, path, headers, body) -> (status, body[, headers])."""
    log = []

    class H(BaseHTTPRequestHandler):
        def _do(self):
            n = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(n).decode() if n else ""
            headers = {k.lower(): v for k, v in self.headers.items()}
            log.append({"method": self.command, "url": self.path, "headers": headers, "body": body})
            r = handler(self.command, self.path, headers, body)
            status, text = r[0], r[1]
            extra = r[2] if len(r) > 2 else {}
            data = text.encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            for k, v in extra.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = _do

        def log_message(self, *_a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def closer():
        srv.shutdown()
        srv.server_close()

    return Started(srv.server_address[1], log, closer)


def _read_head(conn):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(65536)
        if not chunk:
            return None, b""
        buf += chunk
    i = buf.index(b"\r\n\r\n")
    return buf[:i].decode("latin-1"), buf[i + 4:]


def start_http_proxy(require_auth=None):
    log = []

    def handle(client):
        head, rest = _read_head(client)
        if head is None:
            client.close()
            return
        lines = head.split("\r\n")
        method, target = lines[0].split(" ")[:2]
        auth = None
        for line in lines[1:]:
            if line.lower().startswith("proxy-authorization:"):
                auth = line.split(":", 1)[1].strip()
        log.append({"kind": "connect" if method == "CONNECT" else "absolute", "target": target, "auth": auth})
        if require_auth and auth != require_auth:
            client.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
            client.close()
            return
        if method == "CONNECT":
            h, p = target.rsplit(":", 1)
            up = socket.create_connection((h, int(p)))
            client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            if rest:
                up.sendall(rest)
        else:
            from urllib.parse import urlsplit
            u = urlsplit(target)
            up = socket.create_connection((u.hostname, u.port or 80))
            fwd = [f"{method} {u.path or '/'}{('?' + u.query) if u.query else ''} HTTP/1.1"]
            fwd += [ln for ln in lines[1:] if not ln.lower().startswith("proxy-")]
            up.sendall(("\r\n".join(fwd) + "\r\n\r\n").encode("latin-1") + rest)
        _pipe(client, up)

    srv = _TcpServer(handle)
    return Started(srv.port, log, srv.close)


def start_socks5(user=None, password=None):
    log = []

    def read(conn, n):
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("eof")
            buf += chunk
        return buf

    def handle(client):
        ver, n_methods = read(client, 2)
        if ver != 5:
            client.close()
            return
        methods = list(read(client, n_methods))
        seen_user = None
        if user is not None:
            if 2 not in methods:
                client.sendall(bytes([5, 0xFF]))
                client.close()
                return
            client.sendall(bytes([5, 2]))
            _v, ulen = read(client, 2)
            seen_user = read(client, ulen).decode()
            (plen,) = read(client, 1)
            pw = read(client, plen).decode()
            ok = seen_user == user and pw == password
            client.sendall(bytes([1, 0 if ok else 1]))
            if not ok:
                client.close()
                return
        else:
            client.sendall(bytes([5, 0]))
        _v, _c, _r, atyp = read(client, 4)
        if atyp == 1:
            host = ".".join(str(b) for b in read(client, 4))
        elif atyp == 3:
            (hl,) = read(client, 1)
            host = read(client, hl).decode()
        else:
            client.close()
            return
        port = int.from_bytes(read(client, 2), "big")
        log.append({"host": host, "port": port, "user": seen_user})
        try:
            up = socket.create_connection((host, port))
        except OSError:
            client.sendall(bytes([5, 5, 0, 1, 0, 0, 0, 0, 0, 0]))
            client.close()
            return
        client.sendall(bytes([5, 0, 0, 1, 0, 0, 0, 0, 0, 0]))
        _pipe(client, up)

    srv = _TcpServer(handle)
    return Started(srv.port, log, srv.close)


def start_silent():
    """A TCP server that accepts and never answers."""
    held = []

    def handle(conn):
        held.append(conn)

    srv = _TcpServer(handle)
    return Started(srv.port, held, srv.close)


def json_body(obj):
    return json.dumps(obj)
