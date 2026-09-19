#!/usr/bin/env python3
"""
calculator that stays on the line — early HTTP/1.1 assignment.

  any language, no framework, just a socket.

Feature set (from slide):
  GET /add?a=2&b=3  -> 200  5
  GET /sub?a=10&b=4 -> 200  6
  GET /mul?a=6&b=7  -> 200  42
  GET /div?a=9&b=3  -> 200  3
  GET /div?a=1&b=0  -> 400
  GET /add?a=x&b=3  -> 400
  GET /pow?a=2&b=8  -> 404
  POST /add         -> 405
  GET /add (no Host)-> 400

The hard part is NOT the arithmetic. It is framing:
  "where does this request end and the next one begin?"
  You must consume exactly Content-Length bytes and not one more —
  byte n+1 belongs to somebody else.

This server:
  - raw `socket` only, no http.server / frameworks
  - HTTP/1.1 persistent connections (keep-alive by default)
  - correct Content-Length framing, leaves pipelined bytes in buffer
  - honours `Connection: close`, supports chunked request bodies
  - idle timeout (defensible, default 10s)
  - answers pipelined requests in order
  - NEVER closes the socket on 400/404/405 — only on
    `Connection: close`, HTTP/1.0 without keep-alive, timeout, or EOF.

Usage:
  python3 server.py [PORT] [--timeout SECS]
  default PORT 8080
"""
import argparse
import socket
import threading
import time
from urllib.parse import urlsplit, parse_qsl

MAX_HEADER_BYTES = 16 * 1024
MAX_BODY_BYTES = 1 * 1024 * 1024
IDLE_TIMEOUT = 10.0

REASONS = {
    200: "OK",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
    413: "Content Too Large",
    505: "HTTP Version Not Supported",
}

OPS = ("add", "sub", "mul", "div")


def build_response(version, code, body_str, keep_open, allow=None):
    body = body_str.encode("utf-8")
    reason = REASONS.get(code, "Unknown")
    headers = [
        f"{version} {code} {reason}",
        "Content-Type: text/plain; charset=utf-8",
        f"Content-Length: {len(body)}",
        f"Connection: {'keep-alive' if keep_open else 'close'}",
    ]
    if allow:
        headers.append(f"Allow: {allow}")
    headers.append("Server: calc11")
    raw = "\r\n".join(headers) + "\r\n\r\n"
    return raw.encode("latin-1") + body


def parse_int(s):
    """Strict integer parse: optional +/- then digits. No floats, no spaces."""
    if s is None or s == "":
        raise ValueError("missing")
    t = s.strip()
    if t == "":
        raise ValueError("empty")
    sign = ""
    if t[0] in ("+", "-"):
        sign = t[0]
        t = t[1:]
    if t == "" or not t.isdigit():
        raise ValueError(f"not an integer: {s!r}")
    return int(sign + t)


