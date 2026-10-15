"""Market-data contract and the single construction entry point.

Return shapes are asserted by ``tests/test_data_provider_contract.py``:

- 分钟采样 ``historical_minutes`` 至多 240 点，不填补缺失数据；
- 交易日历 ``trading_dates`` 升序且追加下一交易日；
- A 股 T+1 语义不变：缺数据按可重试处理，不得伪造当日成交。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple, runtime_checkable


@runtime_checkable
class DataProvider(Protocol):
    """沪深行情数据源契约。"""

    source_name: str

    def trading_dates(self) -> Sequence[date]:
        """升序交易日列表，末尾追加下一个交易日。"""

    def securities(self) -> List[Dict[str, Any]]:
        """沪深 A 股列表。每项含 symbol/name/market(sh|sz)/board/
        previous_close/volume_unit/decimal_point。"""

    def security(self, symbol: str) -> Optional[Dict[str, Any]]:
        """单只证券信息，未知返回 None；字段同 ``securities`` 单项。"""

    def stock_blocks(self) -> List[Dict[str, Any]]:
        """板块列表，每项含 blockname/count。"""

    def stock_block_symbols(self, blockname: str) -> Tuple[str, ...]:
        """板块成分股代码；板块无效抛 ValueError。"""

    def reference_snapshot(self) -> Dict[str, Any]:
        """参考数据快照：source_node/raw_securities/expected_counts/files/
        securities/memberships。供 reference_sync 落库。"""

    def quote_snapshots(
        self,
        symbols: Sequence[str],
    ) -> Dict[str, Dict[str, Any]]:
        """盘中实时报价，按 code 映射原始行；空输入返回空字典。"""

    def company_profile(self, symbol: str) -> Dict[str, Any]:
        """公司财务/除权除息/F10 分类：finance/corporate_actions/categories。"""

    def company_section(self, symbol: str, section: str) -> str:
        """F10 指定栏目文本。"""

    def history_bars(
        self,
        symbol: str,
        period: str,
        *,
        end_date: Optional[date] = None,
        max_bars: int = 12000,
    ) -> List[Dict[str, Any]]:
        """历史 K 线（升序）。每根含 time/date/open/high/low/close/volume/
        amount/raw。period ∈ day|week|month|year。"""

    def kline_bars(
        self,
        symbol: str,
        period: str,
        end_date: date,
        *,
        limit: int = 120,
    ) -> List[Dict[str, Any]]:
        """最近 limit 根 K 线，字段同 ``history_bars``。"""

    def historical_minutes(
        self,
        symbol: str,
        session: date,
    ) -> List[Dict[str, Any]]:
        """某交易日分钟线，至多 240 根。每根含 time/date/
        open/high/low/close/volume/amount/raw。"""

    def limit_up_history(
        self,
        start_date: date,
        end_date: date,
    ) -> Dict[str, Any]:
        """区间涨停历史采集结果。"""

    def limit_up_pool(self, session: date) -> List[Dict[str, Any]]:
        """某交易日涨停池。"""

    def broken_board_pool(self, session: date) -> List[Dict[str, Any]]:
        """某交易日炸板池。"""

    def strategy_pool(
        self,
        session: date,
        archetype: str,
    ) -> List[Dict[str, Any]]:
        """按策略原型装配的候选池（含题材归属）。"""


# The 16 methods plus ``source_name`` constitute the frozen surface. Any backend
# must implement all of them; contract tests enumerate this tuple.
PROVIDER_METHODS: Tuple[str, ...] = (
    "trading_dates",
    "securities",
    "security",
    "stock_blocks",
    "stock_block_symbols",
    "reference_snapshot",
    "quote_snapshots",
    "company_profile",
    "company_section",
    "history_bars",
    "kline_bars",
    "historical_minutes",
    "limit_up_history",
    "limit_up_pool",
    "broken_board_pool",
    "strategy_pool",
)

DEFAULT_PROVIDER = "rustdx"
SUPPORTED_PROVIDERS: Tuple[str, ...] = ("rustdx",)


def resolve_provider_name(name: Optional[str]) -> str:
    """旧配置中的 mootdx 名称归一化为 rustdx，不加载退役依赖。"""
    resolved = (name or DEFAULT_PROVIDER).strip().lower() or DEFAULT_PROVIDER
    if resolved == "mootdx":
        resolved = DEFAULT_PROVIDER
    if resolved not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"BANXIA_DATA_PROVIDER 必须是 {', '.join(SUPPORTED_PROVIDERS)}，"
            f"收到 {name!r}"
        )
    return resolved


def make_provider(name: Optional[str] = None, **kwargs: Any) -> DataProvider:
    """按名称构造数据源后端。

    name 为 None 时读环境变量 ``BANXIA_DATA_PROVIDER``，仍为空则用 rustdx。
    kwargs 透传给具体后端构造器（如 history_sessions/workers/servers）。
    这是所有调用点应使用的唯一构造入口，使数据源可通过配置灰度切换。
    """
    import os

    resolve_provider_name(
        name if name is not None else os.environ.get("BANXIA_DATA_PROVIDER")
    )
    from .rustdx_provider import RustdxProvider
    return RustdxProvider(**kwargs)
