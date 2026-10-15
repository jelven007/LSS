"""Fee-inclusive chronological portfolio replay with conditional minute fills."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date
from statistics import mean

from .execution_analysis import _daily_bar, classify_candidate
from .leader_execution import buy_fill, cutoff_index, entry_signal, exit_fill
from .market_provider import _limit_price
from .strategy import StrategyEngine
from .t1_research import cash_paid, cash_received, cents, minute_label, minute_quality


def quality(data, symbol, session):
    minute = data["minutes"].get(f"{session}:{symbol}")
    bar = _daily_bar(data, symbol, session)
    return minute_quality(minute, bar) if minute and bar else "missing"


def sector_evidence(frame, data, candidate, session, index):
    """Predeclared D1 limit-up peers; only contemporaneous D2 closes are read."""
    peers = []
    for row in frame["rows"]:
        code = row["code"]
        if (row["industry"] != candidate.industry or row["board_count"] < 1
                or not code.startswith(("00", "60"))
                or "ST" in row["name"].upper() or "退" in row["name"]):
            continue
        if quality(data, code, session) != "ok":
            continue
        minute = data["minutes"][f"{session}:{code}"]
        if minute["volumes"][index] <= 0:
            continue
        peers.append({
            "code": code, "reference": row["latest_price"],
            "price": minute["prices"][index],
        })
    return {
        "minute_index": index, "sample_size": len(peers), "peers": peers,
        "rise_ratio": sum(row["price"] > row["reference"] for row in peers) / len(peers) if peers else None,
    }


def prepare_signal(candidate, bar, minute):
    plan, reference = candidate.plan, candidate.latest_price
    upper = _limit_price(reference, candidate.name)
    if plan.get("leader_execution_version", 1) >= 2:
        return entry_signal(minute["prices"], minute["volumes"], reference, upper, bar["open"], plan)
    classified = classify_candidate(
        {"code": candidate.code, "name": candidate.name, "latest_price": reference, "plan": plan},
        "", bar, minute,
    )
    if not classified["buyable"]:
        return {"confirmed": False, "reason_code": classified["reason_code"]}
    if plan["entry_mode"] == "board_reseal":
        # Legacy comparison preserves the touch signal, but applies the same
        # subsequent-minute fill, sizing, capacity and breach constraints.
        signal_index = next(index for index, p in enumerate(minute["prices"]) if p >= upper - .001)
    else:
        # Old classifiers filled on the confirmation itself; all comparison
        # variants deliberately use a subsequent sample instead.
        signal_index = next(index for index in range(240) if minute_label(index) == classified["confirmation_time"])
    return {"confirmed": True, "confirmation_index": signal_index, "reason_code": "legacy_confirmation"}


def replay(frames, data, config, model, *, initial_cash=100_000):
    """Reserve budgets when signals arrive; execute fills strictly in time order.

    Sale proceeds are usable from the next session (a conservative research
    convention; real A-share proceeds can usually be reinvested on the sale day).
    Positions and unsold losses are never dropped. No D2 selling is possible.
    """
    calendar = sorted(data["calendar"])
    candidates_by_day = {}
    all_attempts, selections = [], []
    for frame in frames:
        day = next(session for session in calendar if session > frame["as_of"])
        engine = StrategyEngine(None, config)
        pools = [(date.fromisoformat(item["date"]), item["rows"]) for item in frame["pools"]]
        diagnostics = {}
        picks = engine._select(engine._build_candidates(
            frame["rows"], frame["industry_stats"], frame["market"], pools, diagnostics=diagnostics), diagnostics)
        for candidate in picks:
            # The comparison imposes one causal exit protocol on old/new
            # selections; it never mutates a stored historical campaign.
            candidate.plan["next_day_exit_mode"] = config.next_day_exit_mode
        candidates_by_day[day] = (frame, picks)
        selections.append({"as_of": frame["as_of"], "entry_date": day, "codes": [c.code for c in picks]})
    first = min(candidates_by_day)
    last_entry = max(candidates_by_day)
    exit_horizon = int(data.get("maximum_exit_sessions", 5))
    tail = [session for session in calendar if session > last_entry][:exit_horizon]
    last = tail[-1] if tail else last_entry
    days = [session for session in calendar if first <= session <= last]
    cash, held, settlement = float(initial_cash), {}, []
    trades, daily_curve, peak, maximum_drawdown = [], [], float(initial_cash), 0.0
    total_costs, mtm_missing = 0.0, set()

    def mark(session, index):
        value = 0.0
        for code, position in held.items():
            if quality(data, code, session) == "ok":
                price = data["minutes"][f"{session}:{code}"]["prices"][index]
            else:
                prior = [b for b in data["histories"].get(code, []) if str(b["datetime"])[:10] < session]
                price = float(prior[-1]["close"]) if prior else position["price"]
                mtm_missing.add(f"{session}:{code}")
            position["mark"] = price
            value += price * position["shares"]
        return value

    for session in days:
        due = [item for item in settlement if item["available_on"] <= session]
        cash += sum(item["amount"] for item in due)
        settlement = [item for item in settlement if item not in due]
        exits, signals, fills, reservations = defaultdict(list), defaultdict(list), defaultdict(list), {}
        for code, position in held.items():
            if session <= position["entry_date"]:
                continue
            if quality(data, code, session) != "ok":
                position["pending"] = True
                position.setdefault("exit_attempts", []).append({"date": session, "status": "missing"})
                continue
            previous = calendar[calendar.index(session) - 1]
            bar, prior = _daily_bar(data, code, session), _daily_bar(data, code, previous)
            if not prior:
                position["pending"] = True
                position.setdefault("exit_attempts", []).append({"date": session, "status": "missing_previous_close"})
                continue
            minute = data["minutes"][f"{session}:{code}"]
            result = exit_fill(
                minute["prices"], minute["volumes"], buy_price=position["price"], shares=position["shares"],
                lower=cents(prior["close"] * .9), model=model, entry_date=position["entry_date"],
                trade_date=session, opening=bar["open"], plan=position["plan"], pending=position.get("pending", False),
            )
            position.setdefault("exit_attempts", []).append({"date": session, **result})
            position["pending"] = result["pending"]
            if result["status"] == "filled_proxy":
                exits[result["index"]].append((code, result))

        frame, picks = candidates_by_day.get(session, (None, []))
        for candidate in picks:
            attempt = {
                "reference_date": frame["as_of"], "entry_date": session, "code": candidate.code,
                "name": candidate.name, "score": candidate.score, "industry": candidate.industry,
                "setup": candidate.plan["setup"], "risk_reason": candidate.plan.get("risk_reason", "v1"),
                "status": "unconfirmed", "plan": candidate.plan,
            }
            all_attempts.append(attempt)
            attempt["quality"] = quality(data, candidate.code, session)
            if attempt["quality"] != "ok":
                attempt.update(status="data_missing")
                continue
            bar = _daily_bar(data, candidate.code, session)
            prior = _daily_bar(data, candidate.code, frame["as_of"])
            if not prior or abs(prior["close"] - candidate.latest_price) > .011:
                attempt.update(status="reference_mismatch")
                continue
            minute = data["minutes"][f"{session}:{candidate.code}"]
            signal = prepare_signal(candidate, bar, minute)
            attempt["signal"] = signal
            if not signal["confirmed"]:
                attempt.update(status=signal["reason_code"])
                continue
            index = signal["confirmation_index"]
            sector = sector_evidence(frame, data, candidate, session, index)
            attempt["sector"] = sector
            if (sector["sample_size"] < candidate.plan.get("minimum_sector_sample_size", 2)
                    or sector["rise_ratio"] < candidate.plan.get("minimum_sector_rise_ratio", .5)):
                attempt.update(status="sector_missing_or_weak")
                continue
            signals[index].append((candidate, signal, attempt))

        for index in range(240):
            for code, result in exits[index]:
                position = held.pop(code)
                proceeds = cash_received(result["price"], position["shares"], model)
                available = next((d for d in calendar if d > session), "9999-12-31")
                settlement.append({"amount": proceeds, "available_on": available})
                pnl = proceeds - position["paid"]
                total_costs += position["shares"] * result["sample_price"] - proceeds
                trades.append({
                    **{k: v for k, v in position.items() if k not in ("pending", "mark", "plan")},
                    "code": code, "exit_date": session, "exit_time": minute_label(index),
                    "sale_price": result["price"], "sale_sample_price": result["sample_price"],
                    "exit_reason": result["reason"], "received": proceeds, "pnl_cny": pnl,
                    "net_return_pct": 100 * pnl / position["paid"], "sell_participation_pct": result["participation_pct"],
                })
            market_value = mark(session, index)
            equity = cash + sum(x["amount"] for x in settlement) + market_value
            for candidate, signal, attempt in sorted(signals[index], key=lambda item: item[0].rank):
                if candidate.code in held or candidate.code in reservations:
                    attempt.update(status="already_held_or_pending")
                    continue
                reserved = sum(reservations.values())
                budget = min(
                    equity * candidate.position_limit_pct / 100,
                    max(0, equity * config.portfolio_risk_limit_pct / 100 - market_value - reserved),
                    max(0, cash - reserved),
                )
                minute = data["minutes"][f"{session}:{candidate.code}"]
                fill = buy_fill(minute["prices"], minute["volumes"], candidate.latest_price,
                                _limit_price(candidate.latest_price, candidate.name), candidate.plan,
                                signal, budget, model)
                attempt.update(status=fill["status"], order=fill, budget_cny=budget)
                reservations[candidate.code] = budget
                if fill["status"] == "filled_proxy":
                    fills[fill["index"]].append((candidate, fill, attempt))
            for candidate, fill, attempt in fills[index]:
                reservations.pop(candidate.code, None)
                paid = cash_paid(fill["price"], fill["shares"], model)
                cash -= paid
                assert cash >= -.001, "cash overdraw"
                total_costs += paid - fill["shares"] * fill["sample_price"]
                held[candidate.code] = {
                    "entry_date": session, "entry_time": minute_label(index), "price": fill["price"],
                    "sample_price": fill["sample_price"], "shares": fill["shares"], "paid": paid,
                    "plan": candidate.plan, "name": candidate.name,
                    "buy_participation_pct": fill["participation_pct"],
                    "confirmation_time": minute_label(attempt["signal"]["confirmation_index"]),
                }
            if index >= cutoff_index(config.entry_cutoff_time):
                reservations.clear()
            market_value = mark(session, index)
            equity = cash + sum(x["amount"] for x in settlement) + market_value
            peak = max(peak, equity)
            maximum_drawdown = max(maximum_drawdown, 100 * (1 - equity / peak))
        daily_curve.append({
            "date": session, "cash": round(cash, 4), "unsettled_cash": round(sum(x["amount"] for x in settlement), 4),
            "market_value": round(market_value, 4), "equity": round(equity, 4),
            "positions": {code: {"shares": p["shares"], "mark": p["mark"]} for code, p in held.items()},
        })

    filled = sum(row["status"] == "filled_proxy" for row in all_attempts)
    returns = [row["net_return_pct"] for row in trades]
    equity = cash + sum(x["amount"] for x in settlement) + sum(p["mark"] * p["shares"] for p in held.values())
    terminal_liquidation = cash + sum(x["amount"] for x in settlement) + sum(
        cash_received(cents(p["mark"] * (1 - model["slippage_each_side"])), p["shares"], model) for p in held.values())
    return {
        "summary": {
            "candidates": len(all_attempts), "empty_selection_days": sum(not row["codes"] for row in selections),
            "complete_entry_data": sum(row["quality"] == "ok" for row in all_attempts),
            "fills": filled, "closed_trades": len(trades), "open_positions": len(held),
            "net_profit_cny": round(equity - initial_cash, 4),
            "portfolio_net_return_pct": round(100 * (equity / initial_cash - 1), 6),
            "terminal_liquidation_net_return_pct": round(100 * (terminal_liquidation / initial_cash - 1), 6),
            "mean_closed_net_return_pct": round(mean(returns), 6) if returns else None,
            "win_rate_pct": 100 * sum(value > 0 for value in returns) / len(returns) if returns else None,
            "worst_closed_net_return_pct": min(returns) if returns else None,
            "maximum_minute_close_drawdown_pct": round(maximum_drawdown, 6),
            "fees_and_slippage_cny": round(total_costs, 4), "mark_data_missing_pairs": len(mtm_missing),
            "status_counts": dict(Counter(row["status"] for row in all_attempts)),
        },
        "initial_cash": initial_cash, "first_entry_session": first, "valuation_date": last,
        "model": model, "cash_reuse": "Sale proceeds available next trading session (conservative)",
        "selection_days": selections, "attempts": all_attempts, "trades": trades,
        "daily_equity": daily_curve, "open_positions": held, "mark_data_missing": sorted(mtm_missing),
    }
