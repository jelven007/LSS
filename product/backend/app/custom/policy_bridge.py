"""Authenticated Policy facade with an in-process production backend."""
from __future__ import annotations

import atexit
import os
import re
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request

from app.extensions import BACKEND_EXTENSION_API_VERSION, BackendExtensionRegistrar

EXTENSION_ID = "policy.bridge"
EXTENSION_API_VERSION = BACKEND_EXTENSION_API_VERSION

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SYMBOL_RE = re.compile(r"^\d{6}$")


@dataclass(frozen=True)
class PolicyBridgeSettings:
    base_url: str
    token: str
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> PolicyBridgeSettings:
        return cls(
            base_url=os.environ.get("POLICY_SERVICE_URL", "").strip().rstrip("/"),
            token=os.environ.get("POLICY_SERVICE_TOKEN", "").strip(),
            timeout_seconds=float(os.environ.get("POLICY_SERVICE_TIMEOUT_SECONDS", "30")),
        )

    def validate(self) -> None:
        if not 1 <= self.timeout_seconds <= 300:
            raise HTTPException(status_code=503, detail="Policy 服务超时配置无效")
        if self.base_url == "embedded://policy":
            return
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=503, detail="Policy 服务尚未配置")


class PolicyBridge:
    def __init__(
        self,
        settings: PolicyBridgeSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        self.settings.validate()
        headers = {"Accept": "application/json"}
        base_url = self.settings.base_url
        transport = self.transport
        if base_url == "embedded://policy":
            base_url = "http://policy.internal"
            if transport is None:
                try:
                    transport = _embedded_policy.transport()
                except Exception as exc:
                    raise HTTPException(
                        status_code=503,
                        detail="Policy 内嵌服务初始化失败",
                    ) from exc
        elif self.settings.token:
            headers["Authorization"] = f"Bearer {self.settings.token}"
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.timeout_seconds,
                transport=transport,
                follow_redirects=False,
            ) as client:
                response = await client.request(
                    method,
                    f"{base_url}{path}",
                    params=params,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Policy 服务连接失败: {exc}",
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=502,
                detail="Policy 服务返回了无效响应",
            ) from exc
        if response.is_error:
            if response.status_code in {401, 403}:
                raise HTTPException(
                    status_code=502,
                    detail="Policy 服务鉴权配置无效",
                )
            detail = payload.get("detail") if isinstance(payload, dict) else None
            if not detail and isinstance(payload, dict):
                detail = payload.get("error", {}).get("message")
            raise HTTPException(
                status_code=response.status_code,
                detail=str(detail or "Policy 服务请求失败"),
            )
        return payload


class EmbeddedPolicyRuntime:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._transport: httpx.ASGITransport | None = None
        self._resources: tuple[Any, ...] = ()

    def transport(self) -> httpx.ASGITransport:
        with self._lock:
            if self._transport is None:
                from banxia_strategy.api import create_api_app
                from banxia_strategy.config import RuntimeSettings
                from banxia_strategy.service import build_api_services

                settings = RuntimeSettings.from_env(
                    service_name="embedded-policy-api",
                )
                services, self._resources = build_api_services(
                    settings,
                    embedded=True,
                )
                self._transport = httpx.ASGITransport(
                    app=create_api_app(services),
                )
            return self._transport

    def close(self) -> None:
        with self._lock:
            resources, self._resources = self._resources, ()
            self._transport = None
        for resource in resources:
            close = getattr(resource, "close", None)
            if close is not None:
                close()


_embedded_policy = EmbeddedPolicyRuntime()
atexit.register(_embedded_policy.close)


router = APIRouter(prefix="/api/policy", tags=["policy"])
bridge = PolicyBridge(PolicyBridgeSettings.from_env())


def _date(value: str) -> str:
    if not _DATE_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="日期格式必须为 YYYY-MM-DD")
    return value


def _symbol(value: str) -> str:
    if not _SYMBOL_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="股票代码必须为六位数字")
    return value


def _require_data_manager(request: Request) -> None:
    account = getattr(request.state, "account", None)
    if account is None:
        return
    if "data:manage" not in account.get("permissions", ()):
        raise HTTPException(status_code=403, detail="无权刷新 Policy 数据")


@router.get("/reports")
async def reports(limit: int = 50, cursor: str | None = None) -> Any:
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=400, detail="limit must be 1..200")
    params = {"limit": limit}
    if cursor:
        params["cursor"] = _date(cursor)
    return await bridge.request("GET", "/api/v1/reports", params=params)


@router.get("/reports/{trade_date}")
async def report(trade_date: str) -> Any:
    return await bridge.request("GET", f"/api/v1/reports/{_date(trade_date)}")


@router.post("/reports/{trade_date}/refresh", status_code=202)
async def refresh_report(trade_date: str, request: Request) -> Any:
    _require_data_manager(request)
    return await bridge.request(
        "POST",
        f"/api/v1/reports/{_date(trade_date)}/refresh",
    )


@router.get("/report-jobs/{job_id}")
async def report_job(job_id: str) -> Any:
    try:
        normalized = str(uuid.UUID(job_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="任务 ID 无效") from exc
    return await bridge.request("GET", f"/api/v1/report-jobs/{normalized}")


@router.get("/monitor")
async def monitor(
    trade_date: str | None = None,
    symbols: str | None = None,
) -> Any:
    params: dict[str, str] = {}
    if trade_date:
        params["trade_date"] = _date(trade_date)
    if symbols:
        values = [item.strip() for item in symbols.split(",") if item.strip()]
        if not values or any(not _SYMBOL_RE.fullmatch(item) for item in values):
            raise HTTPException(status_code=400, detail="股票代码列表无效")
        params["symbols"] = ",".join(dict.fromkeys(values))
    return await bridge.request("GET", "/api/v1/monitor", params=params)


@router.get("/monitor/kline/{symbol}")
async def monitor_kline(symbol: str, trade_date: str | None = None) -> Any:
    params = {"trade_date": _date(trade_date)} if trade_date else None
    return await bridge.request(
        "GET",
        f"/api/v1/monitor/kline/{_symbol(symbol)}",
        params=params,
    )


@router.get("/monitor/events")
async def monitor_events(
    trade_date: str | None = None,
    limit: int = 100,
) -> Any:
    if not 1 <= limit <= 1000:
        raise HTTPException(status_code=400, detail="limit must be 1..1000")
    params: dict[str, Any] = {"limit": limit}
    if trade_date:
        params["trade_date"] = _date(trade_date)
    return await bridge.request("GET", "/api/v1/monitor/events", params=params)


def setup(registrar: BackendExtensionRegistrar) -> None:
    registrar.include_router(router)


def shutdown() -> None:
    _embedded_policy.close()
