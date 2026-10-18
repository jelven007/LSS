"""Prepare private local state from a verified, immutable public release."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path


def write_env(path: Path, values: dict[str, str]) -> None:
    # Generated secrets use URL-safe characters and require no dotenv escaping.
    content = "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"
    path.write_text(content)
    path.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-release", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=3051)
    parser.add_argument("--source-commit", help="Exact source commit when preparing from a container")
    parser.add_argument("--resume", action="store_true", help="Resume an interrupted copy before credentials exist")
    args = parser.parse_args()
    source = args.public_release.resolve(strict=True)
    state = args.state_dir.resolve()
    if state.exists() and (not args.resume or list(state.glob("*.env"))):
        raise SystemExit("State directory already exists; reuse its compose.env.")
    manifest = json.loads((source / ".public_release.json").read_text())
    release_id = manifest["id"]
    if len(release_id) != 32 or any(c not in "0123456789abcdef" for c in release_id):
        raise ValueError("Invalid public release ID")
    checksums = manifest["checksums"]
    print(f"Verifying {len(checksums)} public files", flush=True)
    for name, expected in checksums.items():
        path = (source / name).resolve(strict=True)
        if not path.is_relative_to(source) or path.is_symlink():
            raise ValueError("Public file escaped its release")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Public checksum mismatch: {name}")

    os.umask(0o077)
    state.mkdir(parents=True, exist_ok=True)
    target = state / "public" / "releases" / release_id
    target.mkdir(parents=True, exist_ok=True)
    # Copy only the manifest's public allowlist; never copy private platform state.
    for index, (name, expected) in enumerate(checksums.items()):
        for root in (target, state / "platform-data"):
            dest = root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.is_file() and hashlib.sha256(dest.read_bytes()).hexdigest() == expected:
                continue
            shutil.copy2(source / name, dest)
        if index % 250 == 0:
            print(f"Copied {index + 1}/{len(checksums)} public files", flush=True)
    shutil.copy2(source / ".public_release.json", target / ".public_release.json")
    current = state / "public" / "current"
    if not current.is_symlink():
        current.symlink_to(Path("releases") / release_id)
    for name in ("platform-config", "tenant-artifacts"):
        (state / name).mkdir(exist_ok=True)
    (state / "platform-config" / "preferences.json").write_text(json.dumps({
        "onboarding_completed": True,
        "realtime_data_provider": "rustdx",
    }))
    # Runtime containers use UID 10001, not the host user's UID.
    for path in state.rglob("*"):
        if not path.is_symlink():
            path.chmod(0o777 if path.is_dir() else 0o666)
    state.chmod(0o755)
    passwords = {name: secrets.token_hex(24) for name in (
        "postgres", "control", "data", "redis", "clickhouse", "object",
        "platform_market", "platform_admin", "stream",
    )}
    commit = args.source_commit or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    compose = {
        "LEAN_STATE_DIR": str(state),
        "LEAN_WEB_PORT": str(args.port),
        "POLICY_IMAGE": "banxia-strategy:lean-cleanup",
        "TSP_RUNTIME_IMAGE": "tsp-product-runtime:lean-cleanup",
        "TSP_COMPUTE_IMAGE": "tsp-product-compute:lean-cleanup",
        "POSTGRES_USER": "banxia",
        "POSTGRES_DB": "banxia",
        "POSTGRES_PASSWORD": passwords["postgres"],
        "POSTGRES_PORT": "35432",
        "CLICKHOUSE_HTTP_PORT": "38123",
        "CLICKHOUSE_PASSWORD": passwords["clickhouse"],
        "REDIS_PORT": "36379",
        "REDIS_PASSWORD": passwords["redis"],
        "MINIO_API_PORT": "39002",
        "MINIO_CONSOLE_PORT": "39001",
        "MINIO_ROOT_PASSWORD": passwords["object"],
        "TSP_CONTROL_RUNTIME_PASSWORD": passwords["control"],
        "TSP_DATA_RUNTIME_PASSWORD": passwords["data"],
        "BANXIA_CODE_COMMIT": commit,
    }
    write_env(state / "compose.env", compose)
    write_env(state / "admin.env", {
        "TSP_CONTROL_ADMIN_DSN": f"postgresql://banxia:{passwords['postgres']}@postgres/tsp_control",
        "TSP_DATA_ADMIN_DSN": f"postgresql://banxia:{passwords['postgres']}@postgres/tsp_data_local",
    })
    platform = {
        "TSP_PLATFORM_MARKET_TOKEN": passwords["platform_market"],
        "TSP_PLATFORM_ADMIN_TOKEN": passwords["platform_admin"],
    }
    write_env(state / "platform.env", platform)
    write_env(state / "runtime.env", {
        **platform,
        "TSP_CELL_CONTROL_DSN": f"postgresql://tsp_control_runtime:{passwords['control']}@postgres/tsp_control",
        "TSP_CELL_DATA_DSN": f"postgresql://tsp_data_runtime:{passwords['data']}@postgres/tsp_data_local",
        "TSP_CELL_MARKET_TOKEN": passwords["platform_market"],
        "TSP_CELL_STREAM_TICKET_SECRET": passwords["stream"],
    })
    write_env(state / "policy.env", {
        "BANXIA_ENVIRONMENT": "local-lean-test",
        "BANXIA_DATA_PROVIDER": "rustdx",
        "BANXIA_STORAGE_MODE": "required",
        "BANXIA_POSTGRES_DSN": f"postgresql://banxia:{passwords['postgres']}@postgres/banxia",
        "BANXIA_CLICKHOUSE_HOST": "clickhouse",
        "BANXIA_CLICKHOUSE_PORT": "8123",
        "BANXIA_CLICKHOUSE_DATABASE": "banxia",
        "BANXIA_CLICKHOUSE_USER": "banxia",
        "BANXIA_CLICKHOUSE_PASSWORD": passwords["clickhouse"],
        "BANXIA_REDIS_URL": f"redis://:{passwords['redis']}@redis:6379/0",
        "BANXIA_OBJECT_ENDPOINT": "minio:9000",
        "BANXIA_OBJECT_ACCESS_KEY": "banxia",
        "BANXIA_OBJECT_SECRET_KEY": passwords["object"],
        "BANXIA_OBJECT_SECURE": "false",
        "BANXIA_OBJECT_REPORT_BUCKET": "strategy-reports",
        "BANXIA_OBJECT_MARKET_BUCKET": "market-raw",
        "BANXIA_STRATEGY_CONFIG": "/app/config/strategy.json",
        "BANXIA_REPORT_DIRS": "/app/reports",
        "BANXIA_CODE_COMMIT": commit,
    })
    print(json.dumps({"state_dir": str(state), "release_id": release_id,
                      "verified_files": len(checksums), "web_port": args.port}))


if __name__ == "__main__":
    main()
