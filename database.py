# -*- coding: utf-8 -*-
"""
Шар даних на SQLite.

Важлива ідея: машина і вантаж НЕ окремий довідник — це просто останні
введені водієм значення, які кешуються на самому водії (last_tractor,
last_trailer, last_cargo, last_route) і пропонуються повторно наступного
разу ("той самий / інший").
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone

from config import DB_PATH

_lock = threading.Lock()


def _conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with _lock, _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS drivers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                full_name TEXT NOT NULL,
                phone TEXT,
                last_tractor TEXT,
                last_trailer TEXT,
                last_cargo TEXT,
                last_route TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_id INTEGER NOT NULL REFERENCES drivers(id),
                tractor_number TEXT,
                trailer_number TEXT,
                cargo_description TEXT,
                route TEXT,
                status TEXT NOT NULL DEFAULT 'in_progress', -- in_progress / done / cancelled
                current_step TEXT NOT NULL DEFAULT 'loading_1',
                rules_ack_at TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                report_path TEXT,
                onedrive_report_url TEXT
            );

            CREATE TABLE IF NOT EXISTS step_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trip_id INTEGER NOT NULL REFERENCES trips(id),
                step_id TEXT NOT NULL,
                completed_at TEXT,
                UNIQUE(trip_id, step_id)
            );

            CREATE TABLE IF NOT EXISTS checklist_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trip_id INTEGER NOT NULL REFERENCES trips(id),
                step_id TEXT NOT NULL,
                item_key TEXT NOT NULL,
                checked_at TEXT,
                UNIQUE(trip_id, item_key)
            );

            CREATE TABLE IF NOT EXISTS photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trip_id INTEGER NOT NULL REFERENCES trips(id),
                step_id TEXT,
                item_key TEXT,
                kind TEXT NOT NULL DEFAULT 'checklist', -- checklist / pause / extra_stop / incident
                file_path TEXT NOT NULL,
                onedrive_url TEXT,
                created_at TEXT NOT NULL
            );

            -- Повторювані необов'язкові події: пауза / додаткова зупинка
            CREATE TABLE IF NOT EXISTS optional_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trip_id INTEGER NOT NULL REFERENCES trips(id),
                event_type TEXT NOT NULL, -- pause / extra_stop
                context_step TEXT,        -- на якому кроці рейсу це сталось
                note TEXT,
                items_checked_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trip_id INTEGER NOT NULL REFERENCES trips(id),
                context_step TEXT,
                description TEXT,
                created_at TEXT NOT NULL
            );
            """
        )


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- drivers ----------

def get_driver_by_tg(telegram_id):
    with _lock, _conn() as conn:
        return conn.execute("SELECT * FROM drivers WHERE telegram_id=?", (telegram_id,)).fetchone()


def get_driver(driver_id):
    with _lock, _conn() as conn:
        return conn.execute("SELECT * FROM drivers WHERE id=?", (driver_id,)).fetchone()


def create_driver(telegram_id, full_name, phone=None):
    with _lock, _conn() as conn:
        cur = conn.execute(
            "INSERT INTO drivers (telegram_id, full_name, phone, created_at) VALUES (?,?,?,?)",
            (telegram_id, full_name, phone, now()),
        )
        return cur.lastrowid


def update_driver_last_vehicle_cargo(driver_id, tractor, trailer, cargo, route):
    with _lock, _conn() as conn:
        conn.execute(
            """UPDATE drivers SET last_tractor=?, last_trailer=?, last_cargo=?, last_route=?
               WHERE id=?""",
            (tractor, trailer, cargo, route, driver_id),
        )


# ---------- trips ----------

def create_trip(driver_id, tractor_number, trailer_number, cargo_description, route=""):
    with _lock, _conn() as conn:
        cur = conn.execute(
            """INSERT INTO trips (driver_id, tractor_number, trailer_number, cargo_description,
                                   route, status, current_step, started_at)
               VALUES (?,?,?,?,?, 'in_progress', 'loading_1', ?)""",
            (driver_id, tractor_number, trailer_number, cargo_description, route, now()),
        )
        return cur.lastrowid


def get_trip(trip_id):
    with _lock, _conn() as conn:
        return conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()


def get_active_trip_for_driver(driver_id):
    with _lock, _conn() as conn:
        return conn.execute(
            "SELECT * FROM trips WHERE driver_id=? AND status='in_progress' ORDER BY id DESC LIMIT 1",
            (driver_id,),
        ).fetchone()


def set_trip_step(trip_id, step_id):
    with _lock, _conn() as conn:
        conn.execute("UPDATE trips SET current_step=? WHERE id=?", (step_id, trip_id))


