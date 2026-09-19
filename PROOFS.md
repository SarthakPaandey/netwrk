# Proofs — terminal captures, no video needed

All runs below are reproducible with `./demo.sh`. Raw logs live in `proofs/`.

## 1. Calculator — marking script (one socket, every request)

`python3 calculator/test_mark.py` → `proofs/calculator_mark.txt`:

```
connected localhost:18100, 1 TCP handshake
  OK  GET /add?a=2&b=3       -> 200 '5' (want 200 '5')
  OK  GET /sub?a=10&b=4      -> 200 '6' (want 200 '6')
  OK  GET /mul?a=6&b=7       -> 200 '42' (want 200 '42')
  OK  GET /div?a=1&b=0       -> 400 'division by zero' (want 400 None)
  OK  GET /pow?a=2&b=8       -> 404 'unknown path' (want 404 None)
  OK  POST /add              -> 405 'use GET' (want 405 None)
  socket still open: True (probe -> 200 '2')
1 TCP handshake, 6 responses
  no-Host probe -> 400 (want 400) OK
```

## 2. Calculator — pipelining + framing (`proofs/calculator_pipelining.txt`)

```
pipelining: all six sent at once on ONE socket
  resp[0] -> 200 '5'
  resp[1] -> 200 '6'
  resp[2] -> 200 '42'
  resp[3] -> 200 '3'
  resp[4] -> 404 'unknown path'
  resp[5] -> 405 'use GET'
POST with Content-Length:5 body=hello -> 405 (framing keeps next request intact)
next request after POST body -> 200 '15' (byte n+1 belonged to somebody else: OK)
PIPELINING + FRAMING: PASS
```

## 3. BHP/1 — single + multi + 404

```bash
python3 bproto/bcurl -v localhost:19100/index.html   # exit 0, 45B body → proofs/bcurl_body.bin
python3 bproto/bcurl localhost:19100/hello.txt localhost:19100/index.html  # exit 0, ONE connection
python3 bproto/bcurl localhost:19100/nope.html       # exit 1 (4xx → non-zero)
```

`-v` hexdump of every frame → `proofs/bcurl_verbose.txt`.
Annotated byte-by-byte walkthrough → `bproto/HEXDUMP_annotated.txt`.

## 4. BHP/1 — edge cases (`proofs/bproto_edge.txt`)

```
pipelining: 3 REQUESTs sent back-to-back on ONE connection
  resp id=1 status=200 body=b'hello file\n'
  resp id=2 status=200 body=b'<html><body><h1>hello bhp'
  resp id=3 status=404 body=b'not found'
sent unknown frame type 0x09 (future v2 frame)
  after unknown -> id=9 status=200 (MUST skip cleanly, keep open: PASS)
  PING reply type=0x0 (want 0x0: PASS)
  traversal '/../etc/passwd' -> 400 (PASS)
  missing leading / 'nope' -> 400 (PASS)
BHP EDGE: ALL PASS, connection stayed open throughout
```
