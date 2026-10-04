from __future__ import annotations

import fcntl
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .common import now


class State:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "state.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY, mode TEXT NOT NULL, status TEXT NOT NULL,
                started TEXT NOT NULL, completed TEXT, pass INTEGER DEFAULT 1,
                next_url TEXT, expected INTEGER, page INTEGER DEFAULT 0,
                error TEXT, applied INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS seen (
                run INTEGER, pass INTEGER, url TEXT, summary TEXT, hash TEXT,
                PRIMARY KEY(run,pass,url));
            CREATE TABLE IF NOT EXISTS exclusions (
                run INTEGER, url TEXT, reason TEXT, summary TEXT, PRIMARY KEY(run,url));
            CREATE TABLE IF NOT EXISTS items (
                url TEXT PRIMARY KEY, summary TEXT, hash TEXT, fetched_hash TEXT,
                fetched_at TEXT, cache TEXT, misses INTEGER DEFAULT 0,
                last_seen INTEGER, error TEXT, attempts INTEGER DEFAULT 0);
        """)

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value))
            )

    def active_run(self):
        return self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()

    def start(self, mode, url):
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO runs(mode,status,started,next_url) VALUES (?,'enumerating',?,?)",
                (mode, now(), url),
            )
        return cursor.lastrowid

    def close(self):
        self.db.close()


@contextmanager
def locked_state(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "process.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another harvester is using this state directory") from exc
        state = State(root)
        try:
            yield state
        finally:
            state.close()
