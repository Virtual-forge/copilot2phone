"""Network helpers for finding the address a phone should use.

Kept separate from the CLI so scripts and tests can reuse the same answer.
"""

from __future__ import annotations

import socket

# TEST-NET-1 (RFC 5737). Never routed, so connecting a UDP socket to it sends
# no packet -- it only makes the OS pick the outbound interface for us.
_PROBE_ADDRESS = ("192.0.2.1", 1)


def lan_ip() -> str | None:
    """Best-effort LAN IPv4 address for this machine, or ``None``.

    This is the address a phone on the same Wi-Fi should use. It deliberately
    ignores virtual adapters (WSL, Hyper-V, VPN) because the OS routes the
    probe through the default interface.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(_PROBE_ADDRESS)
        return str(sock.getsockname()[0])
    except OSError:
        return None
    finally:
        sock.close()


def phone_url(port: int, token: str) -> str | None:
    """The URL to open on the phone, with the token in the fragment.

    The fragment is never sent to the server and the page strips it from the
    address bar on load, so the token does not end up in any access log.
    """
    ip = lan_ip()
    if not ip:
        return None
    return f"http://{ip}:{port}/#t={token}"
