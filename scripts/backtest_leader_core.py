"""Reproduce the frozen V2.1 experiment; never search a parameter grid."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

from banxia_strategy.application.paper_trading import execution_model
from banxia_strategy.leader_backtest import replay
from banxia_strategy.strategy_config import StrategyConfig


def read(path):
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    return json.loads(raw), raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--execution-input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("docs/data/leader-v21-net-20261015"))
    args = parser.parse_args()
    frames, frames_raw = read(args.frames)
    data, data_raw = read(args.execution_input)
    root = Path(__file__).resolve().parents[1]
    core = StrategyConfig.from_file(root / "config/strategy.json")
    v2 = StrategyConfig.from_file(root / "config/leader-v2.json")
    v1 = StrategyConfig.from_mapping(frames["baseline_config"])
    model = execution_model(0)
    variants = {
        "v1": v1,
        "v2": v2,
        "core_legacy_entry": replace(core, leader_execution_version=1),
        "core_legacy_early_weak_exit": replace(core, leader_execution_version=1, next_day_exit_mode="protective_weak"),
        "core_strength_entry": core,
        "core_early_weak_exit": replace(core, next_day_exit_mode="protective_weak"),
        "v1_common_risk": replace(v1, position_limit_pct=10, portfolio_risk_limit_pct=20),
        "v2_common_risk": replace(v2, position_limit_pct=10, portfolio_risk_limit_pct=20),
    }
    results = {}
    for name, config in variants.items():
        results[name] = {"config": asdict(config), **replay(frames["frames"], data, config, model)}
        print(json.dumps({"variant": name, **results[name]["summary"]}, ensure_ascii=False), flush=True)
    stress = {**model, "slippage_each_side": .002}
    results["core_slippage_stress"] = {
        "config": asdict(core), **replay(frames["frames"], data, core, stress)}
    output = {
        "purpose": "retrospective_development_portfolio_net_return_not_holdout",
        "source": {
            "provider": data["source"], "collected_at": data["collected_at"],
            "original_selection_snapshot_sha256": frames["input_sha256"],
            "normalized_frames_sha256": hashlib.sha256(frames_raw).hexdigest(),
            "execution_input_sha256": hashlib.sha256(data_raw).hexdigest(),
            "requested_minute_pairs": len(data["requested_pairs"]), "retrieved_minute_pairs": len(data["minutes"]),
            "daily_failures": data["daily_failures"], "sector_scope": data["sector_scope"],
            "selection_quality": frames["quality"],
        },
        "limitations": [
            "Previously inspected 33 sessions are development data, not untouched out-of-sample data.",
            "Minute closes and volumes are conditional fills; no intraminute path, queues or broker executions.",
            "D1 identity is frozen. Intraday displacement of leaders cannot be fully verified by a D1 limit-up peer subset.",
            "Themes, float capital and surviving security universe retain original point-in-time/survivorship limits.",
            "Historical daily/minute consistency is an ex-post data-quality audit, not an available intraday daily close.",
            "First qualifying signal is attempted once; sector gate uses only same-minute D1-declared peers.",
            "Unfilled holdings remain marked at last observable prices; stale marks are counted explicitly.",
            "Minute-close drawdown excludes intraminute extremes and auction execution.",
        ],
        "results": results,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    # Store compact, lossless evidence for reruns without another provider fetch.
    (args.output / "execution-input.json.gz").write_bytes(gzip.compress(data_raw, mtime=0))
    (args.output / "selection-frames.json.gz").write_bytes(gzip.compress(frames_raw, mtime=0))
    fields = [
        "variant", "code", "name", "entry_date", "confirmation_time", "entry_time",
        "price", "shares", "paid", "exit_date", "exit_time", "sale_price", "received",
        "pnl_cny", "net_return_pct", "exit_reason", "buy_participation_pct", "sell_participation_pct",
    ]
    with (args.output / "trades.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for name, result in results.items():
            for trade in result["trades"]:
                writer.writerow({"variant": name, **trade})
    print(json.dumps({"output": str(args.output.resolve()),
                      "stress": results["core_slippage_stress"]["summary"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
