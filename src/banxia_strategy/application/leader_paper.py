"""Forward-only paper execution for the mainline-leader unified strategy.

The entry path is inherited from :class:`PaperTradingWorker`: the frozen
operational config generates one leader plan per session, and each candidate's
embedded ``plan`` drives the same conservative, minute-proven buy window used by
every paper campaign. Only the exit differs. The unified strategy has no
intraday profit target; it keeps a protective stop and a timed force-exit, and
A-share T+1 forbids selling on the entry day. The exit therefore starts on D3:
a -4% stop is the first touched threshold, and if it is never reached through
14:54 a 14:55 force-exit instruction is proxied by the 14:56 minute price.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

from ..t1_research import cents, minute_label, minute_quality, net_return
from ..leader_execution import exit_fill
from .paper_trading import (
    PaperTradingWorker,
    _daily_bar,
    _minute_data,
    execution_model,
)


LEADER_CAMPAIGN_CODE = "mainline-leader-fusion-v1"
DEFAULT_HARD_STOP_PCT = 4.0
D3_LAST_TRIGGER_MINUTE_INDEX = 233  # 14:54, last minute eligible for a stop
D3_FORCE_EXIT_MINUTE_INDEX = 235  # 14:56 proxy for a 14:55 force-exit instruction


def simulate_t1_protective_exit(
    buy_price: float,
    d3_prices: Sequence[float],
    *,
    hard_stop_pct: float,
) -> tuple[float, str, int]:
    """Return the D3 exit proxy price, reason, and minute index.

    A position opened on D2 cannot be sold until D3. A D3 open already at or
    below the stop exits at the observed first-minute price rather than at the
    unreachable threshold. Later stop crossings use the stop price. If the stop
    is never reached through 14:54, the 14:56 minute price proxies a force-exit
    instruction sent at 14:55. ``hard_stop_pct`` is a percentage (e.g. 4.0).
    """
    if buy_price <= 0:
        raise ValueError("buy_price must be positive")
    if hard_stop_pct <= 0:
        raise ValueError("hard_stop_pct must be positive")
    if len(d3_prices) <= D3_FORCE_EXIT_MINUTE_INDEX:
        raise ValueError("D3 minute prices must include the 14:56 sample")
    if any(price <= 0 for price in d3_prices):
        raise ValueError("D3 minute prices must be positive")

    stop_loss = buy_price * (1.0 - hard_stop_pct / 100.0)
    first = float(d3_prices[0])
    if first <= stop_loss:
        return first, "t1_d3_stop_loss", 0

    previous = first
    for index in range(1, D3_LAST_TRIGGER_MINUTE_INDEX + 1):
        current = float(d3_prices[index])
        if min(previous, current) <= stop_loss:
            return stop_loss, "t1_d3_stop_loss", index
        previous = current

    return (
        float(d3_prices[D3_FORCE_EXIT_MINUTE_INDEX]),
        "t1_d3_force_exit",
        D3_FORCE_EXIT_MINUTE_INDEX,
    )


class MainlineLeaderPaperTradingWorker(PaperTradingWorker):
    def __init__(self, **kwargs: Any):
        kwargs.setdefault("campaign_code", LEADER_CAMPAIGN_CODE)
        super().__init__(**kwargs)

    def _settle_exits(
        self,
        campaign: Mapping[str, Any],
        target_date: date,
    ) -> int:
        if int(campaign["config_snapshot"].get("leader_execution_version", 1)) >= 2:
            return self._settle_causal_exits(campaign, target_date)
        hard_stop_pct = float(
            campaign["config_snapshot"].get(
                "hard_stop_pct",
                DEFAULT_HARD_STOP_PCT,
            )
        )
        model = execution_model(0.0)
        records = self.repository.list_paper_pending_exits(
            campaign["campaign_id"],
            target_date,
        )
        processed = 0
        for record in records:
            exit_date = date.fromisoformat(record["exit_date"])
            minute = _minute_data(
                self.provider.historical_minutes(record["symbol"], exit_date)
            )
            sell_bar = _daily_bar(self.provider, record["symbol"], exit_date)
            quality = (
                minute_quality(minute, sell_bar)
                if minute is not None and sell_bar is not None
                else "missing"
            )
            if quality != "ok":
                self.repository.save_paper_exit(
                    record["paper_trade_id"],
                    {
                        "status": "exit_data_missing",
                        "exit_reason": f"D3 行情质量检查失败：{quality}",
                        "exit_evidence": {},
                    },
                )
                processed += 1
                continue
            buy_price = float(record["entry_price"])
            exit_price_raw, reason, minute_index = simulate_t1_protective_exit(
                buy_price,
                minute["prices"],
                hard_stop_pct=hard_stop_pct,
            )
            sell_price = cents(
                exit_price_raw * (1 - model["slippage_each_side"]),
                up=False,
            )
            shares = int(record["shares"])
            net_return_pct = net_return(buy_price, sell_price, shares, model)
            self.repository.save_paper_exit(
                record["paper_trade_id"],
                {
                    "status": "closed",
                    "exit_time": minute_label(minute_index),
                    "exit_price": sell_price,
                    "exit_reason": reason,
                    "net_return_pct": net_return_pct,
                    "positive": net_return_pct > 0,
                    "exit_evidence": {
                        "exit_reason": reason,
                        "minute_index": minute_index,
                        "hard_stop_pct": hard_stop_pct,
                        "d3_open": minute["prices"][0],
                        "exit_day_close": sell_bar["close"],
                        "exit_date": exit_date.isoformat(),
                        "t1_compliant": True,
                    },
                },
            )
            processed += 1
        return processed

    def _settle_causal_exits(self, campaign, target_date):
        """Version 2 retries carried positions without settling unfilled losses."""
        sessions = sorted(self.provider.trading_dates())
        plan = campaign["config_snapshot"]
        model = execution_model(0)
        records = self.repository.list_paper_pending_exits(campaign["campaign_id"], target_date)
        for record in records:
            pending, closed, evidence = False, False, []
            for session in sessions:
                if not record["exit_date"] <= session.isoformat() <= target_date.isoformat():
                    continue
                minute = _minute_data(self.provider.historical_minutes(record["symbol"], session))
                bar = _daily_bar(self.provider, record["symbol"], session)
                previous = next((day for day in reversed(sessions) if day < session), None)
                prior_bar = _daily_bar(self.provider, record["symbol"], previous) if previous else None
                quality = minute_quality(minute, bar) if minute and bar else "missing"
                if quality != "ok" or not prior_bar:
                    evidence.append({"date": session.isoformat(), "quality": quality})
                    # The timed instruction has expired by session end. Retry
                    # its liquidation on the next observable legal session.
                    pending = True
                    continue
                lower = cents(float(prior_bar["close"]) * .9)
                outcome = exit_fill(
                    minute["prices"], minute["volumes"],
                    buy_price=float(record["entry_price"]), shares=int(record["shares"]),
                    lower=lower, model=model, entry_date=record["entry_date"],
                    trade_date=session.isoformat(), opening=float(bar["open"]), plan=plan,
                    pending=pending,
                )
                evidence.append({"date": session.isoformat(), **outcome})
                pending = outcome["pending"]
                if outcome["status"] != "filled_proxy":
                    continue
                value = net_return(float(record["entry_price"]), outcome["price"], int(record["shares"]), model)
                self.repository.save_paper_exit(record["paper_trade_id"], {
                    "status": "closed", "exit_date": session.isoformat(),
                    "exit_time": minute_label(outcome["index"]), "exit_price": outcome["price"],
                    "exit_reason": outcome["reason"], "net_return_pct": value, "positive": value > 0,
                    "exit_evidence": {"leader_execution_version": 2, "attempts": evidence, "t1_compliant": True},
                })
                closed = True
                break
            if not closed:
                self.repository.save_paper_exit(record["paper_trade_id"], {
                    "status": "exit_data_missing",
                    "exit_reason": "保留未退出仓位并重试，未计入已结算样本",
                    "exit_evidence": {"leader_execution_version": 2, "attempts": evidence},
                })
        return len(records)
