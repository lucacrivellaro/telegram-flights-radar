"""Persistenza SQLite: storico prezzi, utenti, offerte inviate, impostazioni.

Multi-utente: `price_history` resta globale (i prezzi sono oggettivi e la
media storica conviene a tutti), mentre `sent_offers` e `user_settings`
sono scoped per chat_id. La tabella `users` gestisce il ciclo di vita
dell'iscrizione: pending → active (approvazione admin), stopped (/stop),
blocked (/rifiuta).
"""

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
    found_at TEXT NOT NULL,
    trip_type TEXT NOT NULL DEFAULT 'one_way'
);
CREATE INDEX IF NOT EXISTS idx_history_route ON price_history (origin, destination);

CREATE TABLE IF NOT EXISTS sent_offers (
    chat_id TEXT NOT NULL,
    offer_hash TEXT NOT NULL,
    price REAL NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, offer_hash)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    chat_id TEXT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    requested_at TEXT NOT NULL,
    approved_at TEXT
);

CREATE TABLE IF NOT EXISTS user_settings (
    chat_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (chat_id, key)
);
"""

USER_STATUSES = {"pending", "active", "stopped", "blocked"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Storage:
    def __init__(self, db_path: str, admin_chat_id: str = ""):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._admin = str(admin_chat_id).strip()
        with self._lock:
            self._migrate_sent_offers()
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate_sent_offers(self) -> None:
        """Rebuild di sent_offers (PK offer_hash → (chat_id, offer_hash)).

        Va eseguita PRIMA dello schema script, altrimenti il CREATE IF NOT
        EXISTS non farebbe nulla sulla vecchia tabella. I dati esistenti
        vengono attribuiti all'admin (l'unico destinatario pre-multiutente).
        """
        exists = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sent_offers'"
        ).fetchone()
        if not exists:
            return
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(sent_offers)")}
        if "chat_id" in cols:
            return
        self._conn.execute("ALTER TABLE sent_offers RENAME TO sent_offers_legacy")
        self._conn.execute(
            "CREATE TABLE sent_offers ("
            " chat_id TEXT NOT NULL, offer_hash TEXT NOT NULL,"
            " price REAL NOT NULL, sent_at TEXT NOT NULL,"
            " PRIMARY KEY (chat_id, offer_hash))"
        )
        if self._admin:
            self._conn.execute(
                "INSERT INTO sent_offers (chat_id, offer_hash, price, sent_at)"
                " SELECT ?, offer_hash, price, sent_at FROM sent_offers_legacy",
                (self._admin,),
            )
        self._conn.execute("DROP TABLE sent_offers_legacy")
        logger.info("Migrazione: sent_offers ora è per utente (chat_id)")

    def _migrate(self) -> None:
        """Migrazioni idempotenti su DB creati con schemi precedenti."""
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(price_history)")}
        if "trip_type" not in cols:
            self._conn.execute(
                "ALTER TABLE price_history"
                " ADD COLUMN trip_type TEXT NOT NULL DEFAULT 'one_way'"
            )
            # backfill: le rilevazioni A/R storiche hanno già return_date valorizzato
            self._conn.execute(
                "UPDATE price_history SET trip_type = 'round_trip'"
                " WHERE return_date IS NOT NULL"
            )
            logger.info("Migrazione: aggiunta colonna trip_type a price_history")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_route_type"
            " ON price_history (origin, destination, trip_type)"
        )

        if self._admin:
            # l'admin è sempre un utente attivo, senza approvazione
            self._conn.execute(
                "INSERT INTO users (chat_id, first_name, status, requested_at,"
                " approved_at) VALUES (?, 'Admin', 'active', ?, ?)"
                " ON CONFLICT(chat_id) DO NOTHING",
                (self._admin, _now(), _now()),
            )
            # le vecchie impostazioni globali (era single-user) diventano sue
            moved = self._conn.execute(
                "INSERT OR IGNORE INTO user_settings (chat_id, key, value)"
                " SELECT ?, key, value FROM settings",
                (self._admin,),
            ).rowcount
            if moved:
                logger.info(
                    "Migrazione: %d impostazioni globali spostate sull'admin", moved
                )
            self._conn.execute("DELETE FROM settings")

    # --- storico prezzi -------------------------------------------------

    def record_price(self, offer) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO price_history (origin, destination, price, stops,"
                " depart_date, return_date, source, found_at, trip_type)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    offer.origin,
                    offer.destination,
                    offer.price,
                    offer.stops,
                    offer.depart_date.isoformat() if offer.depart_date else None,
                    offer.return_date.isoformat() if offer.return_date else None,
                    offer.source,
                    _now(),
                    offer.trip_type,
                ),
            )
            self._conn.commit()

    def route_average(
        self, origin: str, destination: str, trip_type: str = "one_way"
    ) -> tuple[float | None, int]:
        """Media e numero di rilevazioni degli ultimi 90 giorni per la rotta.

        Sola andata e andata/ritorno hanno storici separati: i prezzi non sono
        comparabili e non devono inquinarsi a vicenda."""
        cutoff = (datetime.now() - timedelta(days=90)).isoformat(timespec="seconds")
        with self._lock:
            row = self._conn.execute(
                "SELECT AVG(price), COUNT(*) FROM price_history"
                " WHERE origin = ? AND destination = ? AND trip_type = ?"
                " AND found_at >= ?",
                (origin, destination, trip_type, cutoff),
            ).fetchone()
        return (row[0], row[1]) if row and row[1] else (None, 0)

    # --- dedup offerte inviate (per utente) --------------------------------

    def last_sent(self, chat_id: str | int, offer_hash: str) -> tuple[float, datetime] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT price, sent_at FROM sent_offers"
                " WHERE chat_id = ? AND offer_hash = ?",
                (str(chat_id), offer_hash),
            ).fetchone()
        if not row:
            return None
        return row[0], datetime.fromisoformat(row[1])

    def mark_sent(self, chat_id: str | int, offer_hash: str, price: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO sent_offers (chat_id, offer_hash, price, sent_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(chat_id, offer_hash) DO UPDATE SET"
                " price = excluded.price, sent_at = excluded.sent_at",
                (str(chat_id), offer_hash, price, _now()),
            )
            self._conn.commit()

    # --- utenti -------------------------------------------------------------

    def get_user(self, chat_id: str | int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT chat_id, username, first_name, status, requested_at,"
                " approved_at FROM users WHERE chat_id = ?",
                (str(chat_id),),
            ).fetchone()
        if not row:
            return None
        keys = ["chat_id", "username", "first_name", "status", "requested_at", "approved_at"]
        return dict(zip(keys, row))

    def add_user(
        self, chat_id: str | int, username: str | None, first_name: str | None,
        status: str = "pending",
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO users (chat_id, username, first_name, status,"
                " requested_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(chat_id) DO UPDATE SET"
                " username = excluded.username, first_name = excluded.first_name",
                (str(chat_id), username, first_name, status, _now()),
            )
            self._conn.commit()

    def set_user_status(self, chat_id: str | int, status: str) -> None:
        if status not in USER_STATUSES:
            raise ValueError(f"Status utente non valido: {status}")
        with self._lock:
            self._conn.execute(
                "UPDATE users SET status = ?,"
                " approved_at = CASE WHEN ? = 'active' AND approved_at IS NULL"
                " THEN ? ELSE approved_at END"
                " WHERE chat_id = ?",
                (status, status, _now(), str(chat_id)),
            )
            self._conn.commit()

    def list_users(self, status: str | None = None) -> list[dict]:
        query = (
            "SELECT chat_id, username, first_name, status, requested_at, approved_at"
            " FROM users"
        )
        params: tuple = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY requested_at"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        keys = ["chat_id", "username", "first_name", "status", "requested_at", "approved_at"]
        return [dict(zip(keys, row)) for row in rows]

    # --- impostazioni per utente (sovrascrivono i default del .env) ---------

    def get_user_setting(self, chat_id: str | int, key: str, default=None):
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM user_settings WHERE chat_id = ? AND key = ?",
                (str(chat_id), key),
            ).fetchone()
        return json.loads(row[0]) if row else default

    def set_user_setting(self, chat_id: str | int, key: str, value) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO user_settings (chat_id, key, value) VALUES (?, ?, ?)"
                " ON CONFLICT(chat_id, key) DO UPDATE SET value = excluded.value",
                (str(chat_id), key, json.dumps(value)),
            )
            self._conn.commit()

    def delete_user_setting(self, chat_id: str | int, key: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM user_settings WHERE chat_id = ? AND key = ?",
                (str(chat_id), key),
            )
            self._conn.commit()
