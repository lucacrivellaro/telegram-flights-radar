"""Persistenza SQLite: storico prezzi, offerte già inviate, impostazioni."""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    price REAL NOT NULL,
    stops INTEGER NOT NULL DEFAULT 0,
    depart_date TEXT,
    return_date TEXT,
    source TEXT,
    found_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_route ON price_history (origin, destination);

CREATE TABLE IF NOT EXISTS sent_offers (
    offer_hash TEXT PRIMARY KEY,
    price REAL NOT NULL,
    sent_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # --- storico prezzi -------------------------------------------------

    def record_price(self, offer) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO price_history (origin, destination, price, stops,"
                " depart_date, return_date, source, found_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    offer.origin,
                    offer.destination,
                    offer.price,
                    offer.stops,
                    offer.depart_date.isoformat() if offer.depart_date else None,
                    offer.return_date.isoformat() if offer.return_date else None,
                    offer.source,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            self._conn.commit()

    def route_average(self, origin: str, destination: str) -> tuple[float | None, int]:
        """Media e numero di rilevazioni degli ultimi 90 giorni per la rotta."""
        cutoff = (datetime.now() - timedelta(days=90)).isoformat(timespec="seconds")
        with self._lock:
            row = self._conn.execute(
                "SELECT AVG(price), COUNT(*) FROM price_history"
                " WHERE origin = ? AND destination = ? AND found_at >= ?",
                (origin, destination, cutoff),
            ).fetchone()
        return (row[0], row[1]) if row and row[1] else (None, 0)

    # --- dedup offerte inviate -------------------------------------------

    def last_sent(self, offer_hash: str) -> tuple[float, datetime] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT price, sent_at FROM sent_offers WHERE offer_hash = ?",
                (offer_hash,),
            ).fetchone()
        if not row:
            return None
        return row[0], datetime.fromisoformat(row[1])

    def mark_sent(self, offer_hash: str, price: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO sent_offers (offer_hash, price, sent_at) VALUES (?, ?, ?)"
                " ON CONFLICT(offer_hash) DO UPDATE SET price = excluded.price,"
                " sent_at = excluded.sent_at",
                (offer_hash, price, datetime.now().isoformat(timespec="seconds")),
            )
            self._conn.commit()

    # --- impostazioni modificabili dal bot --------------------------------

    def get_setting(self, key: str, default=None):
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )
            self._conn.commit()

    def delete_setting(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            self._conn.commit()
