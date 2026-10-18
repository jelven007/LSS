"""Supervise the durable tenant workers as one Kubernetes workload."""
from __future__ import annotations

import logging
import signal
import subprocess
import sys
import threading
from contextlib import suppress

WORKER_MODULES = (
    "app.multitenancy.research_worker",
    "app.multitenancy.runtime",
    "app.multitenancy.notification_worker",
    "app.multitenancy.scheduled_worker",
)

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    stopped = threading.Event()
    processes: dict[str, subprocess.Popen] = {}
    exit_code = 0

    def request_stop(*_args) -> None:
        stopped.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, request_stop)

    try:
        for module in WORKER_MODULES:
            processes[module] = subprocess.Popen(
                [sys.executable, "-m", module],
                close_fds=True,
            )
        logger.info("task worker started", extra={"workers": list(WORKER_MODULES)})
        while not stopped.wait(1):
            for module, process in processes.items():
                result = process.poll()
                if result is None:
                    continue
                logger.error(
                    "task worker subprocess exited",
                    extra={"worker": module, "exit_code": result},
                )
                exit_code = result or 1
                stopped.set()
                break
    finally:
        for process in processes.values():
            if process.poll() is None:
                with suppress(OSError):
                    process.terminate()
        for process in processes.values():
            if process.poll() is not None:
                continue
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                with suppress(OSError):
                    process.kill()
        logger.info("task worker stopped")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
