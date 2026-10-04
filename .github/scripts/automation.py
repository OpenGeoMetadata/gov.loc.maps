"""GitHub workflow orchestration. No shell interpolation of external metadata."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from loc_maps.common import read_json
from loc_maps.snapshot import restore
from loc_maps.state import State

REPO = os.environ["REPOSITORY"]
MODE = os.environ.get("MODE", "full")
CHANNEL = "pilot" if MODE == "pilot" else "production"
BRANCH = f"harvest/{CHANNEL}"


def command(*args, check=True):
    return subprocess.run(args, check=check, text=True, capture_output=True).stdout.strip()


def api(path, *args):
    return json.loads(command("gh", "api", path, *args))


def load_state():
    result = api(f"repos/{REPO}/actions/artifacts?name=loc-state-{CHANNEL}&per_page=100")
    artifacts = sorted(
        (a for a in result["artifacts"] if not a["expired"]), key=lambda a: a["id"], reverse=True
    )
    if artifacts:
        run = str(artifacts[0]["workflow_run"]["id"])
        command(
            "gh",
            "run",
            "download",
            run,
            "--repo",
            REPO,
            "--name",
            f"loc-state-{CHANNEL}",
            "--dir",
            "dist/restore",
        )
        restore(Path("dist/restore/loc-state.tar.gz"), Path(".state"))
    else:
        releases = api(f"repos/{REPO}/releases?per_page=100")
        snapshots = [
            r
            for r in releases
            if r["tag_name"].startswith(f"snapshot-{CHANNEL}-")
            and any(a["name"] == "loc-state.tar.gz" for a in r["assets"])
        ]
        if snapshots:
            newest = max(snapshots, key=lambda r: r["created_at"])
            command(
                "gh",
                "release",
                "download",
                newest["tag_name"],
                "--repo",
                REPO,
                "--pattern",
                "loc-state.tar.gz",
                "--dir",
                "dist/restore",
            )
            restore(Path("dist/restore/loc-state.tar.gz"), Path(".state"))
        elif command("git", "ls-tree", "HEAD", "metadata-aardvark"):
            raise RuntimeError(
                "No recoverable checkpoint. Restore state before resuming; do not recreate lifecycle history."
            )
    branch_exists = bool(command("git", "ls-remote", "--heads", "origin", BRANCH))
    if branch_exists:
        command("git", "fetch", "origin", BRANCH)
        for name in ("metadata-aardvark", "withdrawn.json", "reports"):
            if command("git", "ls-tree", "FETCH_HEAD", name):
                command("git", "restore", "--source", "FETCH_HEAD", "--", name)
    if MODE == "pilot" and command("git", "ls-tree", "HEAD", "metadata-aardvark"):
        raise RuntimeError(
            "Pilot runs must precede production publication; use a separate local output directory for later pilots"
        )


def batch():
    if Path(".state/state.sqlite").exists():
        state = State(Path(".state"))
        cooldown = max(0, state.get("pause_until", 0) - time.time())
        state.close()
        if cooldown > 3700:
            raise RuntimeError(
                "LOC requested an extended cooldown; resume manually after it expires"
            )
        if cooldown:
            print(f"Respecting persisted LOC cooldown ({cooldown:.0f} seconds)", flush=True)
            time.sleep(cooldown + 1)
    args = ["loc-maps", "run", "--mode", MODE, "--max-requests", "700", "--max-seconds", "4800"]
    if os.environ.get("CONTINUATION") != "true":
        args.append("--new-inventory")
    if os.environ.get("RETRY_FAILED") == "true":
        args.append("--retry-failed")
    result = subprocess.run(args, check=False)
    status = {0: "complete", 75: "paused"}.get(result.returncode, "failed")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"status={status}\n")
    if status == "failed":
        raise SystemExit(result.returncode)


def propose():
    report = read_json(Path("reports/latest.json"))
    command("git", "config", "user.name", "github-actions[bot]")
    command("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    # No PR for report-only churn. Artifacts still preserve the inventory evidence.
    command("git", "add", "metadata-aardvark", "withdrawn.json", "reports")
    changes = command(
        "git", "diff", "--cached", "--name-only", "--", "metadata-aardvark", "withdrawn.json"
    )
    if not changes:
        print("No metadata changes; checkpoint retained without a commit")
        return
    command("git", "checkout", "-B", BRANCH)
    command("git", "commit", "-m", f"Update LOC maps {CHANNEL} metadata")
    # Only this automation's branch is updated; main is never pushed by the harvester.
    command("git", "push", "--force-with-lease", "origin", f"HEAD:refs/heads/{BRANCH}")
    body = "\n".join(
        [
            f"This proposes the {CHANNEL} LOC maps collection from inventory {report['inventory_date']}.",
            "",
            "Counts:",
            "```json",
            json.dumps(report["counts"], indent=2),
            "```",
            "",
            "Coverage:",
            "```json",
            json.dumps(report["coverage"], indent=2),
            "```",
            "",
            "Validation: offline tests and schema validation passed. See reports/exclusions.json for non-item search results.",
            "",
            "Before the initial release: review 30 varied records, verify OGM API and GeoBlacklight behavior, and complete docs/release-checklist.md.",
            "",
            "Pilot metadata is for review only and must not be merged into the production collection.",
            "The source snapshot is attached to the draft release and the workflow artifact.",
        ]
    )
    Path("dist/pr-body.md").write_text(body)
    prs = api(f"repos/{REPO}/pulls?state=open&head={REPO.split('/')[0]}:{BRANCH}")
    if prs:
        command(
            "gh",
            "pr",
            "edit",
            str(prs[0]["number"]),
            "--repo",
            REPO,
            "--body-file",
            "dist/pr-body.md",
        )
    else:
        command(
            "gh",
            "pr",
            "create",
            "--repo",
            REPO,
            "--base",
            "main",
            "--head",
            BRANCH,
            "--title",
            f"Add LOC maps {CHANNEL} metadata"
            if CHANNEL == "pilot"
            else "Update Library of Congress maps",
            "--body-file",
            "dist/pr-body.md",
            "--draft",
        )
    tag = f"snapshot-{CHANNEL}-{os.environ['GITHUB_RUN_ID']}"
    command(
        "gh",
        "release",
        "create",
        tag,
        "dist/loc-state.tar.gz",
        "reports/latest.json",
        "--repo",
        REPO,
        "--target",
        BRANCH,
        "--draft",
        "--title",
        f"LOC {CHANNEL} source snapshot",
        "--notes-file",
        "dist/pr-body.md",
    )


def continue_run():
    current = int(os.environ.get("ROUND", "1"))
    if os.environ.get("AUTO_CONTINUE") != "true" or current >= 120:
        print(
            "Checkpoint saved. Manual resume required (auto-continuation disabled or 120-job limit reached)."
        )
        return
    command(
        "gh",
        "workflow",
        "run",
        "harvest.yml",
        "--repo",
        REPO,
        "--ref",
        "main",
        "-f",
        f"mode={MODE}",
        "-f",
        "continuation=true",
        "-f",
        "auto_continue=true",
        "-f",
        f"round={current + 1}",
    )


if __name__ == "__main__":
    {"restore": load_state, "batch": batch, "propose": propose, "continue": continue_run}[
        sys.argv[1]
    ]()
