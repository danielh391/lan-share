"""Cross-platform socket error handling with clear firewall guidance."""
from __future__ import annotations

import asyncio
import socket
import sys
from asyncio import StreamReader, StreamWriter


def _firewall_message(port: int, proto: str = "TCP") -> str:
    if sys.platform == "win32":  # type: ignore[comparison-overlap]
        return (
            f"Cannot bind to {proto} port {port}.\n"
            "Allow this port through Windows Firewall or run as Administrator."
        )
    return f"Cannot bind to {proto} port {port}: address already in use or permission denied."


async def safe_start_server(handler, host: str, port: int, **kw) -> asyncio.Server:
    try:
        return await asyncio.start_server(handler, host, port, **kw)
    except PermissionError as e:
        sys.exit(f"Cannot bind to TCP port {port}: {e}\n{_firewall_message(port)}")
    except OSError as e:
        if e.errno in (10013, 98):  # WSAEACCES / EADDRINUSE
            sys.exit(_firewall_message(port))
        raise


async def safe_open_connection(host: str, port: int, **kw) -> tuple[StreamReader, StreamWriter]:
    try:
        return await asyncio.open_connection(host, port, **kw)
    except PermissionError as e:
        sys.exit(f"Cannot connect to {host}:{port}: {e}")
    except OSError as e:
        if e.errno == 10013:  # WSAEACCES
            sys.exit(f"Connection to {host}:{port} blocked by Windows Firewall.")
        raise


def safe_udp_bind(sock: socket.socket, address: tuple[str, int]) -> None:
    port = address[1]
    try:
        sock.bind(address)
    except PermissionError as e:
        sys.exit(f"Cannot bind to UDP port {port}: {e}\n{_firewall_message(port, 'UDP')}")
    except OSError as e:
        if e.errno in (10013, 98):
            sys.exit(_firewall_message(port, "UDP"))
        raise
