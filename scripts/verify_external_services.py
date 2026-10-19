"""Verify production integrations inside Pods; never retrieve Secret values."""
from __future__ import annotations

import argparse
import json
import subprocess
import textwrap

TOS_CHECK = """
import hashlib, json, uuid
from banxia_strategy.config import RuntimeSettings
from banxia_strategy.service import _postgres
from banxia_strategy.adapters.minio import MinioObjectAssetStore
s = RuntimeSettings.from_env().storage
result = {"credentials_configured": bool(s.minio_access_key and s.minio_secret_key)}
if not verify:
    print(json.dumps(result))
    raise SystemExit(0)
if not result["credentials_configured"]:
    raise RuntimeError("TOS credentials missing")
def read(store, key):
    response = store.client.get_object(store.bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()
stores = []
try:
    for bucket in (s.minio_report_bucket, s.minio_market_bucket):
        store = MinioObjectAssetStore(s.minio_endpoint, s.minio_access_key,
            s.minio_secret_key, bucket=bucket, secure=s.minio_secure, region=s.minio_region)
        stores.append(store)
        if not store.ready():
            raise RuntimeError("bucket access failed")
        content = json.dumps({"purpose": "shanao.asia TOS acceptance"}).encode()
        key = "acceptance/tos/" + uuid.uuid4().hex + ".json"
        asset = store.put(object_key=key, content=content, content_type="application/json")
        if hashlib.sha256(read(store, key)).hexdigest() != asset.content_hash:
            raise RuntimeError("object readback mismatch")
    result["bucket_roundtrips"] = 2
    repository = _postgres(RuntimeSettings.from_env())
    try:
        latest = repository.latest_market_reference_snapshot()
        if latest is None:
            raise RuntimeError("no published reference snapshot")
        with repository.connection_factory() as connection:
            row = connection.execute(
                "SELECT raw_object_key FROM banxia.source_snapshot WHERE snapshot_id=%s",
                (latest["snapshot_id"],),
            ).fetchone()
        manifest = json.loads(read(stores[-1], row[0]))
        for asset in manifest["assets"].values():
            content = read(stores[-1], asset["object_key"])
            if (hashlib.sha256(content).hexdigest() != asset["content_hash"]
                or len(content) != asset["size_bytes"]):
                raise RuntimeError("reference asset readback mismatch")
        result.update(reference_snapshot=latest["snapshot_id"],
            verified_assets=len(manifest["assets"]),
            daily_quote_count=manifest["daily_quote_count"],
            missing_quote_count=manifest["missing_quote_count"])
    finally:
        repository.close()
finally:
    for store in stores:
        store.close()
print(json.dumps(result))
"""

SMTP_CHECK = """
import json, logging
from app.services.email_adapter import get_registration_smtp, send_email, is_valid_email
config, password = get_registration_smtp(recipient)
configured = bool(config.get("host") and config.get("username") and password)
result = {"credentials_configured": configured}
if recipient:
    if not configured or not is_valid_email(recipient):
        raise RuntimeError("SMTP configuration or recipient missing")
    logging.disable(logging.CRITICAL)
    if not send_email(config, password, "shanao.asia SMTP 验收",
        "这是指定收件人的单封验收邮件。用于核验系统邮件传输。", max_attempts=1):
        raise RuntimeError("SMTP did not accept the test message")
    result["smtp_accepted"] = True
print(json.dumps(result))
"""


def execute(kubectl: list[str], deployment: str, executable: str,
            source: str) -> None:
    # Only allowlisted JSON results leave the Pod; exception details may contain credentials.
    source = "try:\n" + textwrap.indent(source, "    ")
    source += "\nexcept Exception as exc:\n    print('verification_failed:', type(exc).__name__)\n    raise SystemExit(1)\n"
    result = subprocess.run(
        [*kubectl, "exec", "-i", f"deployment/{deployment}", "--", executable, "-"],
        input=source, capture_output=True, text=True, timeout=90,
    )
    print(result.stdout.strip() or f"{deployment}: no result")
    if result.returncode:
        raise RuntimeError(f"{deployment}: verification failed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default="banxia")
    parser.add_argument("--context")
    parser.add_argument("--server")
    parser.add_argument("--tos", action="store_true",
                        help="write/read probes in both buckets and verify published reference assets")
    parser.add_argument("--email-to", help="send exactly one test email to this authorized recipient")
    args = parser.parse_args()
    kubectl = ["kubectl", "--request-timeout=60s", "-n", args.namespace]
    if args.context:
        kubectl += ["--context", args.context]
    if args.server:
        kubectl += ["--server", args.server]
    try:
        execute(kubectl, "report-scheduler", "python",
                f"verify = {args.tos!r}\n" + TOS_CHECK)
        execute(kubectl, "lss-product", "/app/.venv/bin/python",
                f"recipient = {json.dumps(args.email_to or '')}\n" + SMTP_CHECK)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
