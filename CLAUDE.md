# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A LAN file sharing tool with a CLI interface. Transfers files between devices on a local area network efficiently using async I/O.

## Language & Runtime

- Python 3.11+ required. Use features like `match`/`case`, `tomllib`, `TaskGroup`, `ExceptionGroup` where appropriate.
- Do not add a GUI layer until the CLI is fully functional.

## Network Layer

- All network I/O must use `asyncio` (`asyncio.StreamReader`/`StreamWriter`, `asyncio.start_server`, etc.).
- Use `asyncio.TaskGroup` for concurrent transfers.
- Keep protocol logic separate from transport logic.

## Windows Firewall Handling

When binding or connecting on Windows, catch `PermissionError` and `OSError` (errno 10013 / `WSAEACCES`) around socket operations and emit a clear message instructing the user to allow the port through Windows Firewall or run as administrator. Example pattern:

```python
try:
    server = await asyncio.start_server(handler, host, port)
except PermissionError as e:
    sys.exit(f"Cannot bind to port {port}: {e}\nOn Windows, allow this port in Windows Firewall or run as administrator.")
except OSError as e:
    if e.errno == 10013:  # WSAEACCES
        sys.exit(f"Port {port} blocked by Windows Firewall. Allow it or run as administrator.")
    raise
```

## CLI

- Use `argparse` from the standard library (no Click, Typer, or similar).
- Entry point: `python -m lan_share` (package with `__main__.py`).
- Subcommands: `send`, `receive`, `discover`.

## Dependencies

Prefer the standard library. If a third-party package is genuinely needed, add it to `pyproject.toml` under `[project.dependencies]`.

## Project Layout (target)

```
lan_share/
    __main__.py       # CLI entry point, argparse setup
    protocol.py       # wire format / framing
    transfer.py       # async send/receive logic
    discovery.py      # LAN peer discovery (mDNS or UDP broadcast)
    firewall.py       # Windows firewall helpers
pyproject.toml
```
