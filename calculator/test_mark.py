#!/usr/bin/env python3
"""Replica of the marking script from the slide: one socket, every request."""
import socket
import sys

HOST = sys.argv[1] if len(sys.argv) > 1 else "localhost"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8080

CASES = [
    ("GET /add?a=2&b=3", 200, "5"),
    ("GET /sub?a=10&b=4", 200, "6"),
    ("GET /mul?a=6&b=7", 200, "42"),
    ("GET /div?a=1&b=0", 400, None),
    ("GET /pow?a=2&b=8", 404, None),
    ("POST /add", 405, None),
]

def recv_response(f):
    status_line = f.readline().decode("latin-1")
    if not status_line:
        raise RuntimeError("EOF waiting for status line (server closed socket!)")
    parts = status_line.strip().split(None, 2)
    code = int(parts[1])
    headers = {}
    while True:
        line = f.readline().decode("latin-1")
        if line in ("\r\n", "\n", ""):
            break
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    n = int(headers.get("content-length", "0"))
    body = f.read(n).decode() if n else ""
    return code, body, headers

def main():
    s = socket.create_connection((HOST, PORT), timeout=5)
    f = s.makefile("rb")
    print(f"connected {HOST}:{PORT}, 1 TCP handshake")
    ok = True
    for req, want_code, want_body in CASES:
        method, target = req.split(" ", 1)
        raw = (f"{method} {target} HTTP/1.1\r\nHost: {HOST}\r\n"
               f"Connection: keep-alive\r\n\r\n")
        s.sendall(raw.encode())
        code, body, _ = recv_response(f)
        mark = "OK " if code == want_code else "FAIL"
        if want_body is not None and body != want_body:
            mark = "FAIL"
            ok = False
        if code != want_code:
            ok = False
        print(f"  {mark} {req:22s} -> {code} {body!r} (want {want_code} {want_body!r})")
    # socket still open?
    try:
        s.settimeout(2)
        s.sendall(b"GET /add?a=1&b=1 HTTP/1.1\r\nHost: x\r\nConnection: keep-alive\r\n\r\n")
        code, body, _ = recv_response(f)
        still = code == 200 and body == "2"
        print(f"  socket still open: {still} (probe -> {code} {body!r})")
        if not still:
            ok = False
    except Exception as e:
        print(f"  socket still open: False ({e})")
        ok = False
    print("1 TCP handshake, 6 responses" if ok else "FAILED")
    # extra: missing Host must be 400 but keep open
    try:
        s.sendall(b"GET /add?a=1&b=2 HTTP/1.1\r\nConnection: keep-alive\r\n\r\n")
        code, body, _ = recv_response(f)
        print(f"  no-Host probe -> {code} (want 400) {'OK' if code==400 else 'FAIL'}")
        if code != 400:
            ok = False
    except Exception as e:
        print(f"  no-Host probe failed: {e}")
        ok = False
    s.close()
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
