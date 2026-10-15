"""Causal minute execution for V2.1; prices/volume prove conditional fills only."""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .t1_research import cash_paid, cents, minute_label


def cutoff_index(clock: str) -> int:
    hour, minute = map(int, clock.split(":"))
    absolute = hour * 60 + minute
    return absolute - 571 if absolute <= 690 else 120 + absolute - 781


def volume_ratio(volumes: Sequence[float], index: int):
    if index < 5:
        return None
    previous = volumes[index - 5:index]
    baseline = sum(previous) / 5
    return volumes[index] / baseline if baseline > 0 else None


def entry_signal(prices, volumes, reference, upper, opening, plan):
    """Consume completed minute prefixes; no outcome or future sample is read."""
    result: dict[str, Any] = {"confirmed": False, "reason_code": "no_strength_confirmation"}
    if (not prices or len(prices) != len(volumes)
            or not math.isfinite(reference) or reference <= 0
            or any(not math.isfinite(p) or p <= 0 for p in prices)
            or any(not math.isfinite(v) or v < 0 for v in volumes)):
        return {**result, "reason_code": "invalid_minute"}
    opening_pct = 100 * (opening / reference - 1)
    if not plan["open_min_pct"] - 1e-7 <= opening_pct <= plan["open_max_pct"] + 1e-7:
        return {**result, "reason_code": "auction_outside"}
    if 100 * (prices[0] / reference - 1) < plan.get("minimum_first_minute_change_pct", -20):
        return {**result, "reason_code": "first_minute_weak"}
    mode = plan.get("entry_mode", "board_reseal")
    touched, opened, previous_board, breaks, high = False, False, False, 0, None
    for index in range(min(len(prices), cutoff_index(plan.get("entry_cutoff_time", "10:00")))):
        price = prices[index]
        if volumes[index] <= 0:
            continue
        if plan.get("reject_below_previous_close", True) and price < reference - .001:
            return {**result, "reason_code": "breached_reference"}
        board = price >= upper - .001
        if previous_board and not board:
            breaks += 1
        if breaks > plan.get("manual_max_intraday_breaks", 1):
            return {**result, "reason_code": "too_many_breaks"}
        ratio = volume_ratio(volumes, index)
        strength = (ratio or 0) >= plan.get("minimum_minute_volume_ratio", 1.5)
        condition = False
        if mode == "board_reseal":
            if touched and not opened and not board and volumes[index] > 0:
                opened, high = True, price
            elif opened:
                condition = price >= high + .009 and strength
                if condition:
                    result["post_break_prior_high"] = high
                high = max(high, price)
            if board and volumes[index] > 0:
                if not touched:
                    result["first_touch_index"] = index
                touched = True
        else:
            target = (max(reference, opening) if mode == "reclaim_open"
                      else plan.get("trigger_price", reference) if mode == "breakout"
                      else reference * (1 + plan.get("trigger_change_pct", 5) / 100))
            condition = index > 0 and prices[index - 1] < target - .001 and price >= target - .001 and strength
        previous_board = board
        if condition:
            return {
                **result, "confirmed": True, "reason_code": "strength_confirmed",
                "confirmation_index": index, "confirmation_time": minute_label(index),
                "minute_volume_ratio": ratio, "break_count": breaks,
                "confirmation_price": price,
            }
    return result


def usable_buy_indices(prices, volumes, reference, upper, plan, start):
    """A pending order expires at cutoff, a breach or excess observed breaks."""
    previous_board, breaks = False, 0
    for index in range(min(len(prices), cutoff_index(plan.get("entry_cutoff_time", "10:00")))):
        price = prices[index]
        if volumes[index] <= 0:
            continue
        board = price >= upper - .001
        if previous_board and not board:
            breaks += 1
        previous_board = board
        if (plan.get("reject_below_previous_close", True) and price < reference - .001
                or breaks > plan.get("manual_max_intraday_breaks", 1)):
            return
        if index >= start and volumes[index] > 0 and price < upper - .001:
            yield index


def buy_fill(prices, volumes, reference, upper, plan, signal, budget, model):
    """Size at the signal's declared limit; never resize using future volume."""
    limit = cents(upper - .01)
    shares = math.floor(budget / limit / 100) * 100
    while shares > 0 and cash_paid(limit, shares, model) > budget:
        shares -= 100
    if shares < 100:
        return {"status": "below_one_lot"}
    start = signal["confirmation_index"] + 1
    for index in usable_buy_indices(prices, volumes, reference, upper, plan, start):
        price = cents(prices[index] * (1 + model["slippage_each_side"]), up=True)
        capacity = volumes[index] * 100 * model["maximum_participation_in_observed_minute_volume"]
        if price <= limit + 1e-7 and shares <= capacity:
            return {
                "status": "filled_proxy", "index": index, "price": price,
                "sample_price": prices[index], "shares": shares, "order_limit": limit,
                "participation_pct": shares / (volumes[index] * 100) * 100,
                "confirmation_index": signal["confirmation_index"],
            }
    return {"status": "no_capacity_or_price_fill", "requested_shares": shares, "order_limit": limit}


def exit_fill(prices, volumes, *, buy_price, shares, lower, model,
              entry_date, trade_date, opening, plan, pending=False):
    """Trigger at completed closes, then sell at a later liquid non-limit minute.

    Weakness is predeclared: 09:35–09:59 close below both entry cost and
    today's daily open. The -4% protective stop is shared by both exit modes.
    A failed order stays pending on subsequent sessions; D2 cannot be sold.
    """
    if trade_date <= entry_date:
        return {"status": "t1_locked", "pending": pending}
    force = cutoff_index(plan.get("next_day_force_exit_time", "14:55"))
    reason = "deferred_exit" if pending else None
    trigger = -1 if pending else None
    for index, price in enumerate(prices):
        if (pending and index > max(0, trigger)
                and price > lower + .001
                and shares <= volumes[index] * 100 * model["maximum_participation_in_observed_minute_volume"]):
            fill = max(lower, math.floor(price * (1 - model["slippage_each_side"]) * 100 + 1e-8) / 100)
            return {
                "status": "filled_proxy", "index": index, "price": fill,
                "sample_price": price, "reason": reason, "trigger_index": trigger,
                "participation_pct": shares / (volumes[index] * 100) * 100,
                "pending": False,
            }
        if not pending:
            if volumes[index] > 0 and price <= buy_price * (1 - plan.get("hard_stop_pct", 4) / 100):
                reason = "protective_stop"
            elif (plan.get("next_day_exit_mode") == "protective_weak"
                  and volumes[index] > 0 and 4 <= index < 29 and price < min(buy_price, opening) - .001):
                reason = "early_weakness"
            elif index >= force:
                reason = "timed_exit"
            if reason:
                pending, trigger = True, index
    return {"status": "unfilled", "pending": pending, "reason": reason, "trigger_index": trigger}
