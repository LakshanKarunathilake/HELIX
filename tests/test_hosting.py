import socket

import pytest

from eltool.hosting import (
    UnsafeURLError, check_neo4j_uri, check_outbound_url, cors_origins,
)


def _resolve_to(monkeypatch, *addrs):
    def fake(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ad, port or 0)) for ad in addrs]
    monkeypatch.setattr("eltool.hosting.socket.getaddrinfo", fake)


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("HELIX_HOSTED", "1")
    monkeypatch.delenv("HELIX_URL_ALLOWLIST", raising=False)


def test_local_allows_loopback_http(monkeypatch):
    monkeypatch.delenv("HELIX_HOSTED", raising=False)
    check_outbound_url("http://127.0.0.1:8765/query")


@pytest.mark.parametrize("hosted_flag", [None, "1"])
def test_unknown_scheme_rejected_in_both_modes(monkeypatch, hosted_flag):
    if hosted_flag:
        monkeypatch.setenv("HELIX_HOSTED", hosted_flag)
    else:
        monkeypatch.delenv("HELIX_HOSTED", raising=False)
    _resolve_to(monkeypatch, "93.184.216.34")
    with pytest.raises(UnsafeURLError):
        check_outbound_url("ftp://x/query")


@pytest.mark.parametrize("url", [
    "https://127.0.0.1/query", "https://169.254.169.254/", "https://[::1]/q",
])
def test_hosted_rejects_private_and_metadata(hosted, monkeypatch, url):
    _resolve_to(monkeypatch, "93.184.216.34")  # literals must not be resolved away
    with pytest.raises(UnsafeURLError):
        check_outbound_url(url)


def test_hosted_rejects_if_any_address_private(hosted, monkeypatch):
    _resolve_to(monkeypatch, "93.184.216.34", "10.0.0.5")
    with pytest.raises(UnsafeURLError):
        check_outbound_url("https://kb.example.org/query")


def test_hosted_accepts_public_https(hosted, monkeypatch):
    _resolve_to(monkeypatch, "93.184.216.34")
    check_outbound_url("https://kb.example.org/query")


def test_hosted_requires_https(hosted, monkeypatch):
    _resolve_to(monkeypatch, "93.184.216.34")
    with pytest.raises(UnsafeURLError):
        check_outbound_url("http://kb.example.org/query")


def test_allowlisted_internal_host_passes(hosted, monkeypatch):
    monkeypatch.setenv("HELIX_URL_ALLOWLIST", "KB:8765")
    _resolve_to(monkeypatch, "172.18.0.2")
    check_outbound_url("http://kb:8765/query")
    with pytest.raises(UnsafeURLError):  # same host, other port
        check_outbound_url("http://kb:9999/query")


def test_unresolvable_host_rejected(hosted, monkeypatch):
    def fake(*a, **k):
        raise socket.gaierror("nope")
    monkeypatch.setattr("eltool.hosting.socket.getaddrinfo", fake)
    with pytest.raises(UnsafeURLError):
        check_outbound_url("https://nowhere.example.org/q")


def test_neo4j_schemes(hosted, monkeypatch):
    _resolve_to(monkeypatch, "93.184.216.34")
    check_neo4j_uri("neo4j+s://a.databases.neo4j.io")
    check_neo4j_uri("bolt+ssc://1.2.3.4:7687")
    with pytest.raises(UnsafeURLError):
        check_neo4j_uri("bolt://a.databases.neo4j.io")
    with pytest.raises(UnsafeURLError):
        check_neo4j_uri("neo4j+s://10.0.0.5")
    with pytest.raises(UnsafeURLError):
        check_neo4j_uri("http://a.databases.neo4j.io")


def test_neo4j_local_only_checks_scheme(monkeypatch):
    monkeypatch.delenv("HELIX_HOSTED", raising=False)
    check_neo4j_uri("bolt://127.0.0.1:7687")
    with pytest.raises(UnsafeURLError):
        check_neo4j_uri("http://127.0.0.1:7687")


def test_neo4j_allowlisted_may_be_unencrypted(hosted, monkeypatch):
    monkeypatch.setenv("HELIX_URL_ALLOWLIST", "neo:7687")
    _resolve_to(monkeypatch, "172.18.0.3")
    check_neo4j_uri("bolt://neo")


def test_cors_origins(monkeypatch):
    monkeypatch.delenv("HELIX_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("HELIX_HOSTED", raising=False)
    assert cors_origins() == ["*"]
    monkeypatch.setenv("HELIX_HOSTED", "1")
    assert cors_origins() == []
    monkeypatch.setenv("HELIX_CORS_ORIGINS", "https://a.org, https://b.org")
    assert cors_origins() == ["https://a.org", "https://b.org"]
