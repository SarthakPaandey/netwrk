#!/usr/bin/env python3
"""Shared BHP/1 framing. See SPEC.md. stdlib only."""
import struct

MAGIC = b"BHP1"
HEADER_LEN = 8
MAX_PAYLOAD = (1 << 24) - 1  # 16 MiB - 1

T_PING = 0x00
T_REQUEST = 0x01
T_RESPONSE = 0x02

FLAG_END = 0x01

# The ten names you actually send (HPACK mechanism #1: indexed names).
STATIC_TABLE = [
    "host",            # 0x01
    "user-agent",      # 0x02
    "accept",          # 0x03
    "accept-encoding", # 0x04
    "connection",      # 0x05
    "cache-control",   # 0x06
    "content-type",    # 0x07
    "content-length",  # 0x08
    "server",          # 0x09
    "date",            # 0x0A
]
NAME_TO_CODE = {n: i + 1 for i, n in enumerate(STATIC_TABLE)}
LITERAL = 0xFF


def encode_header(length, ftype, flags, reqid, reserved=0):
    assert 0 <= length <= MAX_PAYLOAD
    assert 0 <= ftype <= 255 and 0 <= flags <= 255
    assert 0 <= reqid <= 0xFFFF and 0 <= reserved <= 255
    b0 = (length >> 16) & 0xFF
    b1 = (length >> 8) & 0xFF
    b2 = length & 0xFF
    return struct.pack("!BBBBBHB", b0, b1, b2, ftype, flags, reqid, reserved)


def decode_header(buf8):
    assert len(buf8) == 8
    b0, b1, b2, ftype, flags, reqid, reserved = struct.unpack("!BBBBBHB", buf8)
    length = (b0 << 16) | (b1 << 8) | b2
    return length, ftype, flags, reqid, reserved


def encode_headers(hdrs):
    """hdrs: list[(name, value)] -> bytes. Uses indexed names + length-prefixed literals."""
    out = bytearray()
    out.append(len(hdrs) & 0xFF)  # n_headers (max 255 for v1; more -> 400)
    for name, value in hdrs:
        ln = name.lower()
        code = NAME_TO_CODE.get(ln, None)
        if code is not None:
            out.append(code)
        else:
            out.append(LITERAL)
            nb = name.encode("utf-8")
            out += struct.pack("!H", len(nb)) + nb
        vb = value.encode("utf-8")
        out += struct.pack("!H", len(vb)) + vb
    return bytes(out)


def decode_headers(buf, pos=0):
    """Returns ([(name,value)], new_pos). Raises ValueError on truncation/malformation."""
    if pos >= len(buf):
        raise ValueError("missing n_headers")
    n = buf[pos]
    pos += 1
    out = []
    for _ in range(n):
        if pos >= len(buf):
            raise ValueError("truncated header name")
        code = buf[pos]
        pos += 1
        if code == LITERAL:
            if pos + 2 > len(buf):
                raise ValueError("truncated literal name len")
            (nl,) = struct.unpack("!H", buf[pos:pos + 2])
            pos += 2
            if pos + nl > len(buf):
                raise ValueError("truncated literal name")
            name = buf[pos:pos + nl].decode("utf-8")
            pos += nl
        elif 1 <= code <= len(STATIC_TABLE):
            name = STATIC_TABLE[code - 1]
        else:
            raise ValueError(f"unknown header code {code:#x}")
        if pos + 2 > len(buf):
            raise ValueError("truncated value len")
        (vl,) = struct.unpack("!H", buf[pos:pos + 2])
        pos += 2
        if pos + vl > len(buf):
            raise ValueError("truncated value")
        value = buf[pos:pos + vl].decode("utf-8")
        pos += vl
        out.append((name, value))
    return out, pos


def encode_request(path, hdrs):
    pb = path.encode("utf-8")
    if len(pb) > 0xFFFF:
        raise ValueError("path too long")
    out = bytearray(struct.pack("!H", len(pb)) + pb)
    out += encode_headers(hdrs)
    return bytes(out)


def decode_request(payload):
    if len(payload) < 2:
        raise ValueError("request too short")
    (pl,) = struct.unpack("!H", payload[0:2])
    if 2 + pl > len(payload):
        raise ValueError("request path truncated")
    path = payload[2:2 + pl].decode("utf-8")
    hdrs, pos = decode_headers(payload, 2 + pl)
    if pos != len(payload):
        raise ValueError("trailing bytes in request")
    return path, hdrs


def encode_response(status, hdrs, body: bytes):
    out = bytearray(struct.pack("!H", status))
    out += encode_headers(hdrs)
    out += body
    return bytes(out)


def decode_response(payload):
    if len(payload) < 3:  # status(2) + n_headers(1)
        raise ValueError("response too short")
    (status,) = struct.unpack("!H", payload[0:2])
    hdrs, pos = decode_headers(payload, 2)
    body = payload[pos:]
    return status, hdrs, body


def hexdump(data: bytes, prefix="") -> str:
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{prefix}{i:04x}: {hexs:<48}  |{asc}|")
    return "\n".join(lines)
