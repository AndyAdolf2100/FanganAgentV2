import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Conflict(ValueError):
    pass


class Store:
    """SQLite is authoritative. State changes and their events commit together."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    kind TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS events_run ON events(run_id, id);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, request: dict) -> dict:
        run = {**request, "id": uuid.uuid4().hex, "status": "ready", "index": 0,
               "outputs": {}, "history": [], "feedback": {}, "gate": None,
               "selected_theme": "", "creative_input": "", "plan": {},
               "error": None, "revision": 0, "created": time.time()}
        with self.connect() as db:
            db.execute("INSERT INTO runs VALUES (?, ?)", (run["id"], json.dumps(run, ensure_ascii=False)))
            self._event(db, run["id"], "created", {"status": "ready"})
        return run

    def get(self, run_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        return json.loads(row[0])

    def list(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT body FROM runs ORDER BY rowid DESC LIMIT 100").fetchall()
        return [json.loads(row[0]) for row in rows]

    def mutate(self, run_id, fn, kind="state"):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            run = json.loads(row[0])
            fn(run)
            run["updated"] = time.time()
            db.execute("UPDATE runs SET body=? WHERE id=?", (json.dumps(run, ensure_ascii=False), run_id))
            self._event(db, run_id, kind, {"status": run["status"], "index": run["index"], "gate": run["gate"]})
        return run

    @staticmethod
    def _event(db, run_id, kind, data):
        db.execute("INSERT INTO events(run_id,kind,body,created) VALUES(?,?,?,?)",
                   (run_id, kind, json.dumps(data, ensure_ascii=False, default=str), time.time()))

    def event(self, run_id, kind, data):
        with self.connect() as db:
            self._event(db, run_id, kind, data)

    def events(self, run_id, after=0):
        with self.connect() as db:
            rows = db.execute("SELECT id,kind,body,created FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT 200",
                              (run_id, after)).fetchall()
        return [{"id": i, "kind": k, "data": json.loads(b), "created": t} for i, k, b, t in rows]

    def recover(self):
        # Single server process owns this DB (enforced by an OS lock in api.py).
        with self.connect() as db:
            rows = db.execute("SELECT body FROM runs").fetchall()
        for run in (json.loads(row[0]) for row in rows):
            if run["status"] == "running":
                def stopped(r):
                    r.update(status="failed", error="服务重启中断了执行；可重试当前阶段。")
                self.mutate(run["id"], stopped, "interrupted")
