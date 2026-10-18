"""Stream prepared public data into Docker volumes before the first startup."""
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

FINALIZE = """
import hashlib, json, os
from pathlib import Path

root = Path("/state/public/current").resolve(strict=True)
manifest = json.loads((root / ".public_release.json").read_text())
for name, expected in manifest["checksums"].items():
    for directory in (root, Path("/state/platform-data")):
        path = (directory / name).resolve(strict=True)
        if not path.is_relative_to(directory):
            raise ValueError("Public file escaped its release")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError("Seed checksum mismatch: " + name)
for name in ("public", "platform-data", "platform-config"):
    directory = Path("/state") / name
    for path in [directory, *directory.rglob("*")]:
        os.chown(path, 10001, 10001, follow_symlinks=False)
    (directory / ".lean-seed-complete").touch()
print(json.dumps({"verified_files": len(manifest["checksums"]), "seeded": True}))
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--finalize-only", action="store_true",
                        help="Verify and finalize a completed archive transfer")
    args = parser.parse_args()
    state = args.state_dir.resolve(strict=True)
    compose = [
        "docker", "compose", "--env-file", str(state / "compose.env"),
        "-f", str(Path(__file__).with_name("compose.yml").resolve()),
    ]
    if not args.finalize_only:
        # Seeding is a first-start operation. Never replace live or seeded data.
        existing = subprocess.check_output(
            [*compose, "ps", "-a", "-q", "platform"], text=True,
        ).strip()
        if existing:
            running = subprocess.check_output(
                ["docker", "inspect", "--format", "{{.State.Running}}", existing], text=True,
            ).strip()
            if running == "true":
                raise SystemExit("Stop this test platform before seeding its volumes.")
        subprocess.run([*compose, "create", "state-bootstrap"], check=True)
        container = subprocess.check_output(
            [*compose, "ps", "-a", "-q", "state-bootstrap"], text=True,
        ).strip()
        if not container:
            raise SystemExit("The seed container was not created.")
        running = subprocess.check_output(
            ["docker", "inspect", "--format", "{{.State.Running}}", container], text=True,
        ).strip()
        if running == "true":
            raise SystemExit("A seed operation is already running.")
        seeded = subprocess.run(
            ["docker", "cp", container + ":/target/public/.lean-seed-complete", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if seeded.returncode == 0:
            raise SystemExit("Public data is already seeded; start the existing environment.")
        archive = subprocess.Popen(
            ["tar", "-C", str(state), "-cf", "-", "public", "platform-data", "platform-config"],
            stdout=subprocess.PIPE, env={**os.environ, "COPYFILE_DISABLE": "1"},
        )
        try:
            copied = subprocess.run(
                ["docker", "cp", "-", container + ":/target"], stdin=archive.stdout,
            )
        finally:
            archive.stdout.close()
        if archive.wait() or copied.returncode:
            raise SystemExit("Archive transfer failed; no completion marker was written.")
    subprocess.run([
        *compose, "run", "--rm", "-T", "--no-deps", "--user", "0:0",
        "--cap-add", "CHOWN", "--cap-add", "DAC_OVERRIDE",
        "--entrypoint", "/app/.venv/bin/python", "platform", "-c", FINALIZE,
    ], check=True)


if __name__ == "__main__":
    main()
