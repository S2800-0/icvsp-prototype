"""Storage. SQLite for now (one small file, or memory for tests); the SQL is plain enough to move to
PostgreSQL (Supabase) by swapping this class, without touching the API or the trust logic."""
from __future__ import annotations

import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    device_id   TEXT PRIMARY KEY,
    public_key  TEXT NOT NULL,              -- Ed25519, hex
    status      TEXT NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE / REVOKED
    good        REAL NOT NULL DEFAULT 0,    -- Beta reputation counts (trust engine)
    bad         REAL NOT NULL DEFAULT 0,
    registered  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    event_id    TEXT PRIMARY KEY,
    device_id   TEXT NOT NULL,
    boot_id     INTEGER NOT NULL,
    sequence    INTEGER NOT NULL,
    detected_at REAL NOT NULL,              -- Unix seconds
    received_at REAL NOT NULL,
    line        TEXT NOT NULL,              -- the signed report exactly as received
    settled     INTEGER NOT NULL DEFAULT 0, -- reputation already updated from this report
    UNIQUE (device_id, boot_id, sequence)
);
CREATE TABLE IF NOT EXISTS beacons (
    vehicle_id  TEXT NOT NULL,
    t           REAL NOT NULL,
    lat REAL NOT NULL, lon REAL NOT NULL, speed_mps REAL NOT NULL, heading_deg REAL NOT NULL,
    detector_on INTEGER NOT NULL,
    PRIMARY KEY (vehicle_id, t)
);
CREATE TABLE IF NOT EXISTS settled_passes (       -- "drove past and saw nothing" already counted
    vehicle_id TEXT NOT NULL, hazard_key TEXT NOT NULL, PRIMARY KEY (vehicle_id, hazard_key)
);
CREATE TABLE IF NOT EXISTS rejections (
    received_at REAL NOT NULL, device_id TEXT, event_id TEXT, reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reports_time ON reports (detected_at);
CREATE INDEX IF NOT EXISTS beacons_time ON beacons (t);
"""


class Store:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def _x(self, sql: str, args=()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    # devices
    def add_device(self, device_id: str, public_key: str, now: float, good: float = 0.0) -> None:
        self._x("INSERT INTO devices (device_id, public_key, registered, good) VALUES (?, ?, ?, ?)",
                (device_id, public_key, now, good))

    def device(self, device_id: str) -> sqlite3.Row | None:
        return self._x("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone()

    def set_status(self, device_id: str, status: str) -> bool:
        return self._x("UPDATE devices SET status = ? WHERE device_id = ?", (status, device_id)).rowcount == 1

    def reputations(self) -> dict[str, tuple[float, float]]:
        return {r["device_id"]: (r["good"], r["bad"]) for r in self._x("SELECT device_id, good, bad FROM devices")}

    def devices(self) -> list[sqlite3.Row]:
        return self._x("SELECT device_id, status, good, bad, registered FROM devices ORDER BY device_id").fetchall()

    def add_reputation(self, device_id: str, good: float, bad: float) -> None:
        self._x("UPDATE devices SET good = good + ?, bad = bad + ? WHERE device_id = ?", (good, bad, device_id))

    # reports
    def report(self, event_id: str) -> sqlite3.Row | None:
        return self._x("SELECT * FROM reports WHERE event_id = ?", (event_id,)).fetchone()

    def add_report(self, ev: dict, line: str, detected_at: float, now: float) -> None:
        """Raises sqlite3.IntegrityError on a repeated event_id or (device, boot, sequence)."""
        self._x("INSERT INTO reports (event_id, device_id, boot_id, sequence, detected_at, received_at, line) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ev["event_id"], ev["device_id"], ev["boot_id"], ev["sequence"], detected_at, now, line))

    def reports_since(self, t: float) -> list[sqlite3.Row]:
        return self._x("SELECT * FROM reports WHERE detected_at >= ? ORDER BY detected_at", (t,)).fetchall()

    def mark_settled(self, event_id: str) -> None:
        self._x("UPDATE reports SET settled = 1 WHERE event_id = ?", (event_id,))

    def reject(self, now: float, device_id: str | None, event_id: str | None, reason: str) -> None:
        self._x("INSERT INTO rejections VALUES (?, ?, ?, ?)", (now, device_id, event_id, reason))

    def rejections(self, limit: int = -1) -> list[sqlite3.Row]:
        """Oldest first; with a limit, only the latest `limit` rows."""
        rows = self._x("SELECT * FROM rejections ORDER BY received_at DESC LIMIT ?", (limit,)).fetchall()
        return rows[::-1]

    def recent_reports(self, limit: int) -> list[sqlite3.Row]:
        return self._x("SELECT * FROM reports ORDER BY received_at DESC LIMIT ?", (limit,)).fetchall()

    def count(self, table: str) -> int:
        assert table in ("devices", "reports", "rejections", "beacons")
        return self._x(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    # beacons
    def add_beacons(self, rows: list[tuple]) -> None:
        with self.lock:
            self.db.executemany("INSERT OR IGNORE INTO beacons VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            self.db.commit()

    def beacons_since(self, t: float) -> list[sqlite3.Row]:
        return self._x("SELECT * FROM beacons WHERE t >= ? ORDER BY t", (t,)).fetchall()

    def latest_positions(self, since: float, until: float) -> list[sqlite3.Row]:
        """The newest beacon of every vehicle seen between `since` and `until`."""
        return self._x("SELECT b.* FROM beacons b JOIN (SELECT vehicle_id, MAX(t) AS t FROM beacons "
                       "WHERE t BETWEEN ? AND ? GROUP BY vehicle_id) m ON b.vehicle_id = m.vehicle_id AND b.t = m.t",
                       (since, until)).fetchall()

    def pass_settled(self, vehicle_id: str, hazard_key: str) -> bool:
        cur = self._x("INSERT OR IGNORE INTO settled_passes VALUES (?, ?)", (vehicle_id, hazard_key))
        return cur.rowcount == 0  # True if it was already there
