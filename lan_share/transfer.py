"""Async TCP send/receive logic with resume support."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import sys
import tarfile
import uuid
from asyncio import StreamReader, StreamWriter
from dataclasses import dataclass
from pathlib import Path

from . import firewall, protocol


class TransferError(Exception):
    pass


class ChecksumError(TransferError):
    pass


@dataclass
class Frame:
    type: int
    payload: bytes


# ---------- Low-level frame I/O ----------

async def read_frame(reader: StreamReader) -> Frame:
    try:
        header = await reader.readexactly(protocol.HEADER_SIZE)
    except asyncio.IncompleteReadError as e:
        raise TransferError("Connection closed unexpectedly while reading header") from e
    frame_type, payload_len = protocol.decode_header(header)
    try:
        payload = await reader.readexactly(payload_len)
    except asyncio.IncompleteReadError as e:
        raise TransferError("Connection closed unexpectedly while reading payload") from e
    return Frame(frame_type, payload)


async def write_frame(writer: StreamWriter, frame_type: int, payload: bytes) -> None:
    writer.write(protocol.encode_frame(frame_type, payload))
    await writer.drain()


# ---------- File transfer ----------

def _lshare_path(dest_dir: Path, filename: str) -> Path:
    return dest_dir / (filename + ".lshare")


def _part_path(dest_dir: Path, filename: str) -> Path:
    return dest_dir / (filename + ".part")


def _load_lshare(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_lshare(path: Path, meta: dict) -> None:
    path.write_text(json.dumps(meta))


def _compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(protocol.DEFAULT_CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


async def send_file(
    writer: StreamWriter,
    reader: StreamReader,
    file_path: Path,
    transfer_id: str,
    chunk_size: int = protocol.DEFAULT_CHUNK_SIZE,
) -> None:
    size = file_path.stat().st_size
    sha256 = _compute_sha256(file_path)

    # Send OFFER
    writer.write(protocol.make_offer(transfer_id, file_path.name, size, sha256, False, chunk_size))
    await writer.drain()

    # Read ACCEPT or REJECT
    frame = await read_frame(reader)
    if frame.type == protocol.FRAME_REJECT:
        info = protocol.parse_json_payload(frame.payload)
        raise TransferError(f"Receiver rejected transfer: {info.get('reason', 'unknown')}")
    if frame.type != protocol.FRAME_ACCEPT:
        raise TransferError(f"Expected ACCEPT, got frame type {frame.type:#x}")

    info = protocol.parse_json_payload(frame.payload)
    offset = info.get("resume_offset", 0)

    sent = offset
    h = hashlib.sha256()

    with open(file_path, "rb") as f:
        if offset:
            # Pre-hash already-sent portion for accurate accounting (not needed for send side)
            f.seek(offset)
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            writer.write(protocol.make_data(chunk))
            await writer.drain()
            sent += len(chunk)
            _print_progress(file_path.name, sent, size)

    writer.write(protocol.make_done(transfer_id))
    await writer.drain()
    print(file=sys.stderr)  # newline after \r progress


async def receive_file(
    reader: StreamReader,
    writer: StreamWriter,
    dest_dir: Path,
    auto_accept: bool = False,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)

    frame = await read_frame(reader)
    if frame.type != protocol.FRAME_OFFER:
        raise TransferError(f"Expected OFFER, got frame type {frame.type:#x}")

    offer = protocol.parse_json_payload(frame.payload)
    transfer_id: str = offer["transfer_id"]
    name: str = offer["name"]
    total_size: int = offer["size"]
    sha256: str = offer["sha256"]
    is_dir: bool = offer.get("is_dir", False)
    chunk_size: int = offer.get("chunk_size", protocol.DEFAULT_CHUNK_SIZE)

    if not auto_accept:
        prompt = f"Accept '{name}' ({total_size} bytes) from sender? [y/N] "
        answer = await asyncio.to_thread(input, prompt)
        if answer.strip().lower() not in ("y", "yes"):
            writer.write(protocol.make_reject(transfer_id, "User declined"))
            await writer.drain()
            raise TransferError("Transfer rejected by user")

    # Check for resume
    lshare_file = _lshare_path(dest_dir, name)
    part_file = _part_path(dest_dir, name)
    meta = _load_lshare(lshare_file)

    if meta and meta.get("transfer_id") == transfer_id and part_file.exists():
        offset = meta.get("received_bytes", 0)
        # Truncate .part to last known good offset in case of partial write
        current_size = part_file.stat().st_size
        if current_size > offset:
            with open(part_file, "ab") as f:
                f.truncate(offset)
    else:
        offset = 0
        # Remove any stale .part
        part_file.unlink(missing_ok=True)
        meta = {
            "transfer_id": transfer_id,
            "filename": name,
            "total_size": total_size,
            "sha256": sha256,
            "received_bytes": 0,
            "chunk_size": chunk_size,
        }

    writer.write(protocol.make_accept(transfer_id, offset))
    await writer.drain()

    received = offset
    sha_hasher = hashlib.sha256()

    # If resuming, hash existing .part for integrity check at end
    if offset and part_file.exists():
        with open(part_file, "rb") as f:
            while chunk := f.read(chunk_size):
                sha_hasher.update(chunk)

    mode = "ab" if offset else "wb"
    with open(part_file, mode) as f:
        while True:
            frame = await read_frame(reader)
            if frame.type == protocol.FRAME_DONE:
                break
            if frame.type == protocol.FRAME_ERROR:
                err = protocol.parse_json_payload(frame.payload)
                raise TransferError(f"Sender error: {err.get('message', 'unknown')}")
            if frame.type != protocol.FRAME_DATA:
                raise TransferError(f"Expected DATA, got frame type {frame.type:#x}")

            chunk = frame.payload
            sha_hasher.update(chunk)
            f.write(chunk)
            received += len(chunk)

            meta["received_bytes"] = received
            _save_lshare(lshare_file, meta)

            _print_progress(name, received, total_size)

    print(file=sys.stderr)  # newline after \r progress

    # Verify checksum
    actual = sha_hasher.hexdigest()
    if actual != sha256:
        raise ChecksumError(f"Checksum mismatch for '{name}': expected {sha256}, got {actual}")

    if is_dir:
        # Extract tar archive.
        # Use an explicit `with open()` so the file handle is guaranteed closed
        # before unlink() — anonymous open() inside tarfile.open(fileobj=...)
        # is treated as external (TarFile._extfileobj=True) and NOT closed by
        # tarfile itself, causing WinError 32 on Windows when GC is delayed.
        out_dir = dest_dir / name
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(part_file, "rb") as fobj, tarfile.open(fileobj=fobj, mode="r:") as tar:
            tar.extractall(out_dir)
        part_file.unlink()
        lshare_file.unlink(missing_ok=True)
        print(f"Saved directory: {out_dir}", file=sys.stderr)
        return out_dir
    else:
        final = dest_dir / name
        # os.replace is atomic on Windows and handles the case where dest exists
        os.replace(part_file, final)
        lshare_file.unlink(missing_ok=True)
        print(f"Saved: {final}", file=sys.stderr)
        return final


# ---------- Directory transfer ----------

async def send_directory(
    writer: StreamWriter,
    reader: StreamReader,
    dir_path: Path,
    chunk_size: int = protocol.DEFAULT_CHUNK_SIZE,
) -> None:
    transfer_id = str(uuid.uuid4())

    # Pack directory into in-memory tar
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tar:
        tar.add(dir_path, arcname=dir_path.name)
    buf.seek(0)
    data = buf.read()

    size = len(data)
    sha256 = hashlib.sha256(data).hexdigest()
    name = dir_path.name

    # Send OFFER with is_dir=True
    writer.write(
        protocol.make_offer(transfer_id, name, size, sha256, True, chunk_size)
    )
    await writer.drain()

    frame = await read_frame(reader)
    if frame.type == protocol.FRAME_REJECT:
        info = protocol.parse_json_payload(frame.payload)
        raise TransferError(f"Receiver rejected directory transfer: {info.get('reason', 'unknown')}")
    if frame.type != protocol.FRAME_ACCEPT:
        raise TransferError(f"Expected ACCEPT, got frame type {frame.type:#x}")

    offset = protocol.parse_json_payload(frame.payload).get("resume_offset", 0)
    sent = offset

    buf.seek(offset)
    while True:
        chunk = buf.read(chunk_size)
        if not chunk:
            break
        writer.write(protocol.make_data(chunk))
        await writer.drain()
        sent += len(chunk)
        _print_progress(name, sent, size)

    writer.write(protocol.make_done(transfer_id))
    await writer.drain()
    print(file=sys.stderr)


# ---------- High-level run functions ----------

async def run_sender(
    receiver_host: str,
    tcp_port: int,
    file_path: Path,
    chunk_size: int = protocol.DEFAULT_CHUNK_SIZE,
) -> None:
    reader, writer = await firewall.safe_open_connection(receiver_host, tcp_port)
    try:
        if file_path.is_dir():
            await send_directory(writer, reader, file_path, chunk_size)
        else:
            transfer_id = str(uuid.uuid4())
            await send_file(writer, reader, file_path, transfer_id, chunk_size)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def run_receiver(
    tcp_port: int,
    dest_dir: Path,
    udp_port: int,
    auto_accept: bool = False,
) -> None:
    async def handler(reader: StreamReader, writer: StreamWriter) -> None:
        addr = writer.get_extra_info("peername")
        print(f"Connection from {addr}", file=sys.stderr)
        try:
            await receive_file(reader, writer, dest_dir, auto_accept)
        except TransferError as e:
            print(f"Transfer error: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Unexpected error: {e}", file=sys.stderr)
        finally:
            writer.close()

    server = await firewall.safe_start_server(handler, "0.0.0.0", tcp_port)
    print(f"Listening on TCP port {tcp_port}…", file=sys.stderr)
    async with server:
        await server.serve_forever()


# ---------- Helpers ----------

def _print_progress(name: str, received: int, total: int) -> None:
    if total:
        pct = received * 100 // total
        bar_len = 30
        filled = bar_len * received // total
        bar = "#" * filled + "-" * (bar_len - filled)
        print(
            f"\r  {name}: [{bar}] {pct:3d}%  {_fmt_bytes(received)}/{_fmt_bytes(total)}",
            end="",
            file=sys.stderr,
            flush=True,
        )


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n //= 1024
    return f"{n:.1f}TB"
