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

import math
from datetime import date
from typing import Any, Mapping, Optional, Sequence

from ..t1_research import cash_paid, cents, minute_label, minute_quality, net_return
from ..leader_execution import entry_signal, exit_fill
from ..execution_analysis import classify_candidate
from ..intraday import plan_for
from ..market_provider import _limit_price
from .paper_trading import (
    PaperTradingWorker,
    _daily_bar,
    _minute_data,
    _next_session,
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

    def _evaluate_entries(self, campaign, target_date, sessions):
        if int(campaign["config_snapshot"].get("leader_execution_version", 1)) >= 2:
            return self._evaluate_causal_entries(campaign, target_date, sessions)
        return super()._evaluate_entries(campaign, target_date, sessions)

    def _sector_confirmation(self, candidate, reference_date, entry_date, index):
        """Dated D1 limit-up peers read at the signal minute; no future data.

        Peers come from the frozen D1 limit-up pool, restricted to the same
        theme, main-board, non-ST names holding at least one board. Each peer's
        rise is judged by its own D2 minute close at the confirmation index.
        """
        try:
            pool = self.provider.limit_up_pool(date.fromisoformat(reference_date))
        except Exception:
            pool = []
        theme = candidate.get("industry")
        peers = []
        for row in pool:
            code = str(row.get("代码") or row.get("code") or "")
            name = str(row.get("名称") or row.get("name") or "")
            if (row.get("所属行业", row.get("industry")) != theme
                    or int(row.get("连板数", row.get("board_count") or 0)) < 1
                    or not code.startswith(("00", "60"))
                    or "ST" in name.upper() or "退" in name):
                continue
            reference = float(row.get("最新价") or row.get("latest_price") or 0)
            if reference <= 0:
                continue
            minute = _minute_data(self.provider.historical_minutes(code, entry_date))
            bar = _daily_bar(self.provider, code, entry_date)
            if minute is None or bar is None or minute_quality(minute, bar) != "ok":
                continue
            if minute["volumes"][index] <= 0:
                continue
            peers.append({"code": code, "reference": reference,
                          "price": minute["prices"][index]})
        rising = sum(peer["price"] > peer["reference"] for peer in peers)
        return {
            "minute_index": index, "sample_size": len(peers), "peers": peers,
            "rise_ratio": rising / len(peers) if peers else None,
        }

    def _evaluate_causal_entries(self, campaign, target_date, sessions):
        """V2.1 forward entry: strength signal, dated sector gate, proven fill."""
        records = self.repository.list_paper_entry_candidates(
            campaign["campaign_id"], target_date)
        model = execution_model(0.0)
        processed = 0
        for record in records:
            candidate = record["candidate"]
            symbol = str(candidate["code"])
            entry_date = date.fromisoformat(record["entry_date"])
            minute = _minute_data(self.provider.historical_minutes(symbol, entry_date))
            bar = _daily_bar(self.provider, symbol, entry_date)
            base = {
                "plan_id": record["plan_id"], "symbol": symbol,
                "name": str(candidate["name"]), "industry": candidate.get("industry"),
                "reference_date": record["reference_date"], "entry_date": record["entry_date"],
            }
            quality = (minute_quality(minute, bar)
                       if minute is not None and bar is not None else "missing")
            if quality != "ok":
                self.repository.save_paper_entry(campaign["campaign_id"], {
                    **base, "status": "entry_data_missing",
                    "rejection_reason": f"D2 行情质量检查失败：{quality}"})
                processed += 1
                continue
            plan = candidate.get("plan") or plan_for(candidate)
            reference = float(candidate["latest_price"])
            upper = _limit_price(reference, str(candidate["name"]))
            signal = entry_signal(minute["prices"], minute["volumes"], reference,
                                  upper, float(bar["open"]), plan)
            sector = (self._sector_confirmation(
                candidate, record["reference_date"], entry_date, signal["confirmation_index"])
                if signal["confirmed"] else None)
            classified = classify_candidate(
                {"code": symbol, "name": candidate["name"], "industry": candidate.get("industry"),
                 "latest_price": reference, "plan": plan,
                 "sector_confirmation": sector}, record["entry_date"], bar, minute)
            if not classified["buyable"]:
                self.repository.save_paper_entry(campaign["campaign_id"], {
                    **base, "status": "rejected",
                    "rejection_reason": classified["reason"],
                    "entry_evidence": {**classified, "sector_confirmation": sector}})
                processed += 1
                continue
            buy_index = int(classified["buy_index"])
            sample_price = float(minute["prices"][buy_index])
            buy_price = cents(sample_price * (1 + model["slippage_each_side"]), up=True)
            position_cny = model["position_cny"]
            shares = math.floor(position_cny / buy_price / model["lot_shares"]) * model["lot_shares"]
            while shares > 0 and cash_paid(buy_price, shares, model) > position_cny:
                shares -= model["lot_shares"]
            capacity = (minute["volumes"][buy_index] * 100
                        * model["maximum_participation_in_observed_minute_volume"])
            if shares < model["minimum_shares_by_board"]["main"]:
                rejection = "1 万元模拟预算不足一手"
            elif shares > capacity:
                rejection = "模拟委托超过该分钟成交量的 1%"
            else:
                rejection = None
            if rejection:
                self.repository.save_paper_entry(campaign["campaign_id"], {
                    **base, "status": "rejected", "rejection_reason": rejection,
                    "entry_evidence": {**classified, "sector_confirmation": sector,
                                       "capacity_shares": capacity,
                                       "requested_shares": shares}})
                processed += 1
                continue
            exit_date = _next_session(sessions, entry_date)
            if exit_date is None:
                self.repository.save_paper_entry(campaign["campaign_id"], {
                    **base, "status": "entry_data_missing",
                    "rejection_reason": "缺少 D3 交易日", "entry_evidence": classified})
                processed += 1
                continue
            self.repository.save_paper_entry(campaign["campaign_id"], {
                **base, "exit_date": exit_date.isoformat(), "status": "open",
                "entry_time": classified["buy_window_time"], "entry_price": buy_price,
                "entry_sample_price": sample_price, "shares": shares,
                "target_price": buy_price,
                "entry_evidence": {**classified, "sector_confirmation": sector,
                                   "entry_day_close": bar["close"], "position_cny": position_cny,
                                   "capacity_shares": capacity, "leader_execution_version": 2,
                                   "execution_model": model}})
            processed += 1
        return processed
