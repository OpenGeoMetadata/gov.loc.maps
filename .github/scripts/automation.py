"""GitHub workflow orchestration. No shell interpolation of external metadata."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from loc_maps.common import now, read_json, write_json
from loc_maps.recovery import promote_pilot
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


def restore_channel(channel):
    result = api(f"repos/{REPO}/actions/artifacts?name=loc-state-{channel}&per_page=100")
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
            f"loc-state-{channel}",
            "--dir",
            "dist/restore",
        )
        restore(Path("dist/restore/loc-state.tar.gz"), Path(".state"))
    else:
        releases = api(f"repos/{REPO}/releases?per_page=100")
        snapshots = [
            r
            for r in releases
            if r["tag_name"].startswith((f"snapshot-{channel}-", f"checkpoint-{channel}-"))
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
        else:
            return False
    return True


def load_state():
    recovered = restore_channel(CHANNEL)
    if not recovered and MODE == "full" and os.environ.get("SEED_PILOT") == "true":
        if not restore_channel("pilot"):
            raise RuntimeError("No pilot checkpoint available for explicit bootstrap")
        state = State(Path(".state"))
        try:
            promote_pilot(state, Path("."))
        finally:
            state.close()
        recovered = True
    if not recovered and command("git", "ls-tree", "HEAD", "metadata-aardvark"):
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
        if cooldown:
            write_json(
                Path(".state/job.json"),
                {
                    "status": "paused",
                    "mode": MODE,
                    "reason": "Persisted LOC cooldown",
                    "updated_at": now(),
                },
            )
            with open(os.environ["GITHUB_OUTPUT"], "a") as output:
                output.write("status=paused\n")
            print(f"Cooldown active for {cooldown:.0f}s; hourly recovery will resume it")
            return
    budget = int(os.environ.get("MAX_REQUESTS", "700"))
    if not 1 <= budget <= 700:
        raise ValueError("Request budget must be between 1 and 700")
    args = [
        "loc-maps",
        "run",
        "--mode",
        MODE,
        "--max-requests",
        str(budget),
        "--max-seconds",
        "4800",
    ]
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
    if MODE == "pilot" and os.environ.get("BOOTSTRAP_FULL") == "true":
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
            "mode=full",
            "-f",
            "continuation=false",
            "-f",
            "auto_continue=true",
        )


def continue_run():
    if Path(".state/state.sqlite").exists():
        state = State(Path(".state"))
        try:
            if state.get("pause_until", 0) > time.time():
                print("Cooldown active; scheduled recovery will resume after expiry")
                return
        finally:
            state.close()
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
        "-f",
        f"bootstrap_full={os.environ.get('BOOTSTRAP_FULL', 'false')}",
    )


def retain_checkpoint():
    """Keep the first checkpoint each UTC day beyond Actions artifact expiry."""
    tag = f"checkpoint-{CHANNEL}-{now()[:10]}"
    releases = api(f"repos/{REPO}/releases?per_page=100")
    existing = next((r for r in releases if r["tag_name"] == tag), None)
    if existing and any(a["name"] == "loc-state.tar.gz" for a in existing["assets"]):
        return
    if not existing:
        command(
            "gh",
            "release",
            "create",
            tag,
            "--repo",
            REPO,
            "--draft",
            "--target",
            os.environ["GITHUB_SHA"],
            "--title",
            f"Recovery checkpoint {CHANNEL} {now()[:10]}",
            "--notes",
            "Incomplete harvest recovery state, not a metadata release.",
        )
    command(
        "gh",
        "release",
        "upload",
        tag,
        "dist/loc-state.tar.gz",
        "dist/status.json",
        "--repo",
        REPO,
        "--clobber",
    )


def recover_paused():
    """Dispatch only an explicitly enabled, safely paused bootstrap harvest."""
    runs = api(f"repos/{REPO}/actions/workflows/harvest.yml/runs?per_page=20")["workflow_runs"]
    runs = [r for r in runs if r.get("conclusion") != "skipped"]
    if not runs or any(r["status"] != "completed" for r in runs):
        return
    latest = runs[0]
    if latest["conclusion"] != "success":
        raise RuntimeError(f"Harvest needs inspection: {latest['html_url']}")
    command(
        "gh",
        "run",
        "download",
        str(latest["id"]),
        "--repo",
        REPO,
        "--name",
        "loc-status-production",
        "--dir",
        "dist/recovery",
    )
    status = read_json(Path("dist/recovery/status.json"))
    if (
        status.get("job", {}).get("mode") != "full"
        or status.get("job", {}).get("status") != "paused"
    ):
        return
    if status.get("pause_until", 0) > time.time():
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
        "mode=full",
        "-f",
        "continuation=true",
        "-f",
        "auto_continue=true",
    )


if __name__ == "__main__":
    {
        "restore": load_state,
        "batch": batch,
        "propose": propose,
        "continue": continue_run,
        "retain": retain_checkpoint,
        "recover": recover_paused,
    }[sys.argv[1]]()
