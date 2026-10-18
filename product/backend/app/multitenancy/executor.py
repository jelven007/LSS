"""Client for the bounded compute service.

Production uses a network-isolated in-cluster service; standalone deployments
may retain the Unix socket transport. Requests cannot override process limits,
the public market root or the compute entrypoint.
"""
# ruff: noqa: RUF001 -- Chinese compute messages use localized punctuation.
from __future__ import annotations

import json
import os
import socket
import time
from collections.abc import Callable

from app.cell.context import AccessError

MAX_MESSAGE_BYTES = 128 * 1024 * 1024
KINDS = frozenset({"describe", "plan", "strategy", "backtest", "optimize", "walkforward", "mining",
                   "signal_backtest", "factor_backtest", "factor_batch", "auto_screen", "monitor"})


class ComputeExecutor:
    def __init__(
        self,
        socket_path: str | None = None,
        address: str | None = None,
    ):
        self.socket_path = socket_path or os.environ.get(
            "TSP_COMPUTE_SOCKET",
            "/run/tsp/compute.sock",
        )
        self.address = address or os.environ.get("TSP_COMPUTE_ADDRESS", "").strip()

    def _connect(self) -> socket.socket:
        if self.address:
            host, separator, raw_port = self.address.rpartition(":")
            if not separator or not host:
                raise ValueError("TSP_COMPUTE_ADDRESS must use host:port")
            try:
                port = int(raw_port)
            except ValueError as exc:
                raise ValueError("TSP_COMPUTE_ADDRESS port must be an integer") from exc
            if not 1 <= port <= 65535:
                raise ValueError("TSP_COMPUTE_ADDRESS port is out of range")
            return socket.create_connection((host, port), timeout=5)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(5)
        client.connect(self.socket_path)
        return client

    def health(self):
        return self.execute({"kind": "health"})

    def execute(
        self, payload: dict, progress: Callable[[dict], None] | None = None, cancel=None,
    ) -> dict:
        if payload.get("kind") not in KINDS and payload.get("kind") != "health":
            raise ValueError("unsupported compute operation")
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                default=str,
            ).encode()
            + b"\n"
        )
        if len(encoded) > MAX_MESSAGE_BYTES:
            raise AccessError(413, "计算任务输入过大")
        try:
            with self._connect() as client:
                client.settimeout(30)
                client.sendall(encoded)
                client.settimeout(1)
                pending = bytearray()
                deadline = time.monotonic() + (5 if payload["kind"] == "health" else 7500)
                while True:
                    if time.monotonic() > deadline:
                        raise AccessError(503, "计算服务响应超时")
                    if cancel is not None and cancel.is_set():
                        # EOF is cancellation. The broker kills the container,
                        # including descendants, before releasing its permit.
                        raise AccessError(409, "计算任务已取消")
                    try:
                        block = client.recv(64 * 1024)
                    except TimeoutError:
                        continue
                    if not block:
                        raise AccessError(503, "计算服务连接中断")
                    pending.extend(block)
                    if len(pending) > MAX_MESSAGE_BYTES:
                        raise AccessError(413, "计算任务输出过大")
                    while b"\n" in pending:
                        line, _, remaining = pending.partition(b"\n")
                        pending = bytearray(remaining)
                        message = json.loads(line)
                        if message.get("type") == "progress":
                            if progress is not None:
                                progress(message["payload"])
                        elif message.get("type") == "result":
                            return message["payload"]
                        elif message.get("type") == "error":
                            raise AccessError(int(message.get("status", 400)), message["message"])
        except (FileNotFoundError, ConnectionRefusedError, PermissionError, TimeoutError) as exc:
            raise AccessError(503, "计算服务暂不可用，请稍后重试") from exc
