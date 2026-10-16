"""Resumable full-market daily-bar backfill from rustdx into ClickHouse."""
from __future__ import annotations

import argparse
import logging
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from .adapters.clickhouse import ClickHouseMarketHistoryStore
from .limit_up_collector import daily_bars, unlisted_evidence
from .data_provider import make_provider
from .rustdx_provider import RustdxProvider
from .market_provider import _bar_date, _finite_number, _json_value
from .storage_config import StorageSettings


SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_START_DATE = date(2016, 1, 1)


@dataclass(frozen=True)
class BackfillResult:
    universe_count: int
    skipped_count: int
    completed_count: int
    unchanged_symbols: tuple[str, ...]
    excluded_symbols: tuple[str, ...]
    failed_symbols: tuple[str, ...]
    row_count: int
    start_date: date
    end_date: date


def normalize_daily_bars(
    records: Iterable[Mapping[str, Any]],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    result = []
    seen: set[date] = set()
    for raw in records:
        try:
            session = _bar_date(dict(raw))
        except (KeyError, TypeError, ValueError):
            continue
        if session in seen or not start_date <= session <= end_date:
            continue
        prices = {
            key: _finite_number(raw.get(key))
            for key in ("open", "high", "low", "close")
        }
        if any(value is None or value <= 0 for value in prices.values()):
            continue
        seen.add(session)
        result.append(
            {
                "time": f"{session.isoformat()}T15:00:00+08:00",
                "date": session.isoformat(),
                **prices,
                "volume": _finite_number(raw.get("vol", raw.get("volume"))),
                "amount": _finite_number(raw.get("amount")),
                "raw": {
                    str(key): _json_value(value)
                    for key, value in raw.items()
                },
            }
        )
    return sorted(result, key=lambda item: item["time"])


class StockHistoryBackfill:
    def __init__(
        self,
        *,
        provider: RustdxProvider,
        store: ClickHouseMarketHistoryStore,
        start_date: date = DEFAULT_START_DATE,
        end_date: Optional[date] = None,
        batch_size: int = 20,
        workers: Optional[int] = None,
        resume: bool = True,
        incremental: bool = False,
        lookback_days: int = 14,
        progress: Optional[Callable[[Mapping[str, Any]], None]] = None,
    ) -> None:
        self.provider = provider
        self.store = store
        self.start_date = start_date
        self.end_date = end_date or datetime.now(SHANGHAI).date()
        self.batch_size = max(1, batch_size)
        self.workers = max(
            1,
            min(
                workers or provider.workers,
                len(provider.servers),
            ),
        )
        self.resume = resume
        self.incremental = incremental
        self.lookback_days = max(0, lookback_days)
        self.progress = progress or (lambda _value: None)
        if self.start_date > self.end_date:
            raise ValueError("start_date cannot be later than end_date")

    def _fetch_batch(
        self,
        batch_number: int,
        securities: Sequence[Mapping[str, Any]],
    ) -> tuple[
        dict[str, list[dict[str, Any]]],
        list[str],
        list[str],
        list[str],
    ]:
        result: dict[str, list[dict[str, Any]]] = {}
        failures: list[str] = []
        excluded: list[str] = []
        unchanged: list[str] = []
        client = None
        active_server = None
        try:
            for security in securities:
                symbol = str(security["symbol"])
                symbol_start = security.get(
                    "_history_start_date",
                    self.start_date,
                )
                for attempt in range(3):
                    server = self.provider.servers[
                        (batch_number + attempt) % len(self.provider.servers)
                    ]
                    try:
                        if client is None or active_server != server:
                            if client is not None:
                                self.provider._close(client)
                            client = self.provider._client(server)
                            active_server = server
                        raw = daily_bars(client, symbol, symbol_start)
                        bars = normalize_daily_bars(
                            raw,
                            symbol_start,
                            self.end_date,
                        )
                        if not bars:
                            if security.get("_latest_history_date") is not None:
                                unchanged.append(symbol)
                                break
                            finance_frame = client.finance(symbol=symbol)
                            if finance_frame is None or finance_frame.empty:
                                raise RuntimeError("finance response unavailable")
                            finance = finance_frame.to_dict(orient="records")[0]
                            evidence = unlisted_evidence(
                                raw,
                                finance,
                                client.F10(symbol=symbol, name="公司概况"),
                            )
                            if evidence:
                                excluded.append(symbol)
                                break
                            raise RuntimeError("no daily bars in requested range")
                        result[symbol] = bars
                        break
                    except Exception:
                        if client is not None:
                            self.provider._close(client)
                        client = None
                        active_server = None
                        if attempt == 2:
                            failures.append(symbol)
        finally:
            if client is not None:
                self.provider._close(client)
        return result, failures, excluded, unchanged

    def _latest_complete_session(self) -> date:
        cutoff = self.end_date
        now = datetime.now(SHANGHAI)
        if cutoff >= now.date() and now.hour < 15:
            cutoff = now.date() - timedelta(days=1)
        bars = self.provider._first_result(
            lambda client: daily_bars(
                client,
                "000001",
                cutoff - timedelta(days=45),
                index=True,
            )
        )
        sessions = [
            _bar_date(dict(bar))
            for bar in bars
            if _bar_date(dict(bar)) <= cutoff
        ]
        if not sessions:
            raise RuntimeError("rustdx returned no completed trading session")
        return max(sessions)

    def run(self) -> BackfillResult:
        if self.incremental:
            self.end_date = self._latest_complete_session()
        securities = self.provider.securities()
        completed: set[str] = set()
        if self.incremental:
            latest_dates = self.store.latest_history_dates("day")
            pending = []
            for security in securities:
                symbol = str(security["symbol"])
                latest = latest_dates.get(symbol)
                if latest is not None and latest >= self.end_date:
                    completed.add(symbol)
                    continue
                symbol_start = (
                    max(
                        self.start_date,
                        latest - timedelta(days=self.lookback_days),
                    )
                    if latest is not None
                    else self.start_date
                )
                pending.append(
                    {
                        **security,
                        "_history_start_date": symbol_start,
                        "_latest_history_date": latest,
                    }
                )
        else:
            completed = (
                self.store.completed_history_symbols("day")
                if self.resume
                else set()
            )
            pending = [
                security
                for security in securities
                if security["symbol"] not in completed
            ]
        batches = [
            pending[index:index + self.batch_size]
            for index in range(0, len(pending), self.batch_size)
        ]
        completed_count = 0
        row_count = 0
        failures: list[str] = []
        excluded: list[str] = []
        unchanged: list[str] = []
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            pending_batches = iter(enumerate(batches))
            futures = {
                executor.submit(self._fetch_batch, number, batch)
                for number, batch in (
                    next(pending_batches, (None, None))
                    for _ in range(self.workers)
                )
                if number is not None
            }
            while futures:
                done, futures = wait(
                    futures,
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    (
                        bars_by_symbol,
                        failed,
                        skipped_unlisted,
                        unchanged_symbols,
                    ) = future.result()
                    if bars_by_symbol:
                        self.store.upsert_history_bars_batch(
                            bars_by_symbol,
                            "day",
                        )
                        for symbol, bars in bars_by_symbol.items():
                            self.store.mark_history_sync(
                                symbol,
                                "day",
                                row_count=len(bars),
                                completed=True,
                            )
                            completed_count += 1
                            row_count += len(bars)
                    failures.extend(failed)
                    excluded.extend(skipped_unlisted)
                    unchanged.extend(unchanged_symbols)
                    self.progress(
                        {
                            "universe_count": len(securities),
                            "skipped_count": len(completed),
                            "completed_count": completed_count,
                            "failed_count": len(failures),
                            "excluded_count": len(excluded),
                            "unchanged_count": len(unchanged),
                            "row_count": row_count,
                        }
                    )
                    number, batch = next(pending_batches, (None, None))
                    if number is not None:
                        futures.add(
                            executor.submit(
                                self._fetch_batch,
                                number,
                                batch,
                            )
                        )
        return BackfillResult(
            universe_count=len(securities),
            skipped_count=len(completed),
            completed_count=completed_count,
            unchanged_symbols=tuple(sorted(unchanged)),
            excluded_symbols=tuple(sorted(excluded)),
            failed_symbols=tuple(sorted(failures)),
            row_count=row_count,
            start_date=self.start_date,
            end_date=self.end_date,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill current Shanghai/Shenzhen A-share daily bars",
    )
    parser.add_argument("--start-date", type=date.fromisoformat, default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--workers", type=int, default=7)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--lookback-days", type=int, default=14)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    settings = StorageSettings.from_env()
    provider = make_provider(workers=args.workers)
    store = ClickHouseMarketHistoryStore(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        database=settings.clickhouse_database,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        send_receive_timeout=120,
    )
    logger = logging.getLogger("stock-history-backfill")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    def report(value: Mapping[str, Any]) -> None:
        logger.info(
            "progress completed=%s skipped=%s unchanged=%s excluded=%s failed=%s rows=%s universe=%s",
            value["completed_count"],
            value["skipped_count"],
            value["unchanged_count"],
            value["excluded_count"],
            value["failed_count"],
            value["row_count"],
            value["universe_count"],
        )

    try:
        result = StockHistoryBackfill(
            provider=provider,
            store=store,
            start_date=args.start_date,
            end_date=args.end_date,
            batch_size=args.batch_size,
            workers=args.workers,
            resume=not args.no_resume,
            incremental=args.incremental,
            lookback_days=args.lookback_days,
            progress=report,
        ).run()
        logger.info("completed result=%s", result)
        return 1 if result.failed_symbols else 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
