"""Transport and execution boundary checks for full-product private strategies."""
import json
import signal
import socket
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

if not hasattr(socket, "AF_UNIX"):
    # The broker is a host-only Unix-socket service; Windows builds without
    # AF_UNIX cannot host it, and its module cannot even be imported there.
    pytest.skip("Unix domain sockets are unavailable on this platform", allow_module_level=True)

from app.cell.context import AccessError
from app.multitenancy.compute_broker import ComputeBroker, ProcessComputeBroker
from app.multitenancy.executor import ComputeExecutor
from app.multitenancy.strategies import definition_payload, metadata_definition
from app.strategy.engine import StrategyEngine


@pytest.fixture
def socket_dir():
    # AF_UNIX paths are limited to 104 bytes on macOS; pytest's normal
    # temporary path includes the long test name.
    with tempfile.TemporaryDirectory(prefix="tsp-ipc-", dir="/tmp") as directory:
        yield Path(directory)


def test_metadata_transfer_never_includes_callable_or_private_file_path():
    root = Path(__file__).resolve().parents[1] / "app" / "strategy" / "builtin"
    engine = StrategyEngine([root])
    for strategy in engine.strategy_definitions():
        payload = definition_payload(strategy)
        transferred = metadata_definition(json.loads(json.dumps(payload)))
        assert transferred.meta == strategy.meta
        assert transferred.required_features == strategy.required_features
        assert transferred.filter_fn is None and transferred.filter_history_fn is None
        assert transferred.matrix_strategy is None and transferred.file_path is None
        assert transferred.execution_backend == strategy.execution_backend


def test_broker_owns_image_entrypoint_and_only_mounts_public_market(socket_dir):
    tmp_path = socket_dir
    public = tmp_path / "public"
    public.mkdir()
    with ComputeBroker(tmp_path / "compute.sock", "pinned-image:digest", public, 4) as broker:
        command = broker.command("tsp-test-job")
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--user") + 1] == "10001:10001"
    assert command[command.index("--memory") + 1] == "4g"
    assert "--read-only" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command.count("--mount") == 1
    assert command[command.index("--mount") + 1] == f"type=bind,src={public},dst=/market,readonly"
    assert command[command.index("pinned-image:digest") + 1] == "-i"
    assert "TICKFLOW_ENV_FILE=/dev/null" in command
    assert "HOME=/tmp" in command
    assert "XDG_CACHE_HOME=/tmp/xdg" in command
    assert not any("docker.sock" in part or "DSN" in part or "password" in part for part in command)
    assert not any(part.startswith("HOME=/root") for part in command)


def test_process_broker_is_container_native_and_has_no_docker_dependency(socket_dir):
    public = socket_dir / "public"
    public.mkdir()
    with ProcessComputeBroker(("127.0.0.1", 0), public, 2) as broker:
        command = broker.command("ignored")
    assert command[0:2] == ["/usr/bin/env", "-i"]
    assert f"DATA_DIR={public}" in command
    assert "TICKFLOW_ENV_FILE=/dev/null" in command
    assert "app.multitenancy.compute_worker" in command
    assert not any("docker" in part or "DSN" in part for part in command)


def test_compute_worker_uses_configured_public_release_before_private_root(tmp_path, monkeypatch):
    from app.config import settings
    from app.multitenancy.compute_worker import execute

    public = tmp_path / "mounted-public-release"
    public.mkdir()
    (public / "public-marker").write_text("market data")
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setattr(settings, "data_dir", public)
    result = execute({"kind": "describe", "strategy_id": "n_day_low_reversal"}, private)
    assert result["definition"]["meta"]["id"] == "n_day_low_reversal"
    assert (private / "public-marker").is_symlink()
    assert (private / "public-marker").read_text() == "market data"
    assert not (public / "strategies").exists()


