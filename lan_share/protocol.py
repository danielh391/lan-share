"""Wire format constants and frame encode/decode. Pure sync, zero I/O."""
from __future__ import annotations

import json
import struct

HEADER_FORMAT = "!BQ"
HEADER_SIZE: int = struct.calcsize(HEADER_FORMAT)  # 9 bytes

# Frame type constants
FRAME_OFFER: int = 0x01
FRAME_ACCEPT: int = 0x02
FRAME_REJECT: int = 0x03
FRAME_DATA: int = 0x04
FRAME_DONE: int = 0x05
FRAME_ERROR: int = 0x06

# Defaults
DEFAULT_UDP_PORT: int = 51820
DEFAULT_TCP_PORT: int = 51821
DEFAULT_CHUNK_SIZE: int = 65536


def encode_frame(frame_type: int, payload: bytes) -> bytes:
    """Pack a frame: 9-byte header + payload."""
    header = struct.pack(HEADER_FORMAT, frame_type, len(payload))
    return header + payload


def decode_header(data: bytes) -> tuple[int, int]:
    """Return (frame_type, payload_len) from a 9-byte header."""
    if len(data) < HEADER_SIZE:
        raise ValueError(f"Header too short: {len(data)} < {HEADER_SIZE}")
    frame_type, payload_len = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
    return frame_type, payload_len


def parse_json_payload(payload: bytes) -> dict:
    return json.loads(payload.decode("utf-8"))


# ---------- Frame constructors ----------

def make_offer(
    transfer_id: str,
    name: str,
    size: int,
    sha256: str,
    is_dir: bool = False,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> bytes:
    payload = json.dumps(
        {
            "transfer_id": transfer_id,
            "name": name,
            "size": size,
            "sha256": sha256,
            "is_dir": is_dir,
            "chunk_size": chunk_size,
        }
    ).encode()
    return encode_frame(FRAME_OFFER, payload)


def make_accept(transfer_id: str, resume_offset: int = 0) -> bytes:
    payload = json.dumps(
        {"transfer_id": transfer_id, "resume_offset": resume_offset}
    ).encode()
    return encode_frame(FRAME_ACCEPT, payload)


def make_reject(transfer_id: str, reason: str = "User declined") -> bytes:
    payload = json.dumps({"transfer_id": transfer_id, "reason": reason}).encode()
    return encode_frame(FRAME_REJECT, payload)


def make_data(chunk: bytes) -> bytes:
    return encode_frame(FRAME_DATA, chunk)


def make_done(transfer_id: str) -> bytes:
    payload = json.dumps({"transfer_id": transfer_id}).encode()
    return encode_frame(FRAME_DONE, payload)


def make_error(code: str, message: str) -> bytes:
    payload = json.dumps({"code": code, "message": message}).encode()
    return encode_frame(FRAME_ERROR, payload)
