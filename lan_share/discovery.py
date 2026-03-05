"""UDP broadcast-based LAN peer discovery."""
from __future__ import annotations

import asyncio
import json
import socket
import sys
from asyncio import DatagramProtocol

from . import protocol
from .firewall import safe_udp_bind


def make_broadcast_socket(udp_port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    if sys.platform == "win32":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    safe_udp_bind(sock, ("", udp_port))
    return sock


async def broadcast_hello(
    tcp_port: int,
    udp_port: int = protocol.DEFAULT_UDP_PORT,
    interval: float = 2.0,
    count: int | None = None,
) -> None:
    """Broadcast a HELLO beacon. Runs until cancelled (or count reached)."""
    import socket as _socket

    hostname = _socket.gethostname()

    # Determine local IP
    try:
        with _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
    except Exception:
        local_ip = "0.0.0.0"

    message = json.dumps(
        {
            "type": "HELLO",
            "role": "receiver",
            "hostname": hostname,
            "ip": local_ip,
            "tcp_port": tcp_port,
            "version": 1,
        }
    ).encode()

    # Keep the socket blocking — use asyncio.to_thread for the sendto call.
    # Windows ProactorEventLoop's sock_sendto does not support broadcast
    # addresses and raises WinError 10022 (WSAEINVAL).
    send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    def _send() -> None:
        send_sock.sendto(message, ("<broadcast>", udp_port))

    n = 0
    try:
        while count is None or n < count:
            await asyncio.to_thread(_send)
            await asyncio.sleep(interval)
            n += 1
    except asyncio.CancelledError:
        pass
    finally:
        send_sock.close()


class _ListenProtocol(DatagramProtocol):
    def __init__(self) -> None:
        self.peers: dict[str, dict] = {}  # keyed by ip
        self._transport: asyncio.BaseTransport | None = None

    def connection_made(self, transport):
        self._transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            msg = json.loads(data.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if msg.get("type") == "HELLO" and msg.get("role") == "receiver":
            ip = msg.get("ip", addr[0])
            self.peers[ip] = {
                "hostname": msg.get("hostname", ""),
                "ip": ip,
                "tcp_port": msg.get("tcp_port", protocol.DEFAULT_TCP_PORT),
            }

    def error_received(self, exc: Exception) -> None:
        pass


async def listen_for_peers(
    udp_port: int = protocol.DEFAULT_UDP_PORT,
    timeout: float = 5.0,
) -> list[dict]:
    """Listen for HELLO beacons and return discovered peers."""
    loop = asyncio.get_running_loop()

    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    safe_udp_bind(listen_sock, ("", udp_port))
    listen_sock.setblocking(False)

    _proto = _ListenProtocol()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _proto, sock=listen_sock
    )
    try:
        print(f"Listening for peers on UDP port {udp_port} ({timeout:.0f}s)…", file=sys.stderr)
        await asyncio.sleep(timeout)
    finally:
        transport.close()

    return list(_proto.peers.values())
