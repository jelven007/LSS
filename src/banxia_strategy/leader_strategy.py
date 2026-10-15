"""Leader identity precedes quality filters; each candidate has one lifecycle setup."""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def leader_order(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    themes: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if int(row.get("board_count") or 0) <= 0:
            continue
        if "ST" in str(row.get("name", "")).upper() or "退" in str(row.get("name", "")):
            continue
        theme = str(row.get("industry") or "其他")
        themes.setdefault(theme, []).append(row)
    for members in themes.values():
        members.sort(key=lambda row: (
            -int(row["board_count"]),
            row.get("first_seal_minutes") if row.get("first_seal_minutes") is not None else 1440,
            -float(row.get("amount") or 0),
            str(row["code"]),
        ))
    return themes


def exclude(diagnostics, row, stage, reason, **details):
    if diagnostics is not None:
        diagnostics.setdefault("excluded", []).append({
            "code": row["code"], "industry": row["industry"],
            "stage": stage, "reason": reason, **details,
        })


def _market_gate(market, config):
    if not market.get("broken_board_data_available"):
        return "closed", "missing_broken_board_data"
    score = float(market.get("score") or 0)
    if config.leader_rule_version == 1:
        return (("regular", "") if score >= config.leader_minimum_market_score
                else ("closed", "market_score_below_regular"))
    break_rate = market.get("break_rate_pct")
    if break_rate is None:
        return "closed", "missing_break_rate"
    if float(break_rate) >= config.leader_defense_break_rate_pct:
        return "closed", "defense_break_rate"
    if score >= config.leader_minimum_market_score:
        return "regular", ""
    if (score >= config.leader_trial_minimum_market_score
            and float(break_rate) <= config.leader_trial_maximum_break_rate_pct
            and int(market.get("max_board") or 0) >= config.leader_trial_minimum_market_height):
        return "trial", ""
    return "closed", "market_trial_conditions_failed"


def _theme_gate(stats, config):
    if stats["today_count"] < config.minimum_industry_limit_up_count:
        return "", "insufficient_theme_limit_ups"
    if stats["active_days"] < config.leader_minimum_active_days:
        if (config.leader_rule_version >= 2 and stats["active_days"] == 1
                and stats["today_count"] >= config.leader_emerging_minimum_limit_ups
                and stats["momentum"] > 0):
            return "emerging", ""
        return "", "insufficient_theme_active_days"
    if stats["momentum"] >= 0:
        return "established", ""
    # momentum is today's count minus the prior lookback average, not yesterday.
    prior_average = stats["today_count"] - stats["momentum"]
    if (config.leader_rule_version >= 2 and stats["max_board"] >= 2
            and prior_average > 0
            and stats["today_count"] / prior_average >= config.leader_contraction_minimum_ratio):
        return "divergence", ""
    return "", "theme_contraction_too_severe"


def leader_setups(rows, pools, industry_stats, market, config, diagnostics=None):
    """Return identities from the entire universe, never from filtered candidates."""
    market_mode, market_reason = _market_gate(market, config)
    if diagnostics is not None:
        diagnostics.update(rule_version=config.leader_rule_version, market_mode=market_mode,
                           market_reason=market_reason, excluded=[], observations=[], theme_decisions={})
    if market_mode == "closed":
        for row in rows:
            exclude(diagnostics, row, "market", market_reason)
        return {}
    decisions = {}
    themes = []
    for theme, stats in industry_stats.items():
        stage, reason = _theme_gate(stats, config) if theme != "其他" else ("", "unknown_theme")
        decisions[theme] = {"stage": stage, "reason": reason,
                            **{key: stats[key] for key in ("today_count", "active_days", "momentum", "max_board")}}
        if stage:
            themes.append((theme, stats))
    themes.sort(key=lambda item: (
        -item[1]["today_count"], -item[1]["momentum"], -item[1]["max_board"], item[0],
    ))
    mainlines = {theme: rank for rank, (theme, _) in enumerate(themes, 1)
                 if rank <= config.leader_maximum_theme_rank}
    for rank, (theme, _) in enumerate(themes, 1):
        decisions[theme].update(rank=rank)
        if theme not in mainlines:
            decisions[theme]["reason"] = "theme_rank_outside_limit"
    if diagnostics is not None:
        diagnostics["theme_decisions"] = decisions
    current = leader_order(rows)
    historical = {session.isoformat(): leader_order(members) for session, members in pools}
    result = {}
    for row in rows:
        theme, code = row["industry"], row["code"]
        if theme not in mainlines:
            exclude(diagnostics, row, "theme", decisions.get(theme, {}).get("reason") or "unknown_theme")
            if config.leader_core_only and diagnostics is not None:
                diagnostics["observations"].append({
                    "code": code, "industry": theme, "reason": "theme_rank_outside_limit",
                })
            continue
        if theme not in current:
            exclude(diagnostics, row, "identity", "no_current_theme_leader")
            continue
        front = current[theme][0]
        boards = row["board_count"]
        evidence = {
            "theme_rank": mainlines[theme],
            "current_leader_code": front["code"],
            "current_theme_height": front["board_count"],
            "identity_method": "完整题材池：连板高度→首封时间→成交额",
        }
        if boards > 0:
            if front["code"] != code:
                exclude(diagnostics, row, "identity", "not_theme_leader", leader_code=front["code"])
                continue
            if market_mode == "trial" and boards > config.leader_trial_maximum_boards:
                exclude(diagnostics, row, "setup", "trial_high_board_excluded")
                continue
            evidence["leader_rank"] = 1
            if boards == 1:
                seal = row.get("first_seal_minutes")
                cutoff = config.leader_start_maximum_seal_time
                maximum = int(cutoff[:2]) * 60 + int(cutoff[3:])
                if seal is None or seal > maximum or not (
                    config.leader_start_minimum_turnover_pct <= row["turnover"]
                    <= config.leader_start_maximum_turnover_pct
                ):
                    exclude(diagnostics, row, "setup", "start_seal_or_turnover_failed")
                    continue
                setup, label, mode = "leader_start", "主线先锋一进二", "board_reseal"
            else:
                setup, label, mode = (
                    ("leader_reseal", "龙头分歧回封", "board_reseal")
                    if row["break_count"] > 0 else
                    ("leader_relay", "主线龙头接力", "board_reseal")
                )
        else:
            if config.leader_rule_version >= 2 and (
                    market_mode == "trial" or decisions[theme]["stage"] != "established"):
                exclude(diagnostics, row, "setup", "repair_requires_regular_established_theme")
                continue
            days = row.get("days_since_limit_up")
            prior_date = row.get("recent_limit_date")
            prior = historical.get(prior_date, {}).get(theme, [])
            if (
                not isinstance(days, int)
                or not 1 <= days <= config.leader_maximum_repair_days
                or not prior
                or prior[0]["code"] != code
                or prior[0]["board_count"] < config.leader_minimum_prior_boards
                or front["board_count"] >= prior[0]["board_count"]
            ):
                exclude(diagnostics, row, "identity", "prior_leader_or_repair_window_failed")
                continue
            evidence.update(
                prior_leader_date=prior_date,
                prior_leader_code=code,
                prior_leader_height=prior[0]["board_count"],
                prior_leader_rank=1,
            )
            if days == 1 and row["is_bearish"]:
                setup, label, mode = "leader_first_yin", "龙头首阴修复", "reclaim_open"
            elif row["reversal_confirmed"]:
                setup, label, mode = "leader_reversal", "龙头断板反包", "breakout"
            else:
                exclude(diagnostics, row, "setup", "repair_confirmation_missing")
                continue
        if config.leader_rule_version >= 2:
            theme_stage = decisions[theme]["stage"]
            risk_reason = (
                "market_trial" if market_mode == "trial"
                else theme_stage if theme_stage in {"emerging", "divergence"}
                else "startup_trial" if setup == "leader_start"
                else "extended_theme_rank" if mainlines[theme] > config.leader_core_maximum_theme_rank
                else "regular"
            )
            trial_risk = risk_reason != "regular"
            evidence.update(rule_version=config.leader_rule_version, market_mode=market_mode, theme_stage=theme_stage)
            evidence["risk_reason"] = risk_reason
            evidence["position_limit_pct"] = min(
                config.position_limit_pct,
                config.leader_trial_position_limit_pct if trial_risk else config.position_limit_pct,
            )
            if config.leader_core_only and trial_risk:
                exclude(diagnostics, row, "core", "observation_only", risk_reason=risk_reason)
                if diagnostics is not None:
                    diagnostics["observations"].append({
                        "code": code, "industry": theme, "setup": setup,
                        "risk_reason": risk_reason, "leader_evidence": evidence,
                    })
                continue
        result[code] = {"setup": setup, "label": label, "entry_mode": mode,
                        "leader_evidence": evidence}
    return result