def ack_rules(trip_id):
    with _lock, _conn() as conn:
        conn.execute("UPDATE trips SET rules_ack_at=? WHERE id=?", (now(), trip_id))


def complete_step(trip_id, step_id):
    with _lock, _conn() as conn:
        conn.execute(
            """INSERT INTO step_progress (trip_id, step_id, completed_at)
               VALUES (?,?,?)
               ON CONFLICT(trip_id, step_id) DO UPDATE SET completed_at=excluded.completed_at""",
            (trip_id, step_id, now()),
        )


def finish_trip(trip_id, report_path=None, onedrive_url=None):
    with _lock, _conn() as conn:
        conn.execute(
            """UPDATE trips SET status='done', finished_at=?, report_path=?, onedrive_report_url=?
               WHERE id=?""",
            (now(), report_path, onedrive_url, trip_id),
        )


# ---------- checklist answers (mandatory steps) ----------

def toggle_item(trip_id, step_id, item_key):
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT * FROM checklist_answers WHERE trip_id=? AND item_key=?",
            (trip_id, item_key),
        ).fetchone()
        if row and row["checked_at"]:
            conn.execute("UPDATE checklist_answers SET checked_at=NULL WHERE id=?", (row["id"],))
            return False
        elif row:
            conn.execute("UPDATE checklist_answers SET checked_at=? WHERE id=?", (now(), row["id"]))
            return True
        else:
            conn.execute(
                """INSERT INTO checklist_answers (trip_id, step_id, item_key, checked_at)
                   VALUES (?,?,?,?)""",
                (trip_id, step_id, item_key, now()),
            )
            return True


def mark_item_checked(trip_id, step_id, item_key):
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT * FROM checklist_answers WHERE trip_id=? AND item_key=?",
            (trip_id, item_key),
        ).fetchone()
        if row:
            conn.execute("UPDATE checklist_answers SET checked_at=? WHERE id=?", (now(), row["id"]))
        else:
            conn.execute(
                """INSERT INTO checklist_answers (trip_id, step_id, item_key, checked_at)
                   VALUES (?,?,?,?)""",
                (trip_id, step_id, item_key, now()),
            )


def get_checked_keys(trip_id):
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT item_key FROM checklist_answers WHERE trip_id=? AND checked_at IS NOT NULL",
            (trip_id,),
        ).fetchall()
        return {r["item_key"] for r in rows}


def get_all_answers(trip_id):
    with _lock, _conn() as conn:
        return conn.execute(
            "SELECT * FROM checklist_answers WHERE trip_id=? ORDER BY step_id, id", (trip_id,)
        ).fetchall()


# ---------- photos ----------

def add_photo(trip_id, step_id, item_key, file_path, kind="checklist", onedrive_url=None):
    with _lock, _conn() as conn:
        cur = conn.execute(
            """INSERT INTO photos (trip_id, step_id, item_key, kind, file_path, onedrive_url, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (trip_id, step_id, item_key, kind, file_path, onedrive_url, now()),
        )
        return cur.lastrowid


def get_photos(trip_id):
    with _lock, _conn() as conn:
        return conn.execute("SELECT * FROM photos WHERE trip_id=? ORDER BY id", (trip_id,)).fetchall()


# ---------- optional repeatable events: pause / extra stop ----------

def add_optional_event(trip_id, event_type, context_step, note, checked_texts):
    with _lock, _conn() as conn:
        cur = conn.execute(
            """INSERT INTO optional_events (trip_id, event_type, context_step, note, items_checked_json, created_at)
               VALUES (?,?,?,?,?,?)""",
            (trip_id, event_type, context_step, note, json.dumps(checked_texts, ensure_ascii=False), now()),
        )
        return cur.lastrowid


def get_optional_events(trip_id, event_type=None):
    with _lock, _conn() as conn:
        if event_type:
            return conn.execute(
                "SELECT * FROM optional_events WHERE trip_id=? AND event_type=? ORDER BY id",
                (trip_id, event_type),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM optional_events WHERE trip_id=? ORDER BY id", (trip_id,)
        ).fetchall()


# ---------- incidents (ЧП) ----------

def add_incident(trip_id, context_step, description):
    with _lock, _conn() as conn:
        cur = conn.execute(
            """INSERT INTO incidents (trip_id, context_step, description, created_at)
               VALUES (?,?,?,?)""",
            (trip_id, context_step, description, now()),
        )
        return cur.lastrowid


def get_incidents(trip_id):
    with _lock, _conn() as conn:
        return conn.execute("SELECT * FROM incidents WHERE trip_id=? ORDER BY id", (trip_id,)).fetchall()
