# Network Architecture — Assignment Submission

Raw sockets, no frameworks (Python standard library only).

## Contents

| Brief item | Location |
|---|---|
| Calculator server (early HTTP/1.1, due before session 7) | `calculator/server.py` |
| Self-test for the calculator marking procedure | `calculator/test_mark.py` |
| Binary-protocol spec (course project, two tracks) | `bproto/SPEC.md` |
| Track 1 — server | `./bserve` (wraps `bproto/bserve`) |
| Track 2 — client | `./bcurl` (wraps `bproto/bcurl`) |
| Sample document root | `./www/` (`index.html`, `hello.txt`) |
| Annotated hexdump, one request + one response | `bproto/HEXDUMP_annotated.txt` |
| Reproducible run logs | `proofs/`, summarised in `PROOFS.md` |

## 1. Calculator (early HTTP/1.1)

```bash
python3 calculator/server.py 8080
```

Behaviour:

| Request | Response |
|---|---|
| `GET /add?a=2&b=3` | `200` `5` |
| `GET /sub?a=10&b=4` | `200` `6` |
| `GET /mul?a=6&b=7` | `200` `42` |
| `GET /div?a=9&b=3` | `200` `3` |
| `GET /div?a=1&b=0`, non-integer or missing `a`/`b` | `400` |
| Unknown path (e.g. `/pow`) | `404` |
| Non-GET method | `405` with `Allow: GET` |
| HTTP/1.1 request without `Host` | `400` |

Implementation notes. One socket serves many requests: headers are read
up to `\r\n\r\n`, then exactly `Content-Length` bytes are consumed
(`Transfer-Encoding: chunked` is decoded the same way), leaving byte
`n+1` buffered for the next — pipelined — request. Every response
carries `Content-Length`; `Connection: close` is honoured (HTTP/1.0
defaults to close), idle timeout is 10 s, and error statuses never close
the connection, so one TCP handshake carries the full marking sequence
plus pipelined requests answered in order.

Check:

```bash
python3 calculator/test_mark.py localhost 8080
sh demo.sh   # end-to-end run for both assignments
```

## 2. Binary HTTP (course project)

```bash
./bserve ./www 9000
./bcurl -v localhost:9000/index.html
./bcurl localhost:9000/hello.txt localhost:9000/index.html
```

Protocol `BHP/1` is defined in `bproto/SPEC.md`. Summary: one TCP
connection carries sequential request/response frames. Each frame has an
8-byte header — 24-bit payload length, 8-bit type (`0x00` PING,
`0x01` REQUEST, `0x02` RESPONSE), 8-bit flags, 16-bit request ID echoed
by the server, 8-bit reserved — followed by the payload. Header names
use a 10-entry static table with length-prefixed literals. A receiver
that meets an unknown frame type skips exactly `Length` bytes and keeps
the connection open, leaving room for a version 2. `bcurl` writes bodies
to stdout, hexdumps frames with `-v`, exits non-zero on 4xx/5xx, and
never opens a second connection.

## Verification

`proofs/` holds captured runs (`calculator_mark.txt`,
`calculator_pipelining.txt`, `bcurl_verbose.txt`, `bproto_edge.txt`,
`demo_output.txt`); `PROOFS.md` quotes them. `bproto/bhp.py` is shared
framing code used by both ends.
