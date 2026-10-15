"""Compare versioned selection on dated, normalized frames; never report P&L."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from datetime import date
from pathlib import Path

from banxia_strategy.strategy import StrategyEngine
from banxia_strategy.strategy_config import StrategyConfig


def select_frame(frame, config):
    engine = StrategyEngine(None, config)
    diagnostics = {}
    pools = [(date.fromisoformat(item["date"]), item["rows"]) for item in frame["pools"]]
    selected = engine._select(engine._build_candidates(
        frame["rows"], frame["industry_stats"], frame["market"], pools,
        diagnostics=diagnostics,
    ), diagnostics)
    for rank, item in enumerate(selected, 1):
        item.rank = rank
    return {"candidates": [asdict(item) for item in selected], "diagnostics": diagnostics}


def next_close_outcome(snapshot, session, code):
    calendar = sorted(snapshot["calendar"])
    next_session = next((day for day in calendar if day > session), None)
    if not next_session:
        return None
    bars = {str(bar.get("datetime", bar.get("date", "")))[:10]: bar
            for bar in snapshot["histories"].get(code, [])}
    before, after = bars.get(session), bars.get(next_session)
    if not before or not after or not float(before.get("close") or 0):
        return None
    previous_close, close = float(before["close"]), float(after["close"])
    # Main-board, non-ST selection; ordinary 10% limit rounded to the cent.
    limit_price = int(previous_close * 110 + 0.5) / 100
    return {"session": next_session, "previous_close": previous_close, "close": close,
            "close_change_pct": round((close / previous_close - 1) * 100, 4),
            "closed_at_limit": close >= limit_price - 0.005,
            "metric": "selection_next_close_proxy_not_trade_return"}


def aggregate(days, version):
    picks = [item for day in days for item in day[version]["candidates"]]
    outcomes = [item["next_close_outcome"] for item in picks if item.get("next_close_outcome")]
    reason_counts = Counter()
    for day in days:
        reason_counts.update(day[version]["diagnostics"]["reason_counts"])
    hits = sum(item["closed_at_limit"] for item in outcomes)
    tiers = {}
    for position in sorted({item["position_limit_pct"] for item in picks}):
        members = [item for item in picks if item["position_limit_pct"] == position]
        observed = [item["next_close_outcome"] for item in members if item.get("next_close_outcome")]
        tier_hits = sum(item["closed_at_limit"] for item in observed)
        tiers[str(position)] = {
            "candidate_occurrences": len(members), "observed_next_close": len(observed),
            "closed_at_limit": tier_hits,
            "closed_at_limit_pct": round(tier_hits / len(observed) * 100, 2) if observed else None,
        }
    return {
        "sessions": len(days), "candidate_occurrences": len(picks),
        "unique_symbols": len({item["code"] for item in picks}),
        "empty_sessions": sum(not day[version]["candidates"] for day in days),
        "setup_counts": dict(Counter(item["plan"]["setup"] for item in picks)),
        "position_counts": dict(Counter(str(item["position_limit_pct"]) for item in picks)),
        "risk_tiers": tiers,
        "rejection_reasons": dict(reason_counts),
        "next_close_proxy": {
            "observed": len(outcomes), "closed_at_limit": hits,
            "closed_at_limit_pct": round(hits / len(outcomes) * 100, 2) if outcomes else None,
            "mean_close_change_pct": round(sum(item["close_change_pct"] for item in outcomes) / len(outcomes), 4) if outcomes else None,
            "not_trade_return": True,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=Path, required=True,
                        help="Dated normalized rows/pools/market/industry_stats JSON")
    parser.add_argument("--latest", type=Path)
    parser.add_argument("--v2-config", type=Path, default=Path("config/leader-v2.json"))
    parser.add_argument("--outcome-snapshot", type=Path,
                        help="Optional research snapshot with calendar and actual daily histories")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.frames.read_bytes()
    source = json.loads(raw)
    configs = {"v1": StrategyConfig.from_mapping(source["baseline_config"]),
               "v2": StrategyConfig.from_file(args.v2_config)}
    if configs["v1"].leader_rule_version != 1 or configs["v2"].leader_rule_version != 2:
        parser.error("Comparison requires V1 baseline and V2 proposal")
    snapshot = None
    if args.outcome_snapshot:
        snapshot_raw = args.outcome_snapshot.read_bytes()
        if hashlib.sha256(snapshot_raw).hexdigest() != source["input_sha256"]:
            parser.error("Outcome snapshot does not match the dated selection input source")
        snapshot = json.loads(snapshot_raw)
    days = []
    for frame in source["frames"]:
        day = {"as_of": frame["as_of"], "market": frame["market"]}
        for version, config in configs.items():
            day[version] = select_frame(frame, config)
            if snapshot:
                for candidate in day[version]["candidates"]:
                    candidate["next_close_outcome"] = next_close_outcome(
                        snapshot, frame["as_of"], candidate["code"])
        if "baseline_candidates" in frame:
            codes = [item["code"] for item in day["v1"]["candidates"]]
            if codes != frame["baseline_candidates"]:
                raise RuntimeError(f"V1 regression on {frame['as_of']}: {codes}")
        days.append(day)
    result = {
        "purpose": "selection_replay_not_profit_validation",
        "input": {key: source[key] for key in ("source", "input_path", "input_sha256", "quality")},
        "normalized_input_sha256": hashlib.sha256(raw).hexdigest(),
        "configs": {version: asdict(config) for version, config in configs.items()},
        "summary": {version: aggregate(days, version) for version in configs},
        "periods": {
            month: {version: aggregate([day for day in days if day["as_of"].startswith(month)], version)
                    for version in configs}
            for month in sorted({day["as_of"][:7] for day in days})
        },
        "days": days,
    }
    if args.latest:
        latest = json.loads(args.latest.read_bytes())
        result["latest"] = {
            "as_of": latest["as_of"], "source": latest["source"], "market": latest["market"],
            **{version: select_frame(latest, config) for version, config in configs.items()},
        }
    # Full row-level diagnostics stay reproducible in the input. Compact durable
    # output keeps stages/counts/theme decisions and rejected leader-quality cases.
    for day in [*days, *([result["latest"]] if args.latest else [])]:
        for version in configs:
            diag = day[version]["diagnostics"]
            diag["excluded"] = [item for item in diag["excluded"]
                                if item["stage"] in {"quality", "score", "diversification"}]
            diag["excluded_detail_scope"] = "quality_score_diversification"
            diag["theme_decisions"] = {theme: decision for theme, decision in diag["theme_decisions"].items()
                                       if decision["today_count"] > 0}
            diag["theme_detail_scope"] = "themes_with_current_limit_ups"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    if args.latest:
        print(json.dumps({version: [(item["code"], item["name"], item["position_limit_pct"])
                                   for item in result["latest"][version]["candidates"]]
                          for version in configs}, ensure_ascii=False))


if __name__ == "__main__":
    main()
