# -*- coding: utf-8 -*-
"""
Постійне сховище стану діалогу (FSM) на SQLite — замінює MemoryStorage.

Навіщо: MemoryStorage тримає стан ("чекаю відповідь на Авто таке ж?" тощо)
тільки в оперативній пам'яті процесу. Кожен redeploy/рестарт на Render
(в т.ч. звичайний передеплой після git push) обнуляє її — і кнопки, що
чекали на конкретний FSM-стан, перестають на щось реагувати (натискання
"зависає", бо жоден хендлер не підходить під уже втрачений стан).
SQLite-файл переживає рестарт процесу (лежить на persistent disk /
локально), тож діалог, перерваний на середині, коректно продовжується.
"""
import json
import sqlite3
import threading
from typing import Any, Mapping

from aiogram.fsm.storage.base import BaseStorage, StorageKey

from config import DB_PATH

_lock = threading.Lock()


def _conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init():
    with _lock, _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fsm_storage (
                storage_key TEXT PRIMARY KEY,
                state TEXT,
                data TEXT NOT NULL DEFAULT '{}'
            )
            """
        )


def _key_to_str(key: StorageKey) -> str:
    return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.thread_id}:{key.business_connection_id}:{key.destiny}"


class SQLiteStorage(BaseStorage):
    def __init__(self):
        _init()

    async def set_state(self, key: StorageKey, state=None) -> None:
        state_str = state.state if hasattr(state, "state") else state
        k = _key_to_str(key)
        with _lock, _conn() as conn:
            conn.execute(
                """INSERT INTO fsm_storage (storage_key, state, data) VALUES (?,?,'{}')
                   ON CONFLICT(storage_key) DO UPDATE SET state=excluded.state""",
                (k, state_str),
            )

    async def get_state(self, key: StorageKey):
        k = _key_to_str(key)
        with _lock, _conn() as conn:
            row = conn.execute("SELECT state FROM fsm_storage WHERE storage_key=?", (k,)).fetchone()
        return row["state"] if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        k = _key_to_str(key)
        payload = json.dumps(dict(data), ensure_ascii=False)
        with _lock, _conn() as conn:
            conn.execute(
                """INSERT INTO fsm_storage (storage_key, state, data) VALUES (?, NULL, ?)
                   ON CONFLICT(storage_key) DO UPDATE SET data=excluded.data""",
                (k, payload),
            )

    async def get_data(self, key: StorageKey) -> dict:
        k = _key_to_str(key)
        with _lock, _conn() as conn:
            row = conn.execute("SELECT data FROM fsm_storage WHERE storage_key=?", (k,)).fetchone()
        if not row or not row["data"]:
            return {}
        try:
            return json.loads(row["data"])
        except Exception:
            return {}

    async def close(self) -> None:
        pass
