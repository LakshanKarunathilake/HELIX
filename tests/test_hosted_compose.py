"""Static checks on the hosted server's compose file; no Docker needed."""
import re
from pathlib import Path

import pytest
import yaml

from eltool.hosting import check_neo4j_uri, check_outbound_url

HOSTED = Path(__file__).resolve().parent.parent / "deploy" / "hosted"
NOTICE = ("Demo data: Medical Subject Headings (MeSH) 2026. "
          "Courtesy of the U.S. National Library of Medicine. "
          "It may not reflect the most current data available from NLM. "
          "NLM does not endorse this service.")


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load((HOSTED / "docker-compose.yml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def services(compose):
    return compose["services"]


def default_of(value, name):
    """The fallback in ``${name:-fallback}``, which must be the whole value."""
    match = re.fullmatch(rf"\$\{{{name}:-([^}}]*)\}}", value)
    assert match, value
    return match.group(1)


def test_the_five_services(services):
    assert set(services) == {"api", "web", "caddy", "kb", "neo4j"}


def test_only_caddy_publishes_ports(services):
    assert [name for name, service in services.items() if "ports" in service] == ["caddy"]
    assert services["caddy"]["ports"] == ["80:80", "443:443"]
    assert not [name for name, service in services.items() if "network_mode" in service]


def test_accounts_stay_invite_only(services):
    environment = services["api"]["environment"]
    assert (environment["HELIX_HOSTED"], environment["HELIX_AUTH"]) == ("1", "1")


def test_api_allows_the_two_internal_servers(services):
    allowlist = services["api"]["environment"]["HELIX_URL_ALLOWLIST"]
    assert allowlist == "kb:8765,neo4j:7687,${HELIX_URL_ALLOWLIST:-}"


def test_the_demo_session_addresses_pass_the_hosted_guard(services, monkeypatch):
    allowlist = services["api"]["environment"]["HELIX_URL_ALLOWLIST"]
    monkeypatch.setenv("HELIX_HOSTED", "1")
    monkeypatch.setenv("HELIX_URL_ALLOWLIST",
                       allowlist.replace("${HELIX_URL_ALLOWLIST:-}", ""))
    check_outbound_url("http://kb:8765/query")      # the seed script's defaults
    check_neo4j_uri("bolt://neo4j:7687")


def test_search_server_reads_the_mounted_demo_index(services):
    kb = services["kb"]
    assert kb["build"] == {"context": "../..", "dockerfile": "docker/Dockerfile.kb"}
    assert kb["environment"]["KB_INDEX_PATH"] == "/data/index/mesh-diseases"
    assert kb["volumes"] == ["${DEMO_INDEX_DIR:-./demo-index}:/data/index:ro",
                             "hf_cache:/root/.cache/huggingface"]
    assert kb["restart"] == "unless-stopped"


def test_graph_is_a_pinned_community_neo4j(services):
    image = services["neo4j"]["image"]
    assert re.fullmatch(r"neo4j:5\.\d+\.\d+-community@sha256:[0-9a-f]{64}", image), image


def test_graph_password_is_required_and_data_is_kept(services):
    neo4j = services["neo4j"]
    auth = neo4j["environment"]["NEO4J_AUTH"]
    assert auth.startswith("neo4j/${NEO4J_PASSWORD:?"), auth
    assert neo4j["environment"]["NEO4J_server_memory_heap_max__size"] == "1G"
    assert neo4j["volumes"] == ["neo4j_data:/data"]
    assert neo4j["restart"] == "unless-stopped"


def test_graph_sends_no_usage_reports(services):
    environment = services["neo4j"]["environment"]
    assert environment["NEO4J_dbms_usage__report_enabled"] == "false"


def test_graph_page_cache_is_capped(services):
    assert services["neo4j"]["environment"]["NEO4J_server_memory_pagecache_size"] == "256M"


def test_search_server_sends_no_hub_telemetry(services):
    assert services["kb"]["environment"]["HF_HUB_DISABLE_TELEMETRY"] == "1"


def test_front_door_image_is_pinned_by_digest(services):
    assert services["caddy"]["image"] == (
        "caddy:2.8.4@sha256:226d1f059b75399fe19182893c7184591c07b97afc8dfcf44eeb80c9a77a530f")


def test_api_gets_the_graph_password_for_the_builders(services):
    password = services["api"]["environment"]["NEO4J_PASSWORD"]
    assert password.startswith("${NEO4J_PASSWORD:?"), password


def test_named_volumes_are_declared(compose):
    assert set(compose["volumes"]) == {"sessions", "caddy_data", "neo4j_data", "hf_cache"}


def test_default_notice_carries_the_nlm_attribution(services):
    notice = default_of(services["api"]["environment"]["HELIX_NOTICE"], "HELIX_NOTICE")
    for wanted in ("Courtesy of the U.S. National Library of Medicine", "2026",
                   "most current", "does not endorse"):
        assert wanted in notice
    assert notice == NOTICE


@pytest.mark.parametrize("line", ["NEO4J_PASSWORD=", "DEMO_INDEX_DIR="])
def test_env_example_lists_the_new_settings_empty(line):
    assert line in (HOSTED / ".env.example").read_text(encoding="utf-8").splitlines()
