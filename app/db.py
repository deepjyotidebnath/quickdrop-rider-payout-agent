"""Tiny portable DB layer: Postgres in docker-compose, SQLite for local runs and unit tests."""
import json
import sqlite3
import threading
import time
from urllib.parse import urlparse

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS messages(
        message_id TEXT PRIMARY KEY, rider_id TEXT, text TEXT, received_at TEXT,
        status TEXT, reply TEXT, created_n BIGINT)""",
    """CREATE TABLE IF NOT EXISTS trace(
        n BIGINT, rider_id TEXT, ts TEXT, type TEXT, name TEXT, input TEXT, output TEXT)""",
    "CREATE INDEX IF NOT EXISTS trace_rider ON trace(rider_id, n)",
    """CREATE TABLE IF NOT EXISTS rider_state(
        rider_id TEXT PRIMARY KEY, state TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS payout_batches(
        idem_key TEXT PRIMARY KEY, rider_id TEXT, amount INTEGER, items TEXT, mode TEXT,
        day TEXT, status TEXT, payout_id TEXT, attempts INTEGER, source TEXT,
        created_at TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS paid_items(
        rider_id TEXT, item_key TEXT, amount INTEGER, idem_key TEXT,
        PRIMARY KEY(rider_id, item_key))""",
    """CREATE TABLE IF NOT EXISTS ops_items(
        id TEXT PRIMARY KEY, rider_id TEXT, type TEXT, amount INTEGER, reason TEXT,
        status TEXT, items TEXT, created_at TEXT, resolved_at TEXT, resolution TEXT,
        message_id TEXT, dedupe_key TEXT)""",
]

TABLES = ["messages", "trace", "rider_state", "payout_batches", "paid_items", "ops_items"]


class DB:
    def __init__(self, url: str):
        self.url = url
        self.is_pg = url.startswith("postgres")
        self._local = threading.local()
        self._n_lock = threading.Lock()
        self._last_n = 0
        if not self.is_pg:
            path = urlparse(url).path
            self.sqlite_path = path[1:] if path.startswith("//") is False and path.startswith("/") else path
            if url.startswith("sqlite:///"):
                self.sqlite_path = url[len("sqlite:///"):]
            if self.sqlite_path in ("", ":memory:"):
                self.sqlite_path = "file:qdmem?mode=memory&cache=shared"
        self.init_schema()

    # -- connections -------------------------------------------------------
    def _conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            if self.is_pg:
                import psycopg
                c = psycopg.connect(self.url, autocommit=True)
            else:
                c = sqlite3.connect(self.sqlite_path, timeout=15, isolation_level=None,
                                    check_same_thread=False, uri=self.sqlite_path.startswith("file:"))
                c.execute("PRAGMA journal_mode=WAL")
                c.execute("PRAGMA busy_timeout=15000")
            self._local.conn = c
        return c

    def _sql(self, sql):
        return sql.replace("?", "%s") if self.is_pg else sql

    def execute(self, sql, params=()):
        cur = self._conn().execute(self._sql(sql), params)
        return cur.rowcount

    def query(self, sql, params=()):
        cur = self._conn().execute(self._sql(sql), params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def init_schema(self):
        last = None
        for _ in range(40):          # Postgres may still be starting
            try:
                for stmt in SCHEMA:
                    self.execute(stmt)
                return
            except Exception as e:   # noqa: BLE001
                last = e
                self._local.conn = None
                time.sleep(1)
        raise last

    def reset(self):
        for t in TABLES:
            self.execute(f"DELETE FROM {t}")

    def next_n(self) -> int:
        with self._n_lock:
            n = max(time.time_ns(), self._last_n + 1)
            self._last_n = n
            return n


def dumps(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def loads(s, default=None):
    if s is None or s == "":
        return default
    return json.loads(s)
