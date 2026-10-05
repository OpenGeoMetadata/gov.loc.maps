from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .client import Client, Paused
from .common import now, read_json, write_json
from .inventory import enumerate_items, identifier_review
from .pipeline import fetch, publish, transform
from .snapshot import restore, snapshot
from .state import locked_state
from .validation import validate_tree


def parser():
    cli = argparse.ArgumentParser(description="LOC maps → Aardvark, with resumable checkpoints")
    cli.add_argument(
        "command",
        choices=[
            "inventory",
            "fetch",
            "transform",
            "validate",
            "publish",
            "run",
            "status",
            "snapshot",
            "restore",
        ],
    )
    cli.add_argument("--root", type=Path, default=Path("."))
    cli.add_argument("--state", type=Path, default=Path(".state"))
    cli.add_argument("--stage", type=Path, default=Path(".staging"))
    cli.add_argument("--archive", type=Path, default=Path("dist/loc-state.tar.gz"))
    cli.add_argument("--mode", choices=["pilot", "full", "update"], default="full")
    cli.add_argument(
        "--new-inventory",
        action="store_true",
        help="Start a new inventory after the previous one was published",
    )
    cli.add_argument(
        "--resume",
        action="store_true",
        help="Explicit spelling of the default checkpoint-resume behavior",
    )
    cli.add_argument(
        "--dry-run",
        action="store_true",
        help="Publish/run: validate and report without changing published files",
    )
    cli.add_argument("--retry-failed", action="store_true")
    cli.add_argument(
        "--allow-large-withdrawal",
        action="store_true",
        help="Reviewed override for withdrawals above 2%%",
    )
    cli.add_argument("--max-requests", type=int, default=700)
    cli.add_argument("--max-seconds", type=int, default=4800)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    if args.max_requests < 1 or args.max_seconds < 1:
        raise SystemExit("Budgets must be positive")
    if args.dry_run and args.command not in {"run", "publish"}:
        raise SystemExit(
            "--dry-run applies to run or publish; use transform to build reviewable output"
        )
    try:
        if args.command == "restore":
            restore(args.archive, args.state)
            return 0
        if args.command == "validate":
            print(json.dumps({"validated": len(validate_tree(args.root))}))
            return 0
        with locked_state(args.state) as state:
            if args.command == "status":
                run = state.active_run()
                print(
                    json.dumps(
                        {
                            "run": dict(run) if run else None,
                            "items": state.db.execute("SELECT COUNT(*) FROM items").fetchone()[0],
                            "cached": state.db.execute(
                                "SELECT COUNT(*) FROM items WHERE cache IS NOT NULL"
                            ).fetchone()[0],
                            "unresolved_identifiers": len(identifier_review(state)),
                            "job": read_json(state.root / "job.json", {}),
                            "request_interval": state.get("request_interval", 6.1),
                            "errors": state.db.execute(
                                "SELECT COUNT(*) FROM items WHERE error IS NOT NULL"
                            ).fetchone()[0],
                            "pause_until": state.get("pause_until", 0),
                        },
                        indent=2,
                    )
                )
                return 0
            if args.command == "snapshot":
                snapshot(state, args.archive)
                return 0
            client = Client(state, args.max_requests, args.max_seconds)
            if args.command in {"inventory", "run"}:
                current = state.active_run()
                new = args.new_inventory and (not current or current["applied"])
                enumerate_items(state, client, args.mode, new=new)
            if args.command in {"fetch", "run"}:
                fetch(state, client, retry_failed=args.retry_failed)
            if args.command in {"transform", "run"}:
                report = transform(state, args.root, args.stage, args.allow_large_withdrawal)
                print(json.dumps(report, indent=2))
            if args.command in {"publish", "run"}:
                changed = publish(state, args.root, args.stage, args.dry_run)
                print(json.dumps({"dry_run": args.dry_run, "changed_files": len(changed)}))
            write_json(
                args.state / "job.json",
                {"status": "complete", "mode": args.mode, "updated_at": now()},
            )
            return 0
    except Paused as exc:
        write_json(
            args.state / "job.json",
            {"status": "paused", "reason": str(exc), "mode": args.mode, "updated_at": now()},
        )
        print(str(exc), file=sys.stderr)
        return 75
    except Exception as exc:
        write_json(
            args.state / "job.json",
            {"status": "failed", "error": str(exc), "mode": args.mode, "updated_at": now()},
        )
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
