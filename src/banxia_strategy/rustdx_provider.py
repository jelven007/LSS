"""rustdx 后端骨架：通过 rustdx serve 的 HTTP/JSON 接口提供行情数据。

本模块是全面替换 mootdx 的第二后端。rustdx 本体是纯 Rust 库（crate
``jelven-rustdx-complete``），无 Python 绑定，因此采用进程外 HTTP 集成：
rustdx 侧新增常驻 ``serve`` 服务暴露 quotes/minute/k/stocks/block/finance
等端点，本类作为同契约的 Python 客户端。

当前为骨架：方法签名、入参校验、返回结构规整与 mootdx 后端保持一致，但在
``rustdx serve`` 端点接通前，依赖远端的调用显式抛出 :class:`RustdxNotReady`，
绝不静默返回错误或空数据——避免污染 T+1 结算等关键链路。接口契约见
``data_provider.DataProvider``。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .mootdx_provider import (
    ALL_A_SHARE_PREFIXES,
    KLINE_FREQUENCIES,
    _minute_label,
)


DEFAULT_BASE_URL = "http://rustdx-serve.banxia.svc.cluster.local:7616"
DEFAULT_TIMEOUT_SECONDS = 5.0


class RustdxNotReady(RuntimeError):
    """rustdx serve 端点尚未接通或不可用时抛出。"""


class RustdxProvider:
    """rustdx serve 的 HTTP 客户端，满足 DataProvider 契约。

    骨架阶段：仅实现 HTTP 调用脚手架与入参/返回规整；具体端点在 rustdx serve
    上线后逐个接通。未接通的方法抛 :class:`RustdxNotReady`。
    """

    source_name = "rustdx"

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        history_sessions: int = 40,
        workers: int = 7,
        opener: Optional[Any] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.history_sessions = max(10, history_sessions)
        self.workers = max(1, workers)
        # Injected for tests; defaults to urllib at call time.
        self._opener = opener

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------
    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        query = f"?{urllib.parse.urlencode(params, doseq=True)}" if params else ""
        url = f"{self.base_url}{path}{query}"
        try:
            if self._opener is not None:
                return self._opener(url, self.timeout)
            request = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise RustdxNotReady(
                f"rustdx serve 不可用（{url}）：{exc}"
            ) from exc

    @staticmethod
    def _validate_symbol(symbol: str) -> None:
        if (
            not isinstance(symbol, str)
            or len(symbol) != 6
            or not symbol.isdigit()
            or not symbol.startswith(ALL_A_SHARE_PREFIXES)
        ):
            raise ValueError("股票代码必须是沪深 A 股六位代码")

    @staticmethod
    def _not_wired(endpoint: str) -> "RustdxNotReady":
        return RustdxNotReady(
            f"rustdx serve 端点 {endpoint!r} 尚未接通；"
            "请先在 rustdx 侧实现并部署该端点，再切换 BANXIA_DATA_PROVIDER=rustdx"
        )

    # ------------------------------------------------------------------
    # DataProvider contract — skeleton
    # Signatures and validation mirror MootdxProvider; endpoints are wired
    # incrementally once rustdx serve exposes them.
    # ------------------------------------------------------------------
    def trading_dates(self) -> Sequence[date]:
        raise self._not_wired("/v1/calendar")

    def securities(self) -> List[Dict[str, Any]]:
        raise self._not_wired("/v1/securities")

    def security(self, symbol: str) -> Optional[Dict[str, Any]]:
        self._validate_symbol(symbol)
        raise self._not_wired("/v1/securities")

    def stock_blocks(self) -> List[Dict[str, Any]]:
        raise self._not_wired("/v1/blocks")

    def stock_block_symbols(self, blockname: str) -> Tuple[str, ...]:
        raise self._not_wired("/v1/blocks")

    def reference_snapshot(self) -> Dict[str, Any]:
        raise self._not_wired("/v1/reference-snapshot")

    def quote_snapshots(
        self,
        symbols: Sequence[str],
    ) -> Dict[str, Dict[str, Any]]:
        codes = tuple(dict.fromkeys(symbols))
        if not codes:
            return {}
        for symbol in codes:
            self._validate_symbol(symbol)
        raise self._not_wired("/v1/quotes")

    def company_profile(self, symbol: str) -> Dict[str, Any]:
        self._validate_symbol(symbol)
        raise self._not_wired("/v1/company-profile")

    def company_section(self, symbol: str, section: str) -> str:
        self._validate_symbol(symbol)
        if not section or len(section) > 40:
            raise ValueError("公司资料栏目无效")
        raise self._not_wired("/v1/company-section")

    def history_bars(
        self,
        symbol: str,
        period: str,
        *,
        end_date: Optional[date] = None,
        max_bars: int = 12000,
    ) -> List[Dict[str, Any]]:
        self._validate_symbol(symbol)
        if period not in KLINE_FREQUENCIES:
            raise ValueError("K线周期必须是 day、week、month 或 year")
        if not 1 <= max_bars <= 20000:
            raise ValueError("历史 K 线数量必须为 1 至 20000")
        raise self._not_wired("/v1/bars")

    def kline_bars(
        self,
        symbol: str,
        period: str,
        end_date: date,
        *,
        limit: int = 120,
    ) -> List[Dict[str, Any]]:
        if period not in KLINE_FREQUENCIES:
            raise ValueError("K线周期必须是 day、week、month 或 year")
        self._validate_symbol(symbol)
        if not 1 <= limit <= 240:
            raise ValueError("K线数量必须为 1 至 240")
        raise self._not_wired("/v1/bars")

    def historical_minutes(
        self,
        symbol: str,
        session: date,
    ) -> List[Dict[str, Any]]:
        self._validate_symbol(symbol)
        raise self._not_wired("/v1/minutes")

    def limit_up_history(
        self,
        start_date: date,
        end_date: date,
    ) -> Dict[str, Any]:
        from .limit_up_collector import collect_limit_up_history

        # Reuses the shared collector, which drives this provider's own
        # historical_minutes / limit_up_pool; it works once those are wired.
        return collect_limit_up_history(self, start_date, end_date)

    def limit_up_pool(self, session: date) -> List[Dict[str, Any]]:
        raise self._not_wired("/v1/limit-up-pool")

    def broken_board_pool(self, session: date) -> List[Dict[str, Any]]:
        raise self._not_wired("/v1/broken-board-pool")

    def strategy_pool(
        self,
        session: date,
        archetype: str,
    ) -> List[Dict[str, Any]]:
        raise self._not_wired("/v1/strategy-pool")

    @staticmethod
    def _normalize_minutes(
        rows: Sequence[Dict[str, Any]],
        session: date,
    ) -> List[Dict[str, Any]]:
        """Shape rustdx minute rows into the frozen 240-bar contract.

        Kept as a static helper so the eventual /v1/minutes wiring and the
        contract tests share one normalization path with the mootdx backend.
        """
        result: List[Dict[str, Any]] = []
        for index, raw in enumerate(list(rows)[:240]):
            price = raw.get("close", raw.get("price"))
            try:
                price = float(price) if price is not None else None
            except (TypeError, ValueError):
                price = None
            if price is None or price <= 0:
                continue
            label = _minute_label(index)
            volume = raw.get("volume", raw.get("vol"))
            try:
                volume = float(volume) if volume is not None else None
            except (TypeError, ValueError):
                volume = None
            result.append(
                {
                    "time": f"{session.isoformat()}T{label}+08:00",
                    "date": session.isoformat(),
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": volume,
                    "amount": None,
                    "raw": dict(raw),
                }
            )
        return result
