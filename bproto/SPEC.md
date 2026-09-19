# BHP/1 — Binary HTTP, v1 — Specification

`bserve` (server) + `bcurl` (client). One TCP connection carries
many request/response pairs, sequentially, in order. All integers are
unsigned big-endian (network order). All strings are UTF-8.

## 1. Connection lifecycle

1. Client opens TCP to `host:port`.
2. Client MUST send 4-byte preface `42 48 50 31` (`"BHP1"`) exactly once.
   Server MUST verify it. On mismatch the server SHOULD send a `400`
   RESPONSE with `reqid=0` (best effort) and close. Rationale: 4 bytes
   per connection fail fast on HTTP/garbage probes instead of waiting
   for megabytes (see §2).
3. Then: a sequence of *frames*. Either side MAY pipeline: send N
   REQUESTs before reading N RESPONSEs. Responses MUST be sent in the
   order requests were received, with the request's ID echoed, so a
   client can match them without reordering logic.
4. Either side MUST keep the connection open across requests.
   Server SHOULD close after 30 s idle (defensible: longer than a
   human `bcurl` run, shorter than a leaked fd; configurable).
   Either side MUST honour a clean EOF (close = end, no framing error).

## 2. Frame header — 8 bytes, fixed size

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                 Payload Length (24)           |    Type (8)   |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|   Flags (8)   |         Request ID (16)       |  Reserved (8) |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

| Field | Width | Meaning |
|---|---|---|
| Length | 24 | bytes after the header, `0 … 16 777 215`. |
| Type | 8 | `0x00` PING, `0x01` REQUEST, `0x02` RESPONSE. All others *unknown*. |
| Flags | 8 | bit 0 `END` (sender MUST set 1 in v1; receiver MUST accept 0/1). Bits 1–7 reserved, send 0, ignore on receipt. |
| Request ID | 16 | client-chosen `1 … 65535`, server MUST echo. `0` reserved for connection-level (PING, preface errors). Client SHOULD increment; wrap is allowed. |
| Reserved | 8 | MUST be 0 on send, MUST be ignored on receipt. |

Framing rule: read 8 bytes, read exactly `Length` bytes, that is one
frame. Byte `8+Length` belongs to the next frame — the binary analogue
of "consume exactly Content-Length, not one more".

**Why these widths (HTTP/2 chose 24 / 8 / 8 / 31 — why do we differ)?**
*Length 24:* identical argument to HTTP/2. 16 MiB covers any file under
`./www` in one frame; 16-bit (64 KiB) would force DATA-splitting and
reassembly for ordinary pages; 32-bit wastes a byte per frame for a
4 GiB frame we will never send and cannot buffer. 24 is the sweet spot.
*Type 8 / Flags 8:* one byte is the smallest addressable unit; 256
types and 8 booleans leave room for a version 2 (we use 3 types and 1
flag). Same as HTTP/2, no reason to innovate.
*Request ID 16, not 31:* HTTP/2 needs 2 billion concurrent streams
because browsers multiplex. We do strictly sequential
request→response (HTTP/1.1 keep-alive semantics, but in binary), so at
most a TCP window of pipelined requests is in flight — 65 535 IDs are
more than enough. The payoff: 24+8+8+16+8 = 64 bits = 8 bytes, a
64-bit-aligned header parseable in C as two `uint32`s. HTTP/2's 9-byte
header is famously unaligned. *Reserved 8:* explicit growth space so v2
can claim flag bits or a wider ID without changing the header size.
A receiver MUST ignore it today — that is how v1 stays wire-compatible.

**Extensibility (MAY NOT skip):** a receiver meeting a frame type it
does not know MUST read exactly `Length` bytes, discard them, and keep
the connection open. Example: a v2 `0x09 COMPRESSED` frame passes
through a v1 server as a skip. A receiver MUST also ignore unknown flag
bits and non-zero Reserved. That single rule is what leaves room for a
version 2. Violation (close/reset on unknown) is a spec bug.

## 3. Payloads

### REQUEST (`0x01`, client → server)

```
u16 path_len | path[path_len] | u8 n_hdrs | hdrs...
```

* `path`: UTF-8, MUST start with `/`, no query semantics in v1
  (query string, if present, is treated as part of the filename and
  will 404 — keep the mapping pure). Max 65535 bytes.
* `hdrs`: header block (§4). Client SHOULD send `host`, `user-agent`,
  `accept`. Server MUST ignore headers it does not need (static files
  need only the path), but MUST still parse them to find the body
  boundary (there is no body in v1 REQUEST — anything after the header
  block is malformed → `400`).

### RESPONSE (`0x02`, server → client)

```
u16 status | u8 n_hdrs | hdrs... | body[Length - (3 + hdrs_len)]
```

* `status`: e.g. `200`, `400`, `404`. Body is the *remainder* of the
  payload — no separate body-length field (frame `Length` is
  authoritative; a `content-length` header SHOULD match but receivers
  MUST use framing, not the header).
* Server mapping: strip query/fragment, percent-decode, lexically
  resolve `.`/`..`, realpath-check against the root. `/` → `/index.html`,
  directory → `directory/index.html`. Missing → `404`. Traversal
  outside root, NUL, bad UTF-8, path not starting with `/`,
  truncated header block → `400`. Content-Type by extension
  (`mimetypes`), default `application/octet-stream`.

### PING (`0x00`, either direction)

Payload `0` or 8 opaque bytes. Receiver MUST reply with identical
payload, `reqid=0`. Keepalive only; never produces a `RESPONSE`.

## 4. Headers — indexed names + length-prefixed literals

HPACK's first two mechanisms, in an evening (deliberately *not* the
other two — no Huffman, no dynamic table: length-prefix keeps every
frame self-describing and hexdump-auditable, with no compression
context to desynchronise).

Static table (code → name), 10 entries — the ten names we actually send:

```
0x01 host  0x02 user-agent  0x03 accept  0x04 accept-encoding
0x05 connection  0x06 cache-control  0x07 content-type
0x08 content-length  0x09 server  0x0A date
```

Encoding: `u8 n_hdrs`, then per header `u8 code | u16 vlen | value`
for indexed, or `0xFF | u16 nlen | name | u16 vlen | value` for
literals. Unknown *code* (not `0xFF`, not 1–10) → malformed frame →
`400`. `n_hdrs` max 255 in v1.

## 5. Limits & errors

Max payload 16 MiB; `bserve` buffers one frame (fine for `./www`).
`400` malformed, `404` not there — and the connection STAYS OPEN in
both cases (only preface failure, idle timeout, or EOF closes it).
`bcurl` writes each body to stdout, `-v` hexdumps every frame both
directions, exits 1 if any status is 4xx/5xx, and MUST use a single
TCP connection for all paths on the command line.

See `HEXDUMP_annotated.txt` — if you cannot annotate your own bytes,
the spec is not finished.
