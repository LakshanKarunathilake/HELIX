"""Hosted-mode switch and outbound URL guard.

Everything reads the environment on each call so tests (and operators) can
change it without re-importing.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit

_HTTP_SCHEMES = {"http", "https"}
_NEO4J_ALL = {"bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"}
_NEO4J_ENCRYPTED = {"bolt+s", "bolt+ssc", "neo4j+s", "neo4j+ssc"}
_DEFAULT_PORTS = {"https": 443, "http": 80, **{s: 7687 for s in _NEO4J_ALL}}


class UnsafeURLError(ValueError):
    """An outbound URL that this deployment refuses to contact."""


def hosted_mode() -> bool:
    return os.environ.get("HELIX_HOSTED", "") == "1"


def _csv_env(name: str) -> list[str]:
    return [p.strip() for p in os.environ.get(name, "").split(",") if p.strip()]


def url_allowlist() -> set[str]:
    return {e.lower() for e in _csv_env("HELIX_URL_ALLOWLIST")}


def cors_origins() -> list[str]:
    origins = _csv_env("HELIX_CORS_ORIGINS")
    if origins:
        return origins
    return [] if hosted_mode() else ["*"]


def _parse(url: str, allowed_schemes: set[str]):
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError as e:
        raise UnsafeURLError(f"Invalid URL: {e}") from e
    scheme = parts.scheme.lower()
    if scheme not in allowed_schemes:
        raise UnsafeURLError(f"URL scheme '{scheme}' is not allowed")
    if not host:
        raise UnsafeURLError("URL has no host")
    return scheme, host, port or _DEFAULT_PORTS[scheme]


def _require_public(host: str) -> None:
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError as e:
            raise UnsafeURLError(f"Cannot resolve host '{host}'") from e
        try:
            addrs = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
        except ValueError as e:
            raise UnsafeURLError(f"Host '{host}' resolved to an invalid address") from e
    if not addrs:
        raise UnsafeURLError(f"Host '{host}' did not resolve")
    for a in addrs:
        if (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved
                or a.is_multicast or a.is_unspecified):
            raise UnsafeURLError(f"Host '{host}' resolves to a non-public address")


def _check(url: str, schemes: set[str], hosted_schemes: set[str]) -> None:
    scheme, host, port = _parse(url, schemes)
    if not hosted_mode():
        return
    if f"{host}:{port}" in url_allowlist():
        return
    if scheme not in hosted_schemes:
        raise UnsafeURLError(f"URL scheme '{scheme}' is not allowed on a hosted server")
    _require_public(host)


def check_outbound_url(url: str) -> None:
    """Validate a KB URL (http/https)."""
    _check(url, _HTTP_SCHEMES, {"https"})


def check_neo4j_uri(uri: str) -> None:
    """Validate a Neo4j connection URI."""
    _check(uri, _NEO4J_ALL, _NEO4J_ENCRYPTED)
