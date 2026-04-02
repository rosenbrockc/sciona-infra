from __future__ import annotations

import json
import os

# Prevent FAISS/OpenMP segfaults when other tests leave thread-pool workers alive
# (e.g. langgraph's concurrent.futures threads in test_e2e_principal_hodges).
os.environ.setdefault("OMP_NUM_THREADS", "1")

# Force file-only telemetry backend during tests to avoid 30s pool retry
# timeouts when Postgres is configured in .env but not running.
os.environ.setdefault("SCIONA_TELEMETRY_BACKEND", "file")
import shlex
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator

import pytest

from tests.helpers.match_regression import (
    MatchCase,
    build_ageo_atoms_declarations,
    load_match_cases,
)


def _run_subprocess(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: float = 300.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def _ensure_current_event_loop() -> None:
    """Restore pre-3.11 asyncio behavior for legacy sync fixtures."""
    import asyncio

    policy = asyncio.get_event_loop_policy()
    try:
        loop = policy.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("current event loop is closed")
    except RuntimeError:
        policy.set_event_loop(policy.new_event_loop())


def pytest_runtest_setup(item: pytest.Item) -> None:
    _ensure_current_event_loop()


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def _check_llama_ready(base_url: str) -> bool:
    for endpoint in ("/models", "/health"):
        url = f"{base_url}{endpoint}"
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            continue
    return False


def _http_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2.0) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return False


def _wait_for_supabase_ready(
    repo_root: Path,
    *,
    timeout: float = 240.0,
) -> subprocess.CompletedProcess[str] | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = _run_subprocess(
            ["supabase", "status", "-o", "env"],
            cwd=repo_root,
            timeout=60.0,
        )
        if status.returncode == 0:
            parsed = _parse_env_lines(status.stdout)
            api_url = _pick_env_var(parsed, "API_URL", "SUPABASE_URL")
            if api_url and _http_ready(f"{api_url}/rest/v1/"):
                return status
        time.sleep(2.0)
    return None


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return

    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        proc.terminate()

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()
        proc.wait(timeout=5)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _docker_available() -> bool:
    result = subprocess.run(
        ["docker", "ps"],
        text=True,
        capture_output=True,
        timeout=10.0,
    )
    return result.returncode == 0


def _parse_env_lines(output: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key.strip()] = value.strip().strip('"').strip("'")
    return parsed


def _pick_env_var(parsed: dict[str, str], *names: str) -> str:
    for name in names:
        value = parsed.get(name, "")
        if value:
            return value
    return ""


def _supabase_project_id(repo_root: Path) -> str:
    config_path = repo_root / "supabase" / "config.toml"
    if not config_path.exists():
        return "sciona"
    for raw_line in config_path.read_text().splitlines():
        line = raw_line.strip()
        if not line.startswith("project_id"):
            continue
        _, _, value = line.partition("=")
        project_id = value.strip().strip('"').strip("'")
        return project_id or "sciona"
    return "sciona"


