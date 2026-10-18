from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

from app.custom.policy_bridge import (
    PolicyBridge,
    PolicyBridgeSettings,
    _date,
    _symbol,
)


@pytest.mark.asyncio
async def test_bridge_forwards_server_token_and_query() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(
            path=request.url.path,
            query=dict(request.url.params),
            authorization=request.headers.get("authorization"),
        )
        return httpx.Response(200, json={"items": [{"trade_date": "2026-10-17"}]})

    bridge = PolicyBridge(
        PolicyBridgeSettings(
            "https://policy.internal",
            "server-secret",
        ),
        transport=httpx.MockTransport(handler),
    )

    result = await bridge.request(
        "GET",
        "/api/v1/reports",
        params={"limit": 20},
    )

    assert result["items"][0]["trade_date"] == "2026-10-17"
    assert seen == {
        "path": "/api/v1/reports",
        "query": {"limit": "20"},
        "authorization": "Bearer server-secret",
    }


@pytest.mark.asyncio
async def test_bridge_preserves_upstream_error_without_exposing_credentials() -> None:
    bridge = PolicyBridge(
        PolicyBridgeSettings("https://policy.internal", "server-secret"),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                409,
                json={"error": {"message": "任务已经在执行"}},
            )
        ),
    )

    with pytest.raises(HTTPException) as error:
        await bridge.request("POST", "/api/v1/reports/2026-10-17/refresh")

    assert error.value.status_code == 409
    assert error.value.detail == "任务已经在执行"
    assert "server-secret" not in str(error.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_bridge_maps_upstream_auth_failure_to_gateway_error(status_code: int) -> None:
    bridge = PolicyBridge(
        PolicyBridgeSettings("https://policy.internal", "invalid-server-token"),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                status_code,
                json={"detail": "upstream credential detail"},
            )
        ),
    )

    with pytest.raises(HTTPException) as error:
        await bridge.request("GET", "/api/v1/reports")

    assert error.value.status_code == 502
    assert error.value.detail == "Policy 服务鉴权配置无效"
    assert "upstream credential detail" not in str(error.value.detail)


@pytest.mark.asyncio
async def test_bridge_fails_closed_when_service_is_not_configured() -> None:
    bridge = PolicyBridge(PolicyBridgeSettings("", ""))

    with pytest.raises(HTTPException) as error:
        await bridge.request("GET", "/api/v1/reports")

    assert error.value.status_code == 503
    assert error.value.detail == "Policy 服务尚未配置"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-17", "2026-10-17"),
        ("20261017", None),
        ("../secret", None),
    ],
)
def test_date_path_validation(value: str, expected: str | None) -> None:
    if expected is not None:
        assert _date(value) == expected
        return
    with pytest.raises(HTTPException):
        _date(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("600000", "600000"),
        ("60000", None),
        ("600000/../../secret", None),
    ],
)
def test_symbol_path_validation(value: str, expected: str | None) -> None:
    if expected is not None:
        assert _symbol(value) == expected
        return
    with pytest.raises(HTTPException):
        _symbol(value)