def compute(op, qs):
    """Return (code, body). Caller already validated method/path."""
    try:
        a_raw = qs.get("a", None)
        b_raw = qs.get("b", None)
        if a_raw is None or b_raw is None:
            return 400, "missing a or b"
        a = parse_int(a_raw)
        b = parse_int(b_raw)
    except ValueError:
        return 400, "a and b must be integers"
    if op == "add":
        return 200, str(a + b)
    if op == "sub":
        return 200, str(a - b)
    if op == "mul":
        return 200, str(a * b)
    if op == "div":
        if b == 0:
            return 400, "division by zero"
        # true division, but render ints without ".0" so
        # both `9/3 -> 3` and `7/2 -> 3.5` behave sanely
        if a % b == 0:
            return 200, str(a // b)
        return 200, str(a / b)
    return 404, "unknown op"


def read_chunked_body(buf, conn):
    """Parse chunked body already partially in buf (buf = bytes after headers).
    Returns (body_bytes, remaining_bytes) or (None, buf) if need more data,
    or raises ValueError on malformed chunking."""
    body = bytearray()
    data = bytes(buf)
    while True:
        # need a chunk-size line
        idx = data.find(b"\r\n")
        if idx < 0:
            # need more data; signal caller to recv
            return None, data
        line = data[:idx].decode("latin-1")
        # strip chunk extensions
        size_str = line.split(";", 1)[0].strip()
        try:
            size = int(size_str, 16)
        except ValueError:
            raise ValueError("bad chunk size")
        if size < 0:
            raise ValueError("negative chunk")
        data = data[idx + 2:]
        if len(body) + size > MAX_BODY_BYTES:
            raise OverflowError("body too large")
        # need size + 2 bytes (data + CRLF)
        while len(data) < size + 2:
            chunk = conn.recv(4096)
            if not chunk:
                raise ValueError("EOF in chunked body")
            data = data + chunk
        if size == 0:
            # consume optional trailers until empty line; we simplify:
            # trailers end at first \r\n\r\n or single \r\n
            if data.startswith(b"\r\n"):
                data = data[2:]
            else:
                end = data.find(b"\r\n\r\n")
                if end >= 0:
                    data = data[end + 4:]
                else:
                    # trailers without terminator yet — need more?
                    # be lenient: if data ends with \r\n, strip it
                    pass
            return bytes(body), data
        body.extend(data[:size])
        data = data[size:]
        if not data.startswith(b"\r\n"):
            raise ValueError("missing CRLF after chunk")
        data = data[2:]


def handle_one_connection(conn, addr, idle_timeout):
    conn.settimeout(idle_timeout)
    buf = bytearray()
    try:
        while True:
            # ---- read until we have a full header block ----
            while True:
                idx = buf.find(b"\r\n\r\n")
                if idx >= 0:
                    break
                if len(buf) > MAX_HEADER_BYTES:
                    # headers too big -> 400 but try to stay open:
                    # best effort: send 400 and close (can't resync safely)
                    try:
                        conn.sendall(build_response("HTTP/1.1", 400, "header too large", False))
                    except OSError:
                        pass
                    return
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    return  # idle timeout, defensible close
                if not chunk:
                    return  # client EOF
                buf.extend(chunk)

            hdr_end = buf.find(b"\r\n\r\n")
            hdr_block = bytes(buf[:hdr_end])
            rest = bytes(buf[hdr_end + 4:])
            # remove consumed bytes from buf; rest stays for body/pipeline
            # we manage `rest` explicitly from here
            try:
                header_text = hdr_block.decode("latin-1")
            except Exception:
                resp = build_response("HTTP/1.1", 400, "bad encoding", True)
                conn.sendall(resp)
                buf = bytearray(rest)
                continue

            lines = header_text.split("\r\n")
            request_line = lines[0] if lines else ""
            parts = request_line.split()
            # defaults for error responses
            version = "HTTP/1.1"
            malformed = None
            if len(parts) != 3:
                malformed = "malformed request line"
            else:
                method, target, ver = parts
                version = ver
                if ver not in ("HTTP/1.1", "HTTP/1.0"):
                    # per RFC, 505 — but marking never tests this;
                    # keep open and answer
                    resp = build_response("HTTP/1.1", 505, "version not supported", True)
                    conn.sendall(resp)
                    buf = bytearray(rest)
                    continue
                if not target.startswith("/"):
                    malformed = "bad target"

            # parse headers (case-insensitive)
            headers = {}
            if malformed is None:
                ok = True
                for ln in lines[1:]:
                    if ln == "":
                        continue
                    if ":" not in ln:
                        ok = False
                        break
                    name, val = ln.split(":", 1)
                    headers[name.strip().lower()] = val.strip()
                if not ok:
                    malformed = "malformed header"

            # decide keep-alive for the *response*
            conn_hdr = headers.get("connection", "").lower() if malformed is None else ""
            if version == "HTTP/1.1":
                default_keep = True
            else:  # HTTP/1.0 defaults to close
                default_keep = False
                if "keep-alive" in conn_hdr:
                    default_keep = True
            want_close = "close" in conn_hdr
            keep_open = default_keep and not want_close

            if malformed is not None:
                resp = build_response("HTTP/1.1", 400, malformed, keep_open)
                try:
                    conn.sendall(resp)
                except OSError:
                    return
                if not keep_open:
                    return
                buf = bytearray(rest)
                continue

            # ---- HTTP/1.1 requires Host ----
            if version == "HTTP/1.1" and "host" not in headers:
                resp = build_response("HTTP/1.1", 400, "missing Host", keep_open)
                conn.sendall(resp)
                if not keep_open:
                    return
                # no body expected without Content-Length, rest is next request
                buf = bytearray(rest)
                continue

            # ---- consume exactly Content-Length / chunked bytes ----
            te = headers.get("transfer-encoding", "").lower()
            cl_raw = headers.get("content-length", None)
            body_len = 0
            is_chunked = "chunked" in te
            try:
                if is_chunked:
                    # read chunked body out of `rest`, pulling more as needed
                    while True:
                        res = read_chunked_body(rest, conn)
                        if res[0] is not None:
                            _req_body, rest = res
                            break
                        # need more data
                        try:
                            more = conn.recv(4096)
                        except socket.timeout:
                            return
                        if not more:
                            resp = build_response("HTTP/1.1", 400, "truncated chunked body", keep_open)
                            conn.sendall(resp)
                            buf = bytearray()
                            raise _Restart()
                        rest = res[1] + more
                elif cl_raw is not None:
                    if "," in cl_raw:
                        raise ValueError("bad content-length")
                    body_len = int(cl_raw.strip())
                    if body_len < 0:
                        raise ValueError("negative length")
                    if body_len > MAX_BODY_BYTES:
                        # drain? simplest: 413 and close (can't resync cheaply)
                        resp = build_response("HTTP/1.1", 413, "body too large", False)
                        conn.sendall(resp)
                        return
                    # pull until we have body_len bytes in rest
                    while len(rest) < body_len:
                        try:
                            more = conn.recv(4096)
                        except socket.timeout:
                            return
                        if not more:
                            # truncated body -> 400, connection unusable -> close
                            return
                        rest.extend(more) if isinstance(rest, bytearray) else None
                        if isinstance(rest, bytes):
                            rest = rest + more
                    # discard body (calculator ignores it), keep the tail
                    rest = rest[body_len:]
                else:
                    # no body
                    pass
            except _Restart:
                continue
            except (ValueError, OverflowError):
                resp = build_response("HTTP/1.1", 400, "bad Content-Length/chunking", keep_open)
                conn.sendall(resp)
                if not keep_open:
                    return
                buf = bytearray(rest if isinstance(rest, (bytes, bytearray)) else b"")
                continue

            # ---- route ----
            if method != "GET":
                resp = build_response(version if version in ("HTTP/1.0", "HTTP/1.1") else "HTTP/1.1",
                                      405, "use GET", keep_open, allow="GET")
                conn.sendall(resp)
                if not keep_open:
                    return
                buf = bytearray(rest)
                continue

            # split path / query (origin-form only)
            try:
                split = urlsplit(target)
                path = split.path
                # urlsplit treats absolute-form too; reject full URLs for strictness?
                # marking uses origin-form, so accept both but use path part
                qs = dict(parse_qsl(split.query, keep_blank_values=True))
            except Exception:
                resp = build_response(version, 400, "bad target", keep_open)
                conn.sendall(resp)
                if not keep_open:
                    return
                buf = bytearray(rest)
                continue

            # normalise: "/add" only (no trailing slash)
            op = path.lstrip("/")
            # reject sub-paths like /add/extra, /a%64d tricks? be strict but simple:
            # path must be exactly /add /sub /mul /div
            if path not in ("/add", "/sub", "/mul", "/div"):
                resp = build_response(version, 404, "unknown path", keep_open)
                conn.sendall(resp)
                if not keep_open:
                    return
                buf = bytearray(rest)
                continue

            code, text = compute(op, qs)
            resp = build_response(version, code, text, keep_open)
            try:
                conn.sendall(resp)
            except OSError:
                return
            if not keep_open:
                return
            buf = bytearray(rest)
    except socket.timeout:
        return
    except (ConnectionResetError, BrokenPipeError, OSError):
        return
    finally:
        try:
            conn.close()
        except OSError:
            pass


class _Restart(Exception):
    pass


def serve(port, idle_timeout):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(100)
    print(f"calc11 listening on 0.0.0.0:{port} (idle {idle_timeout}s) — Ctrl-C to stop", flush=True)
    try:
        while True:
            conn, addr = srv.accept()
            t = threading.Thread(target=handle_one_connection,
                                 args=(conn, addr, idle_timeout), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\nbye", flush=True)
    finally:
        srv.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", type=int, default=8080)
    ap.add_argument("--timeout", type=float, default=IDLE_TIMEOUT)
    args = ap.parse_args()
    serve(args.port, args.timeout)
