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


def leader_setups(rows, pools, industry_stats, market, config):
    """Return identities from the entire universe, never from filtered candidates."""
    if (
        float(market.get("score") or 0) < config.leader_minimum_market_score
        or not market.get("broken_board_data_available")
    ):
        return {}
    themes = [
        (theme, stats) for theme, stats in industry_stats.items()
        if theme != "其他"
        and stats["today_count"] >= config.minimum_industry_limit_up_count
        and stats["active_days"] >= config.leader_minimum_active_days
        and stats["momentum"] >= 0
    ]
    themes.sort(key=lambda item: (
        -item[1]["today_count"], -item[1]["momentum"], -item[1]["max_board"], item[0],
    ))
    mainlines = {theme: rank for rank, (theme, _) in enumerate(themes, 1)
                 if rank <= config.leader_maximum_theme_rank}
    current = leader_order(rows)
    historical = {session.isoformat(): leader_order(members) for session, members in pools}
    result = {}
    for row in rows:
        theme, code = row["industry"], row["code"]
        if theme not in mainlines or theme not in current:
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
                    continue
                setup, label, mode = "leader_start", "主线先锋一进二", "board_reseal"
            else:
                setup, label, mode = (
                    ("leader_reseal", "龙头分歧回封", "board_reseal")
                    if row["break_count"] > 0 else
                    ("leader_relay", "主线龙头接力", "board_reseal")
                )
        else:
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
                continue
        result[code] = {"setup": setup, "label": label, "entry_mode": mode,
                        "leader_evidence": evidence}
    return result
