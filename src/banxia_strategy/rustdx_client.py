"""Native rustdx transport and record adapter, without pandas or legacy clients."""
from __future__ import annotations

import importlib
import json
import os
from datetime import datetime
from typing import Any, Mapping, Sequence

RUSTDX_SOURCE_REV = "904b33e514d4e368e633622c96a9e63af8a53d37"


class RustdxNotReady(RuntimeError):
    """Missing bridge, invalid response, or unavailable upstream connection."""


def native_module():
    try:
        module = importlib.import_module("banxia_rustdx_native")
    except ImportError as exc:
        raise RustdxNotReady(
            "缺少 rustdx 原生桥接，请先运行 python -m pip install ./native/rustdx_native"
        ) from exc
    if getattr(module, "RUSTDX_SOURCE_REV", None) != RUSTDX_SOURCE_REV:
        raise RustdxNotReady("rustdx 原生桥接版本不匹配，请重新构建")
    return module


def records(payload: str) -> list[dict[str, Any]]:
    try:
        rows = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise RustdxNotReady("rustdx 返回无效 JSON") from exc
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RustdxNotReady("rustdx 返回结构不是记录数组")
    return rows


class Record(dict):
    def to_dict(self):
        return dict(self)


class Column(list):
    def tolist(self):
        return list(self)


class RecordFrame:
    """Only the record operations used by the existing strategy collectors."""

    def __init__(self, rows: Sequence[Mapping[str, Any]]):
        self.rows = [dict(row) for row in rows]

    @property
    def empty(self):
        return not self.rows

    @property
    def iloc(self):
        return [Record(row) for row in self.rows]

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, key):
        return Column(row.get(key) for row in self.rows)

    def to_dict(self, orient="records"):
        if orient != "records":
            raise ValueError("Only record orientation is supported")
        return [dict(row) for row in self.rows]


def _market(symbol: str) -> int:
    from .market_provider import TdxMarketProvider
    TdxMarketProvider._validate_symbol(symbol)
    return 1 if symbol.startswith("6") else 0


def _bar(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["source"] = "rustdx"
    parts = result.pop("dt", None)
    if not isinstance(parts, dict):
        raise RustdxNotReady("rustdx K 线缺少原始日期")
    try:
        stamp = datetime(
            int(parts["year"]), int(parts["month"]), int(parts["day"]),
            int(parts.get("hour", 15)), int(parts.get("minute", 0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RustdxNotReady("rustdx K 线日期无效") from exc
    result["datetime"] = stamp.isoformat(sep=" ")
    for key in ("vol", "amount"):
        if result.get(key) == 2.0 ** -127:
            result[key] = 0.0
    return result


class RustdxQuoteClient:
    """One native TCP connection; ownership follows each existing worker."""

    def __init__(self, server, *, native=None, timeout=None):
        self._native = native or native_module().QuoteClient(
            f"{server[0]}:{server[1]}",
            int(timeout or os.environ.get("BANXIA_RUSTDX_TIMEOUT_SECONDS", "5")),
        )
        self.client = self
        self._catalog = {}
        self._files = {}
        self._f10 = {}

    def close(self):
        self._native.close()

    def get_security_list(self, market, start):
        if market not in self._catalog:
            self._catalog[market] = records(self._native.stocks_json(market))
        return self._catalog[market][start:start + 1000]

    def stock_count(self, market):
        self.get_security_list(market, 0)
        return len(self._catalog[market])

    def quotes(self, *, symbol):
        symbols = list(symbol) if not isinstance(symbol, str) else [symbol]
        rows = records(self._native.quotes_json([(_market(code), code) for code in symbols]))
        requested = set(symbols)
        if any(str(row.get("code")) not in requested for row in rows):
            raise RustdxNotReady("rustdx 返回了请求之外的盘口")
        for row in rows:
            row["source"] = "rustdx"
            for side in ("bid", "ask"):
                for level in range(1, 6):
                    source = f"{side}{level}_vol"
                    if source in row:
                        row[f"{side}_vol{level}"] = row.pop(source)
            for key in ("vol", "amount"):
                if row.get(key) == 2.0 ** -127:
                    row[key] = 0.0
        return RecordFrame(rows)

    def _bars(self, symbol, frequency, start, offset, index):
        market = 1 if index else _market(symbol)
        rows = records(self._native.bars_json(
            market, symbol, frequency, start, min(offset, 800), index,
        ))
        return RecordFrame([_bar(row) for row in rows])

    def bars(self, *, symbol, frequency=9, start=0, offset=800):
        return self._bars(symbol, frequency, start, offset, False)

    def index(self, *, symbol, frequency=9, start=0, offset=800):
        return self._bars(symbol, frequency, start, offset, True)

    def minutes(self, *, symbol, date):
        session = datetime.strptime(str(date), "%Y%m%d").date()
        rows = records(self._native.minutes_json(_market(symbol), symbol, int(date)))
        if not rows or len(rows) > 240:
            raise RustdxNotReady(f"rustdx 历史分时缺失或长度无效：{symbol} {session}")
        from .market_provider import _finite_number
        if any(
            (_finite_number(row.get("price")) or 0) <= 0
            or _finite_number(row.get("vol")) is None
            or float(row["vol"]) < 0
            for row in rows
        ):
            raise RustdxNotReady(f"rustdx 历史分时含无效价格或成交量：{symbol} {session}")
        for row in rows:
            row["source"] = "rustdx"
        return RecordFrame(rows)

    def transactions(self, *, symbol, date, start=0, offset=2000):
        datetime.strptime(str(date), "%Y%m%d")
        return RecordFrame(records(self._native.transactions_json(
            _market(symbol), symbol, int(date), start, min(offset, 2000),
        )))

    def finance(self, *, symbol):
        payload = json.loads(self._native.finance_json(_market(symbol), symbol))
        if not isinstance(payload, dict) or str(payload.get("code")) != symbol:
            raise RustdxNotReady("rustdx 财务响应代码不匹配")
        return RecordFrame([payload])

    def get_xdxr_info(self, market, symbol):
        if market != _market(symbol):
            raise ValueError("market does not match symbol")
        return records(self._native.xdxr_json(market, symbol))

    def xdxr(self, *, symbol):
        return RecordFrame(self.get_xdxr_info(_market(symbol), symbol))

    def _sections(self, symbol):
        if symbol not in self._f10:
            payload = json.loads(self._native.f10_json(_market(symbol), symbol))
            if not isinstance(payload, list) or any(
                not isinstance(row, list) or len(row) != 2
                or not all(isinstance(value, str) for value in row)
                for row in payload
            ):
                raise RustdxNotReady("rustdx F10 响应无效")
            self._f10[symbol] = {name.strip(): text for name, text in payload}
        return self._f10[symbol]

    def F10C(self, *, symbol):
        return [
            {"name": name, "length": len(text.encode("gbk", errors="replace"))}
            for name, text in self._sections(symbol).items()
        ]

    def F10(self, *, symbol, name):
        sections = self._sections(symbol)
        if name not in sections:
            raise RustdxNotReady(f"rustdx F10 栏目不存在：{name}")
        return sections[name]

    def get_block_info_meta(self, filename):
        if filename not in self._files:
            self._files[filename] = bytes(self._native.file_bytes(filename))
        return {"size": len(self._files[filename])}

    def get_block_info(self, filename, start, size):
        self.get_block_info_meta(filename)
        return self._files[filename][start:start + size]
