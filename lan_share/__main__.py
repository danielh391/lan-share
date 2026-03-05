"""CLI entry point for lan-share."""
from __future__ import annotations

import sys

if sys.version_info < (3, 11):
    sys.exit(
        f"lan-share requires Python 3.11+  (found {sys.version.split()[0]})\n"
        "Install Python 3.11 or newer and try again."
    )

import argparse
import asyncio
from pathlib import Path

from . import discovery, protocol, transfer


def get_local_ip() -> str:
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "0.0.0.0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lshare",
        description="LAN file sharing tool",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ---- send ----
    p_send = sub.add_parser("send", help="Send a file or directory to a receiver")
    p_send.add_argument("path", type=Path, help="File or directory to send")
    p_send.add_argument(
        "--to", metavar="IP", default=None, help="Receiver IP (auto-discover if omitted)"
    )
    p_send.add_argument(
        "--tcp-port", type=int, default=protocol.DEFAULT_TCP_PORT, metavar="N"
    )
    p_send.add_argument(
        "--udp-port", type=int, default=protocol.DEFAULT_UDP_PORT, metavar="N"
    )
    p_send.add_argument(
        "--chunk-size", type=int, default=protocol.DEFAULT_CHUNK_SIZE, metavar="N"
    )
    p_send.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        metavar="S",
        help="Discovery timeout in seconds (default: 5)",
    )

    # ---- recv ----
    p_recv = sub.add_parser("recv", help="Start receiver service (also broadcasts UDP beacon)")
    p_recv.add_argument(
        "dest", nargs="?", type=Path, default=Path("."), help="Destination directory"
    )
    p_recv.add_argument(
        "--tcp-port", type=int, default=protocol.DEFAULT_TCP_PORT, metavar="N"
    )
    p_recv.add_argument(
        "--udp-port", type=int, default=protocol.DEFAULT_UDP_PORT, metavar="N"
    )
    p_recv.add_argument(
        "--auto-accept", action="store_true", help="Accept all incoming transfers without prompting"
    )

    # ---- find ----
    p_find = sub.add_parser("find", help="Discover receivers on the LAN")
    p_find.add_argument(
        "--udp-port", type=int, default=protocol.DEFAULT_UDP_PORT, metavar="N"
    )
    p_find.add_argument(
        "--timeout", type=float, default=5.0, metavar="S", help="Listen duration in seconds"
    )

    return parser


def _prompt_peer_selection(peers: list[dict]) -> str:
    if not peers:
        print("No receivers found.", file=sys.stderr)
        sys.exit(1)
    if len(peers) == 1:
        peer = peers[0]
        print(
            f"Auto-selecting sole peer: {peer['ip']} ({peer['hostname']}) :{peer['tcp_port']}",
            file=sys.stderr,
        )
        return peer["ip"]
    print("\nAvailable receivers:", file=sys.stderr)
    for i, p in enumerate(peers):
        print(f"  [{i + 1}] {p['ip']}  {p['hostname']}  :{p['tcp_port']}", file=sys.stderr)
    while True:
        try:
            choice = int(input("Select receiver [1]: ") or "1") - 1
            if 0 <= choice < len(peers):
                return peers[choice]["ip"]
        except (ValueError, KeyboardInterrupt):
            pass
        print("Invalid choice, try again.", file=sys.stderr)


async def cmd_send(args: argparse.Namespace) -> None:
    path: Path = args.path
    if not path.exists():
        print(f"Error: '{path}' does not exist.", file=sys.stderr)
        sys.exit(1)

    if args.to:
        receiver_ip = args.to
    else:
        peers = await discovery.listen_for_peers(args.udp_port, timeout=args.timeout)
        receiver_ip = _prompt_peer_selection(peers)

    print(f"Sending '{path}' → {receiver_ip}:{args.tcp_port}", file=sys.stderr)
    await transfer.run_sender(receiver_ip, args.tcp_port, path, args.chunk_size)


async def cmd_recv(args: argparse.Namespace) -> None:
    dest: Path = args.dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)

    local_ip = get_local_ip()
    print(
        f"Receiver ready  IP={local_ip}  TCP={args.tcp_port}  UDP={args.udp_port}",
        file=sys.stderr,
    )
    print(f"Saving to: {dest}", file=sys.stderr)

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(
                transfer.run_receiver(args.tcp_port, dest, args.udp_port, args.auto_accept),
                name="tcp-server",
            )
            tg.create_task(
                discovery.broadcast_hello(args.tcp_port, args.udp_port),
                name="udp-beacon",
            )
    except* KeyboardInterrupt:
        pass
    except* transfer.TransferError as eg:
        for e in eg.exceptions:
            print(f"Transfer error: {e}", file=sys.stderr)
        sys.exit(1)


async def cmd_find(args: argparse.Namespace) -> None:
    peers = await discovery.listen_for_peers(args.udp_port, timeout=args.timeout)
    if not peers:
        print("No receivers found.")
    else:
        print(f"Found {len(peers)} receiver(s):")
        for p in peers:
            print(f"  {p['ip']:15s}  {p['hostname']}  TCP:{p['tcp_port']}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        match args.command:
            case "send":
                asyncio.run(cmd_send(args))
            case "recv":
                asyncio.run(cmd_recv(args))
            case "find":
                asyncio.run(cmd_find(args))
            case _:
                parser.print_help()
                sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
