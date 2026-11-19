"""Local reference ledger for atomic hosted-capacity reservations.

This ledger is intentionally separate from the public local cache runner. A real
checkout must put its payment/order row in the SAME transaction and replace the
operator-supplied metrics with trusted hosted measurements before serving users.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from cacheplus_capacity import CapacityError, Measurements, Policy, PLAN_BYTES, quote


class ReservationLedger:
    """Serialize quote and reservation decisions in one SQLite write transaction."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.parent.is_dir():
            raise CapacityError("Reservation database parent must already exist")
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS config (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    measurements TEXT NOT NULL,
                    policy TEXT NOT NULL,
                    baseline_committed_bytes INTEGER NOT NULL CHECK (baseline_committed_bytes >= 0)
                );
                CREATE TABLE IF NOT EXISTS reservations (
                    order_id TEXT PRIMARY KEY,
                    plan TEXT NOT NULL CHECK (plan IN ('Space', 'Universe')),
                    bytes INTEGER NOT NULL CHECK (bytes > 0),
                    state TEXT NOT NULL CHECK (state IN ('pending', 'active', 'released')),
                    created_at_s INTEGER NOT NULL,
                    updated_at_s INTEGER NOT NULL,
                    release_evidence TEXT
                );
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("PRAGMA synchronous=FULL")
            yield db
            if db.in_transaction:
                db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _clock(now_s):
        if type(now_s) is not int or now_s < 0:
            raise CapacityError("Clock must be a nonnegative integer")

    @staticmethod
    def _order_id(order_id):
        if (not isinstance(order_id, str) or not 1 <= len(order_id) <= 128 or
                not all(ch.isascii() and (ch.isalnum() or ch in "-_:") for ch in order_id)):
            raise CapacityError("Order ID must be 1–128 ASCII letters, digits, hyphens, underscores or colons")

    @staticmethod
    def _committed(db, baseline):
        held = db.execute("SELECT COALESCE(SUM(bytes), 0) FROM reservations "
                          "WHERE state IN ('pending', 'active')").fetchone()[0]
        return baseline + held

    @staticmethod
    def _load(db):
        row = db.execute("SELECT measurements, policy, baseline_committed_bytes "
                         "FROM config WHERE singleton=1").fetchone()
        if row is None:
            raise CapacityError("Hosted measurements and policy are not configured")
        return Measurements.parse(json.loads(row[0])), Policy.parse(json.loads(row[1])), row[2]

    def configure(self, measurements: Measurements, policy: Policy,
                  baseline_committed_bytes: int, now_s: int):
        """Install a fresh operator snapshot; never erase existing reservations."""
        self._clock(now_s)
        if type(baseline_committed_bytes) is not int or baseline_committed_bytes < 0:
            raise CapacityError("Baseline commitments must be nonnegative integer bytes")
        m = Measurements.parse(vars(measurements))
        p = Policy.parse(vars(policy))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            committed = self._committed(db, baseline_committed_bytes)
            result = quote(m, p, committed, now_s)
            db.execute("INSERT INTO config VALUES (1, ?, ?, ?) "
                       "ON CONFLICT(singleton) DO UPDATE SET measurements=excluded.measurements, "
                       "policy=excluded.policy, baseline_committed_bytes=excluded.baseline_committed_bytes",
                       (json.dumps(vars(m), sort_keys=True), json.dumps(vars(p), sort_keys=True),
                        baseline_committed_bytes))
            db.commit()
            return result

    def inspect(self, now_s: int):
        self._clock(now_s)
        with self._connect() as db:
            m, p, baseline = self._load(db)
            committed = self._committed(db, baseline)
            result = quote(m, p, committed, now_s)
            return {**result, "committed_bytes": committed}

    def reserve(self, order_id: str, plan: str, now_s: int):
        """Hold a whole-plan commitment; concurrent calls cannot oversell."""
        self._order_id(order_id)
        self._clock(now_s)
        if plan not in PLAN_BYTES:
            raise CapacityError("Unknown workspace plan")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT plan, bytes, state FROM reservations WHERE order_id=?",
                                  (order_id,)).fetchone()
            if existing:
                if existing[0] != plan or existing[2] == "released":
                    raise CapacityError("Order ID already belongs to a different or released reservation")
                db.commit()
                return {"order_id": order_id, "plan": plan, "bytes": existing[1],
                        "state": existing[2], "idempotent": True}
            m, p, baseline = self._load(db)
            committed = self._committed(db, baseline)
            result = quote(m, p, committed, now_s)
            size = PLAN_BYTES[plan]
            if result["sellable_bytes"] < size:
                raise CapacityError("Insufficient verified hosted and backup capacity")
            db.execute("INSERT INTO reservations VALUES (?, ?, ?, 'pending', ?, ?, NULL)",
                       (order_id, plan, size, now_s, now_s))
            db.commit()
            return {"order_id": order_id, "plan": plan, "bytes": size,
                    "state": "pending", "idempotent": False}

    def activate(self, order_id: str, now_s: int):
        """Mark a paid order active without changing its held bytes."""
        self._order_id(order_id)
        self._clock(now_s)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM reservations WHERE order_id=?", (order_id,)).fetchone()
            if row is None or row[0] == "released":
                raise CapacityError("No held reservation for order")
            if row[0] == "pending":
                db.execute("UPDATE reservations SET state='active', updated_at_s=? WHERE order_id=?",
                           (now_s, order_id))
            db.commit()
            return "active"

    def release(self, order_id: str, now_s: int, evidence: str):
        """Admin-only release after external deprovisioning or payment reconciliation.

        An evidence string is an audit pointer, not proof by itself. Never expose
        this method directly to an untrusted customer-facing endpoint.
        """
        self._order_id(order_id)
        self._clock(now_s)
        if not isinstance(evidence, str) or not 8 <= len(evidence) <= 256:
            raise CapacityError("A deprovision or cancellation evidence pointer is required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM reservations WHERE order_id=?", (order_id,)).fetchone()
            if row is None or row[0] == "released":
                raise CapacityError("No held reservation for order")
            db.execute("UPDATE reservations SET state='released', updated_at_s=?, "
                       "release_evidence=? WHERE order_id=?", (now_s, evidence, order_id))
            db.commit()
            return "released"