@pytest.fixture(scope="session")
def supabase_local_env(repo_root: Path) -> Iterator[dict[str, str]]:
    if os.environ.get("SCIONA_TEST_SUPABASE_LOCAL", "0") != "1":
        pytest.skip("Set SCIONA_TEST_SUPABASE_LOCAL=1 to enable local Supabase tests")
    if not _docker_available():
        pytest.skip("Docker is not available for local Supabase tests")

    project_id = _supabase_project_id(repo_root)
    started_here = False
    status = _run_subprocess(
        ["supabase", "status", "-o", "env"],
        cwd=repo_root,
        timeout=60.0,
    )
    if status.returncode != 0:
        start = _run_subprocess(
            ["supabase", "start", "--ignore-health-check", "--yes"],
            cwd=repo_root,
            timeout=1200.0,
        )
        if start.returncode != 0:
            pytest.skip(
                "Unable to start local Supabase stack:\n"
                f"stdout:\n{start.stdout}\n\nstderr:\n{start.stderr}"
            )
        started_here = True

    if os.environ.get("SCIONA_TEST_SUPABASE_RESET_LOCAL", "0") == "1":
        reset = _run_subprocess(
            ["supabase", "db", "reset", "--local", "--no-seed", "--yes"],
            cwd=repo_root,
            timeout=1200.0,
        )
        recovered_status = _wait_for_supabase_ready(repo_root)
        if recovered_status is None:
            pytest.fail(
                "Local Supabase reset failed to recover:\n"
                f"stdout:\n{reset.stdout}\n\nstderr:\n{reset.stderr}"
            )
        status = recovered_status

    parsed = _parse_env_lines(status.stdout)
    api_url = _pick_env_var(parsed, "API_URL", "SUPABASE_URL")
    anon_key = _pick_env_var(parsed, "ANON_KEY", "SUPABASE_ANON_KEY")
    service_role_key = _pick_env_var(
        parsed,
        "SERVICE_ROLE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
    )
    db_url = _pick_env_var(
        parsed,
        "DB_URL",
        "POSTGRES_URL",
        "SUPABASE_DB_URL",
    )
    if not (api_url and anon_key and service_role_key and db_url):
        pytest.fail(f"Incomplete local Supabase status output: {parsed!r}")

    try:
        yield {
            "api_url": api_url,
            "anon_key": anon_key,
            "service_role_key": service_role_key,
            "db_url": db_url,
        }
    finally:
        if started_here:
            _run_subprocess(
                ["supabase", "stop", "--project-id", project_id],
                cwd=repo_root,
                timeout=300.0,
            )


@pytest.fixture(scope="session")
def ageo_atoms_root(repo_root: Path) -> Path:
    configured = os.environ.get("SCIONA_TEST_AGEO_ATOMS_ROOT", "")
    if configured:
        return Path(configured).expanduser().resolve()
    return (repo_root / ".." / "ageo-atoms").resolve()


@pytest.fixture(scope="session")
def match_cases(repo_root: Path) -> list[MatchCase]:
    fixture_path = repo_root / "tests" / "fixtures" / "match_cases.json"
    if not fixture_path.exists():
        raise FileNotFoundError(f"Missing match-case fixture: {fixture_path}")
    return load_match_cases(repo_root, fixture_path)


@pytest.fixture(scope="session")
def ageo_atoms_declarations(ageo_atoms_root: Path) -> list:
    if not ageo_atoms_root.exists():
        pytest.skip(f"ageo-atoms repo not found at {ageo_atoms_root}")
    return build_ageo_atoms_declarations(ageo_atoms_root)


@pytest.fixture(scope="session")
def llama_server() -> Iterator[dict[str, str]]:
    """Start a dedicated local llama.cpp server on an ephemeral port for live tests."""
    cmd_template = os.environ.get("SCIONA_TEST_LLAMA_SERVER_CMD", "").strip()
    if not cmd_template:
        pytest.skip(
            "Set SCIONA_TEST_LLAMA_SERVER_CMD to enable live llama regression tests"
        )

    model_name = os.environ.get("SCIONA_TEST_LLAMA_MODEL", "").strip() or "local-llama"

    port = _find_free_port()
    cmd_str = cmd_template.format(port=port)
    cmd = shlex.split(cmd_str)

    if "{port}" not in cmd_template and "--port" not in cmd and "-p" not in cmd:
        cmd.extend(["--host", "127.0.0.1", "--port", str(port)])

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )

    base_url = f"http://127.0.0.1:{port}/v1"
    deadline = time.time() + 60.0
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.skip("llama test server exited before becoming ready")
        if _check_llama_ready(base_url):
            break
        time.sleep(0.5)
    else:
        _terminate_process(proc)
        pytest.skip("llama test server did not become ready within 60s")

    try:
        yield {
            "base_url": base_url,
            "model": model_name,
            "port": str(port),
            "command": json.dumps(cmd),
        }
    finally:
        _terminate_process(proc)
