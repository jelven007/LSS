"""Leader V2 ablations and execution-data audit; no fabricated trade returns."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, replace
from pathlib import Path

from banxia_strategy.strategy import StrategyEngine
from banxia_strategy.strategy_config import StrategyConfig
from compare_leader_versions import aggregate, next_close_outcome
from datetime import date


def select(frame, config, core_only=False):
    engine = StrategyEngine(None, config)
    pools = [(date.fromisoformat(item["date"]), item["rows"]) for item in frame["pools"]]
    diagnostics = {}
    candidates = engine._build_candidates(
        frame["rows"], frame["industry_stats"], frame["market"], pools,
        diagnostics=diagnostics)
    if core_only:
        # Restrict already-proven identities; never rank a filtered follower up.
        candidates = [item for item in candidates if item.plan.get("risk_reason") == "regular"]
    candidates = engine._select(candidates)
    return [asdict(item) for item in candidates]


def bucket_metrics(picks):
    outcomes = [item["next_close_outcome"] for item in picks if item["next_close_outcome"]]
    hits = sum(item["closed_at_limit"] for item in outcomes)
    return {
        "candidate_occurrences": len(picks),
        "next_close_observed": len(outcomes),
        "closed_at_limit": hits,
        "closed_at_limit_pct": round(100 * hits / len(outcomes), 2) if outcomes else None,
        "mean_close_change_pct": round(sum(item["close_change_pct"] for item in outcomes) / len(outcomes), 4) if outcomes else None,
        "metric": "selection_proxy_not_trade_return",
    }


def attribution(days):
    groups = defaultdict(list)
    for day in days:
        for candidate in day["candidates"]:
            evidence = candidate["plan"]["leader_evidence"]
            score_bucket = "<60" if candidate["score"] < 60 else "60..70" if candidate["score"] < 70 else ">=70"
            market_bucket = "<48" if day["market"]["score"] < 48 else "48..72" if day["market"]["score"] < 72 else ">=72"
            dimensions = {
                "risk_reason": candidate["plan"]["risk_reason"],
                "theme_rank": str(evidence["theme_rank"]),
                "theme_stage": evidence["theme_stage"],
                "setup": candidate["plan"]["setup"],
                "score": score_bucket,
                "market_score": market_bucket,
                "turnover_pct": "<=18" if candidate["turnover_pct"] <= 18 else ">18",
                "first_seal_time": "<=10:30" if candidate["first_seal_time"] <= "10:30" else ">10:30",
            }
            for dimension, value in dimensions.items():
                groups[(dimension, value)].append(candidate)
    result = defaultdict(dict)
    for (dimension, value), picks in groups.items():
        result[dimension][value] = bucket_metrics(picks)
    return dict(result)


def execution_coverage(days, snapshot):
    calendar = sorted(snapshot["calendar"])
    rows = []
    for day in days:
        subsequent = [session for session in calendar if session > day["as_of"]][:2]
        for candidate in day["candidates"]:
            row = {"reference_date": day["as_of"], "code": candidate["code"]}
            row["entry_date"] = subsequent[0] if subsequent else None
            row["exit_date"] = subsequent[1] if len(subsequent) > 1 else None
            for label, session in (("d2", row["entry_date"]), ("d3", row["exit_date"])):
                values = snapshot.get("minutes", {}).get(f"{session}:{candidate['code']}", [])
                # Existing research snapshot stores prices alone. Volume must be
                # explicitly observed; daily volume cannot be spread over minutes.
                prices = values.get("prices", []) if isinstance(values, dict) else values
                volumes = values.get("volumes", []) if isinstance(values, dict) else []
                row[f"{label}_prices_complete"] = len(prices) == 240
                row[f"{label}_volume_complete"] = len(volumes) == 240
            row["execution_data_complete"] = all(row[key] for key in (
                "d2_prices_complete", "d2_volume_complete",
                "d3_prices_complete", "d3_volume_complete"))
            rows.append(row)
    return {
        "candidate_occurrences": len(rows),
        "d2_prices_complete": sum(row["d2_prices_complete"] for row in rows),
        "d3_prices_complete": sum(row["d3_prices_complete"] for row in rows),
        "both_price_sessions_complete": sum(row["d2_prices_complete"] and row["d3_prices_complete"] for row in rows),
        "both_price_and_volume_sessions_complete": sum(row["execution_data_complete"] for row in rows),
        "net_return": None,
        "reason": "Minute volume is absent; fills, capacity, exits and net P&L cannot be validated",
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--v2-config", type=Path, default=Path("config/leader-v2.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    frames_raw, snapshot_raw = args.frames.read_bytes(), args.snapshot.read_bytes()
    frames = json.loads(frames_raw)
    digest = hashlib.sha256(snapshot_raw).hexdigest()
    if digest != frames["input_sha256"]:
        parser.error("Source snapshot hash mismatch")
    snapshot = json.loads(snapshot_raw)
    del snapshot_raw
    v1 = StrategyConfig.from_mapping(frames["baseline_config"])
    v2 = StrategyConfig.from_file(args.v2_config)
    profiles = {
        "v1": (v1, False, "Frozen V1 comparison"),
        "v2": (v2, False, "Original V2 expanded observation"),
        "theme_rank_2": (replace(v2, leader_maximum_theme_rank=2), False, "Only change theme rank 3 to 2"),
        "minimum_score_50": (replace(v2, minimum_score=50), False, "Only change score 45 to 50"),
        "minimum_score_60": (replace(v2, minimum_score=60), False, "Only change score 45 to 60"),
        "disable_weak_market": (replace(v2, leader_trial_minimum_market_score=48), False, "Close the 40..48 market trial range"),
        "original_start_rules": (replace(v2,
            leader_start_maximum_seal_time="10:30",
            leader_start_minimum_turnover_pct=3, leader_start_maximum_turnover_pct=15,
            leader_start_first_minute_pct=6.5, leader_start_entry_cutoff="09:43",
            leader_start_open_min_pct=0.5), False, "Restore V1 startup rules; entry profitability remains unevaluated"),
        "disable_contraction": (replace(v2, leader_contraction_minimum_ratio=1), False, "Remove negative-momentum theme admission; ranks are recalculated"),
        "core_only": (v2, True, "Only preselection candidates with risk_reason=regular"),
        "profit_hypothesis": (replace(v2, leader_maximum_theme_rank=2,
            minimum_score=60, leader_trial_minimum_market_score=48,
            position_limit_pct=10, portfolio_risk_limit_pct=20), True,
            "Research proposal: top-two core only; score>=60; 10%/20% risk caps"),
    }
    results = {}
    for name, (config, core_only, rationale) in profiles.items():
        dated = []
        for frame in frames["frames"]:
            picks = select(frame, config, core_only)
            if name == "v1" and [item["code"] for item in picks] != frame["baseline_candidates"]:
                raise RuntimeError(f"V1 regression on {frame['as_of']}")
            for candidate in picks:
                candidate["next_close_outcome"] = next_close_outcome(
                    snapshot, frame["as_of"], candidate["code"])
            dated.append({"as_of": frame["as_of"], "market": frame["market"], "candidates": picks})
        # Aggregation reuses the established proxy definition; counts are kept
        # separate from any actual trading performance.
        aggregate_input = [{**day, "profile": {"candidates": day["candidates"],
                                             "diagnostics": {"reason_counts": {}}}} for day in dated]
        summary = aggregate(aggregate_input, "profile")
        # This analysis attributes selected candidates, not universe exclusions.
        summary.pop("rejection_reasons")
        results[name] = {
            "rationale": rationale,
            "strategy_config": asdict(config),
            "candidate_policy": {"allowed_risk_reasons": ["regular"]} if core_only else {},
            "summary": summary,
            "periods": {month: bucket_metrics([item for day in dated if day["as_of"].startswith(month)
                                               for item in day["candidates"]])
                        for month in ("2026-08", "2026-09")},
            "days": dated,
        }
    output = {
        "purpose": "selection_ablation_and_execution_coverage_not_profit_backtest",
        "source": {"snapshot": str(args.snapshot), "snapshot_sha256": digest,
                   "normalized_frames_sha256": hashlib.sha256(frames_raw).hexdigest(),
                   "quality": frames["quality"]},
        "statistical_scope": "Retrospective, previously inspected dates; monthly slices are not untouched holdouts",
        "variants": results,
        "v2_attribution": attribution(results["v2"]["days"]),
        "execution_coverage": execution_coverage(results["v2"]["days"], snapshot),
        "deployment": "Research only; no operational config or frozen campaign changed",
    }
    # Save evidence rather than repeating prose and full entry plans per variant.
    for variant in results.values():
        for day in variant["days"]:
            day["candidates"] = [{
                key: item[key] for key in ("code", "name", "industry", "score",
                                           "position_limit_pct", "next_close_outcome")
            } | {"setup": item["plan"]["setup"],
                 "risk_reason": item["plan"].get("risk_reason", "v1")}
                for item in day["candidates"]]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({name: {"summary": value["summary"],
                            "periods": value["periods"]} for name, value in results.items()},
                     ensure_ascii=False, indent=2))
    print(json.dumps({key: value for key, value in output["execution_coverage"].items() if key != "rows"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
