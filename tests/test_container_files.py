"""Static checks on the container build files; no Docker needed."""
import re
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent
LOCKS = {"requirements-api.lock": "requirements-api.txt",
         "requirements-kb.lock": "requirements-kb.txt"}
CPU_INDEX = "https://download.pytorch.org/whl/cpu"
PIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*==[0-9][A-Za-z0-9.+!_-]*")


def lines(name):
    """Non-blank, non-comment lines, with shell continuations joined."""
    text = (ROOT / name).read_text(encoding="utf-8").replace("\\\n", " ")
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")]


def pins(name):
    return {canonicalize_name(pkg): version
            for pkg, version in (line.split("==") for line in lines(name))}


@pytest.mark.parametrize("dockerfile", ["api", "kb", "web"])
def test_base_images_are_pinned_by_digest(dockerfile):
    froms = [l for l in lines(f"docker/Dockerfile.{dockerfile}") if l.startswith("FROM ")]
    assert froms
    for line in froms:
        assert re.search(r"@sha256:[0-9a-f]{64}( |$)", line), line


@pytest.mark.parametrize("dockerfile", ["api", "kb"])
def test_images_contain_the_scripts(dockerfile):
    assert "COPY scripts/ ./scripts/" in lines(f"docker/Dockerfile.{dockerfile}")


def test_api_image_installs_the_lock():
    text = "\n".join(lines("docker/Dockerfile.api"))
    assert "COPY requirements-api.lock ." in text
    assert "pip install --no-cache-dir -r requirements-api.lock" in text


def test_kb_image_installs_the_locked_cpu_torch_then_the_lock():
    runs = [l for l in lines("docker/Dockerfile.kb") if l.startswith("RUN pip ")]
    assert len(runs) == 1
    torch_step, lock_step = runs[0].split("&&")
    assert f"--index-url {CPU_INDEX}" in torch_step
    assert "grep '^torch==' requirements-kb.lock" in torch_step
    assert "-r requirements-kb.lock" in lock_step
    assert "COPY requirements-kb.lock ." in lines("docker/Dockerfile.kb")


def test_kb_image_gets_the_neutral_config():
    steps = lines("docker/Dockerfile.kb")
    neutral = "COPY kb/config.container.yaml ./kb/config.yaml"
    assert steps.index("COPY kb/ ./kb/") < steps.index(neutral)


def test_container_config_is_neutral():
    text = (ROOT / "kb/config.container.yaml").read_text(encoding="utf-8")
    assert "/Users/" not in text
    assert yaml.safe_load(text) == {
        "server": {"host": "0.0.0.0", "port": 8765},
        "index": {"path": "/data/index/mesh-diseases"},
        "adapter": {"path": None, "name": None},
        "lexical": {"enabled": False},
    }


@pytest.mark.parametrize("pattern", [
    "kb/config.yaml", "**/.env", "deploy/hosted/demo-index", "deploy/hosted/mesh"])
def test_local_files_stay_out_of_the_build_context(pattern):
    assert pattern in lines(".dockerignore")


@pytest.mark.parametrize("lock", LOCKS)
def test_lock_lines_are_exact_pins(lock):
    assert lines(lock)
    for line in lines(lock):
        assert PIN.fullmatch(line), line


@pytest.mark.parametrize("lock", LOCKS)
def test_lock_has_no_gpu_builds(lock):
    for line in lines(lock):
        assert "+cu" not in line, line
        assert not line.lower().startswith("nvidia"), line


@pytest.mark.parametrize("lock, requirements", LOCKS.items())
def test_lock_satisfies_its_requirements(lock, requirements):
    locked = pins(lock)
    for line in lines(requirements):
        wanted = Requirement(line)
        name = canonicalize_name(wanted.name)
        assert name in locked, f"{name} missing from {lock}"
        assert wanted.specifier.contains(locked[name]), f"{line} vs {locked[name]}"


def major(lock, name):
    return int(pins(lock)[name].split(".")[0])


def test_tested_major_versions_are_locked():
    # The searcher calls tokenizer.batch_encode_plus, removed in transformers 5.
    assert major("requirements-kb.lock", "transformers") == 4
    assert major("requirements-api.lock", "neo4j") == 6
    # anthropic 2 rejects the temperature argument the re-ranker passes.
    assert major("requirements-api.lock", "anthropic") == 0
    # openai 3 no longer installs httpx, which the re-ranker imports.
    assert major("requirements-api.lock", "openai") < 3
    assert "httpx" in pins("requirements-api.lock")
    # Later releases hide an included router's routes from app.routes, which the
    # tests that every write route is guarded walk.
    assert int(pins("requirements-api.lock")["fastapi"].split(".")[1]) <= 136
