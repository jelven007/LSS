"""Interactively patch TOS/SMTP configuration without exporting existing Secrets."""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import subprocess
import sys
from urllib.parse import urlsplit


def prompt(label: str, default: str = "", *, secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    value = (getpass.getpass(f"{label}: ") if secret
             else input(f"{label}{suffix}: ")).strip()
    value = value or default
    if not value or value.startswith("REPLACE_"):
        raise ValueError(f"{label} is required")
    return value


def tos_config() -> tuple[dict[str, str], dict[str, str]]:
    endpoint = prompt("TOS S3 endpoint", "tos-s3-cn-shanghai.ivolces.com")
    parsed = urlsplit(f"https://{endpoint}")
    if "://" in endpoint or parsed.path or parsed.query or parsed.username:
        raise ValueError("endpoint must be a hostname without scheme or path")
    config = {
        "BANXIA_OBJECT_ENDPOINT": endpoint,
        "BANXIA_OBJECT_REGION": prompt("TOS region", "cn-shanghai"),
        "BANXIA_OBJECT_SECURE": "true",
        "BANXIA_OBJECT_MARKET_BUCKET": prompt("Market bucket", "market-raw"),
        "BANXIA_OBJECT_REPORT_BUCKET": prompt("Report bucket", "strategy-reports"),
    }
    credentials = {
        "BANXIA_OBJECT_ACCESS_KEY": prompt("TOS access key", secret=True),
        "BANXIA_OBJECT_SECRET_KEY": prompt("TOS secret key", secret=True),
    }
    return config, credentials


def smtp_config() -> dict[str, str]:
    host = prompt("SMTP host")
    if any(c in host for c in "/@ \t\r\n"):
        raise ValueError("SMTP host must be a hostname")
    port = prompt("SMTP port", "465")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("invalid SMTP port")
    security = prompt("SMTP security", "starttls" if port == "587" else "ssl")
    if security not in {"ssl", "starttls"}:
        raise ValueError("SMTP requires ssl or starttls")
    username = prompt("SMTP username")
    sender = prompt("SMTP sender", username)
    if sender.count("@") != 1 or any(c in sender for c in " \r\n"):
        raise ValueError("invalid SMTP sender")
    return {
        "AUTH_SMTP_HOST": host, "AUTH_SMTP_PORT": port,
        "AUTH_SMTP_SECURITY": security, "AUTH_SMTP_USERNAME": username,
        "AUTH_SMTP_PASSWORD": prompt("SMTP authorization password", secret=True),
        "AUTH_SMTP_FROM_ADDRESS": sender,
    }


def patch(kubectl: list[str], kind: str, name: str,
          values: dict[str, str]) -> None:
    # Secret values travel over stdin, never in command arguments or printed output.
    data = ({key: base64.b64encode(value.encode()).decode()
             for key, value in values.items()} if kind == "secret" else values)
    result = subprocess.run(
        [*kubectl, "patch", kind, name, "--type=merge", "--patch-file=/dev/stdin"],
        input=json.dumps({"data": data}), capture_output=True, text=True, timeout=40,
    )
    if result.returncode:
        raise RuntimeError(f"patch failed: {kind}/{name}; inspect cluster access")
    print(f"Updated {kind}/{name}; existing keys preserved.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default="banxia")
    parser.add_argument("--context")
    parser.add_argument("--server")
    parser.add_argument("--tos", action="store_true")
    parser.add_argument("--smtp", action="store_true")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error("run interactively in a terminal; credentials must not be echoed or piped")
    kubectl = ["kubectl", "--request-timeout=30s", "-n", args.namespace]
    if args.context:
        kubectl += ["--context", args.context]
    if args.server:
        kubectl += ["--server", args.server]
    use_tos, use_smtp = args.tos or not args.smtp, args.smtp or not args.tos
    try:
        # Validate every input before changing any resource.
        config, credentials = tos_config() if use_tos else ({}, {})
        smtp = smtp_config() if use_smtp else {}
        if credentials:
            patch(kubectl, "secret", "banxia-secrets", credentials)
            patch(kubectl, "configmap", "banxia-runtime", config)
        if smtp:
            patch(kubectl, "secret", "tsp-product-secrets", smtp)
        workloads = (["deployment/tsp-product"] +
                     (["deployment/market-reference-sync", "deployment/report-scheduler",
                       "statefulset/market-collector"] if use_tos else []) +
                     (["deployment/tsp-task-worker"] if use_smtp else []))
        for workload in workloads:
            result = subprocess.run([*kubectl, "rollout", "restart", workload],
                                    capture_output=True, text=True, timeout=40)
            if result.returncode:
                raise RuntimeError(f"restart failed: {workload}; configuration is already saved")
            print(f"Restart requested: {workload}")
        print("Configuration saved. Verify rollout and real delivery before signing off.")
    except (ValueError, RuntimeError, subprocess.TimeoutExpired, EOFError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