def test_process_broker_cleanup_terminates_the_complete_process_group(monkeypatch):
    signals = []

    class Process:
        pid = 321
        calls = 0

        @staticmethod
        def poll():
            return None

        def communicate(self, timeout):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("compute", timeout)
            return b"", b""

    monkeypatch.setattr(
        "app.multitenancy.compute_broker.os.killpg",
        lambda pid, signum: signals.append((pid, signum)),
    )
    process = Process()

    ProcessComputeBroker.cleanup("ignored", process)

    assert signals == [
        (321, signal.SIGTERM),
        (321, signal.SIGKILL),
    ]
    assert process.calls == 2


def test_executor_streams_progress_result_and_rejects_unknown_operations(socket_dir):
    tmp_path = socket_dir
    path = tmp_path / "compute.sock"
    events = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        server.listen()

        def respond():
            connection, _ = server.accept()
            with connection, connection.makefile("rb") as request:
                payload = json.loads(request.readline())
                assert payload["tenant_id"] == "tenant-a"
                connection.sendall(b'{"type":"progress","payload":{"step":1}}\n')
                connection.sendall(b'{"type":"heartbeat"}\n')
                connection.sendall(b'{"type":"result","payload":{"total":1}}\n')

        thread = threading.Thread(target=respond)
        thread.start()
        executor = ComputeExecutor(str(path))
        assert executor.execute(
            {"kind": "strategy", "tenant_id": "tenant-a"},
            events.append,
        ) == {"total": 1}
        thread.join(timeout=2)
    assert events == [{"step": 1}]
    with pytest.raises(ValueError, match="unsupported compute"):
        executor.execute({"kind": "shell", "command": "anything"})


def test_executor_supports_the_network_isolated_compute_service():
    events = []
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        host, port = server.getsockname()

        def respond():
            connection, _ = server.accept()
            with connection, connection.makefile("rb") as request:
                payload = json.loads(request.readline())
                assert payload["tenant_id"] == "tenant-a"
                connection.sendall(b'{"type":"progress","payload":{"step":1}}\n')
                connection.sendall(b'{"type":"result","payload":{"total":1}}\n')

        thread = threading.Thread(target=respond)
        thread.start()
        executor = ComputeExecutor(address=f"{host}:{port}")
        assert executor.execute(
            {"kind": "strategy", "tenant_id": "tenant-a"},
            events.append,
        ) == {"total": 1}
        thread.join(timeout=2)
    assert events == [{"step": 1}]


def test_executor_cancellation_closes_transport_and_service_unavailability_is_503(socket_dir):
    tmp_path = socket_dir
    path = tmp_path / "compute.sock"
    disconnected = threading.Event()
    cancel = threading.Event()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        server.listen()

        def respond():
            connection, _ = server.accept()
            with connection, connection.makefile("rb") as request:
                request.readline()
                cancel.set()
                assert connection.recv(1) == b""
                disconnected.set()

        thread = threading.Thread(target=respond)
        thread.start()
        with pytest.raises(AccessError, match="已取消"):
            ComputeExecutor(str(path)).execute(
                {"kind": "strategy", "tenant_id": "tenant-a"},
                cancel=cancel,
            )
        thread.join(timeout=3)
    assert disconnected.is_set()
    with pytest.raises(AccessError) as unavailable:
        ComputeExecutor(str(tmp_path / "absent.sock")).execute({"kind": "describe"})
    assert unavailable.value.status == 503


def test_read_only_repository_does_not_create_missing_market_generation(tmp_path):
    from app.enriched_generation import EnrichedGenerationUnavailableError
    from app.tickflow.repository import DataStore, KlineRepository

    store = DataStore(tmp_path, read_only=True)
    try:
        repo = KlineRepository(store)
        with pytest.raises(EnrichedGenerationUnavailableError, match="marker is unavailable"):
            repo.get_matrix_data_generation()
        assert list(tmp_path.iterdir()) == []
    finally:
        store.db.close()
