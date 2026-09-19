# netwrk — calculator that stays on the line + binary HTTP

Two assignments from `Network Architecture Assignment_ Build a Calculator that stays on the line.pdf`,
both implemented with **raw sockets, no frameworks** (stdlib only).

```
netwrk/
  calculator/
    server.py      # early HTTP/1.1 assignment — persistent calculator
    test_mark.py   # replica of the "How I will mark it" script (one socket, every request)
  bproto/
    SPEC.md                # course project — 2-page spec (the actual project)
    bhp.py                 # shared framing (imported by both ends, still stdlib+sockets)
    bserve                 # Track 1 — the server  ($ ./bserve ./www 9000)
    bcurl                  # Track 2 — the client  ($ ./bcurl -v localhost:9000/index.html)
    www/index.html, hello.txt
    HEXDUMP_annotated.txt  # annotated bytes of one request+response
```

## 1. Calculator (early HTTP/1.1, due before session 7)

```bash
python3 calculator/server.py 8080
python3 calculator/test_mark.py localhost 8080
```

Behaviour: `GET /add|sub|mul|div?a=N&b=M` → `200` + result;
`/div` by zero, non-integer `a`/`b`, missing param → `400`;
unknown path → `404`; non-GET → `405` (+`Allow: GET`);
missing `Host:` on HTTP/1.1 → `400`. Arithmetic is strict integers
(`7/2 → 3.5`, `9/3 → 3`).

The hard part — framing — is the point: headers are read until
`\r\n\r\n`, then **exactly** `Content-Length` bytes are consumed
(and `Transfer-Encoding: chunked` is decoded); byte `n+1` stays in the
buffer for the next pipelined request. Responses always carry
`Content-Length` so the client can frame too. `Connection: close`
is honoured (HTTP/1.0 defaults to close), idle timeout 10 s, and the
socket is **never** closed on 400/404/405 — the marker's
`socket still open: True` + `1 TCP handshake, 6 responses` passes,
including pipelined "all six at once, answer in order".

## 2. Binary HTTP (course project — you write the spec)

```bash
./bproto/bserve ./bproto/www 9000
./bproto/bcurl -v localhost:9000/index.html          # body → stdout, frames → stderr
./bproto/bcurl localhost:9000/hello.txt localhost:9000/index.html   # one connection, two files
```

The spec is `bproto/SPEC.md`. Header in one line:
`24-bit Length | 8-bit Type | 8-bit Flags | 16-bit ReqID | 8-bit Reserved`
(= 8 bytes, 64-bit aligned; HTTP/2's 24/8/8/31 needs 9 unaligned bytes
because browsers multiplex — we stay sequential, so 16-bit IDs suffice).
Headers use HPACK's first two tricks (indexed 10 names + length-prefixed
literals, no Huffman/dynamic table). Unknown frame types **MUST be
skipped cleanly via Length** — that rule is version 2's room.
`HEXDUMP_annotated.txt` annotates every byte of a real capture.
