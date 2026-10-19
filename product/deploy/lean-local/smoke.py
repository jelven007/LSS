"""Verify the isolated API without persisting passwords, cookies or SSE tickets."""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3061")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exercise-date", help="Run a small backtest and refresh this closed trading day")
    parser.add_argument("--strategy-id", default="n_day_low_reversal")
    args = parser.parse_args()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    cookie = ""
    results = []

    def request(path, body=None, authenticated=True):
        headers = {"Accept": "application/json", "Origin": args.url.rstrip("/")}
        if cookie and authenticated:
            headers["Cookie"] = cookie
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(args.url.rstrip("/") + path, data=data, headers=headers)
        try:
            response = opener.open(req, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response), response.headers

    def record(name, passed, details=None):
        result = {"check": name, "passed": bool(passed), "details": details}
        results.append(result)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result, ensure_ascii=False), flush=True)

    status, payload, _ = request("/health/live", authenticated=False)
    record("version", status == 200 and payload.get("version") == "0.5.1", payload)
    if not results[-1]["passed"]:
        raise SystemExit("The selected endpoint is not v0.5.1.")
    status, payload, _ = request("/health/ready", authenticated=False)
    record("readiness", status == 200 and payload.get("ready") is True, payload)
    status, _, _ = request("/api/business/bootstrap", authenticated=False)
    record("authentication_required", status == 401, {"status": status})
    status, payload, headers = request("/api/auth/login", {
        "identifier": "admin",
        "password": os.environ.get("LEAN_TEST_PASSWORD", "Admin123"),
    }, authenticated=False)
    record("login", status == 200 and payload.get("authenticated") is True, {"status": status})
    if not results[-1]["passed"]:
        raise SystemExit("Test administrator login failed.")
    # Explicit cookie forwarding allows this loopback-only HTTP acceptance test
    # to exercise the production Secure cookie without relaxing server policy.
    cookie = headers["Set-Cookie"].split(";", 1)[0]
    status, payload, _ = request("/api/business/bootstrap")
    menus = payload.get("menus", [])
    record("business_bootstrap", status == 200 and len(menus) == 9,
           {"version": payload.get("version"), "menus": menus})
    for path in ("/api/policy/reports", "/api/policy/monitor"):
        status, payload, _ = request(path)
        record(path, status == 200, {"status": status, "keys": sorted(payload) if isinstance(payload, dict) else []})
    status, payload, _ = request("/api/events/ticket", {})
    record("sse_ticket", status == 200 and bool(payload.get("ticket")),
           {"status": status, "fields": sorted(payload)})
    if status == 200:
        stream_url = args.url.rstrip("/") + payload["stream"]
        with opener.open(stream_url, timeout=25) as response:
            frame = []
            for line in response:
                text = line.decode().strip()
                frame.append(text)
                if not text:
                    break
            record("sse_subscription", response.status == 200 and
                   "text/event-stream" in response.headers.get("Content-Type", "") and
                   any(line.startswith(("event:", ":")) for line in frame),
                   {"frame_types": [line.split(":", 1)[0] or "heartbeat" for line in frame if line]})
        status, _, _ = request(payload["stream"], authenticated=False)
        record("sse_ticket_single_use", status == 401, {"status": status})
    if args.exercise_date:
        query = urllib.parse.urlencode({
            "strategy_id": args.strategy_id,
            "symbols": "000001.SZ,600519.SH",
            "start": args.exercise_date[:7] + "-01",
            "end": args.exercise_date,
        })
        req = urllib.request.Request(
            args.url.rstrip("/") + "/api/backtest/strategy/stream?" + query,
            headers={"Cookie": cookie, "Origin": args.url.rstrip("/")},
        )
        job_id = None
        with opener.open(req, timeout=30) as response:
            event = ""
            for line in response:
                text = line.decode().strip()
                if text.startswith("event:"):
                    event = text[6:].strip()
                if event == "job" and text.startswith("data:"):
                    job_id = json.loads(text[5:])["id"]
                    break
        record("backtest_submitted", bool(job_id), {"job_id": job_id})
        if job_id:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                status, payload, _ = request("/api/backtest/jobs/" + job_id)
                if payload.get("state") not in {"queued", "running", "cancelling"}:
                    break
                time.sleep(2)
            record("backtest_completed", status == 200 and payload.get("state") == "succeeded",
                   {"job_id": job_id, "state": payload.get("state"), "error": payload.get("error"),
                    "result_keys": sorted(payload.get("result") or {})})
        status, payload, _ = request("/api/policy/reports/" + args.exercise_date + "/refresh", {})
        report_id = payload.get("job_id")
        record("report_submitted", status == 202 and bool(report_id),
               {"status": status, "job_id": report_id, "detail": payload.get("detail")})
        if report_id:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                status, payload, _ = request("/api/policy/report-jobs/" + report_id)
                if payload.get("status") not in {"queued", "running"}:
                    break
                time.sleep(2)
            record("report_completed", status == 200 and payload.get("status") == "succeeded",
                   {"job_id": report_id, "status": payload.get("status"),
                    "attempt": payload.get("attempt"), "error": payload.get("error_message")})
    if not all(result["passed"] for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
