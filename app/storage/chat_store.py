"""Persistencia de conversaciones en SQLite.

Dos tablas: sessions (una fila por session_id) y messages (cada mensaje de usuario o
asistente, con un JSON de metadatos del turno). El contenido del usuario se guarda
enmascarado (ver app/guardrails/response_validators.mask_sensitive_data).
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.models.schemas import ChatSessionSummary, StoredMessage

SUMMARY_MAX_CHARS = 50

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def summarize(text: str, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


class ChatStore:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def append_turn(
        self,
        session_id: str,
        user_content: str,
        assistant_content: str,
        user_metadata: Optional[dict[str, Any]] = None,
        assistant_metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Guarda un turno completo (mensaje del usuario + respuesta) en una transacción."""
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (session_id, created_at) VALUES (?, ?)", (session_id, _now())
            )
            for role, content, metadata in [
                ("user", user_content, user_metadata),
                ("assistant", assistant_content, assistant_metadata),
            ]:
                conn.execute(
                    "INSERT INTO messages (session_id, role, content, timestamp, metadata) VALUES (?, ?, ?, ?, ?)",
                    (session_id, role, content, _now(), json.dumps(metadata or {}, ensure_ascii=False)),
                )

    def history(self, session_id: str) -> list[StoredMessage]:
        """Mensajes de la sesión en orden cronológico ([] si no existe)."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT role, content, timestamp, metadata FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [
            StoredMessage(
                role=r["role"], content=r["content"], timestamp=r["timestamp"], metadata=json.loads(r["metadata"])
            )
            for r in rows
        ]

    def session_exists(self, session_id: str) -> bool:
        with closing(self._connect()) as conn:
            return conn.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)).fetchone() is not None

    def list_sessions(self) -> list[ChatSessionSummary]:
        """Sesiones con su primer mensaje de usuario (resumen) y la hora del último mensaje."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT s.session_id,
                       (SELECT content FROM messages m WHERE m.session_id = s.session_id AND m.role = 'user'
                        ORDER BY m.id LIMIT 1) AS first_user_message,
                       (SELECT MAX(timestamp) FROM messages m WHERE m.session_id = s.session_id) AS last_message_at
                FROM sessions s
                ORDER BY last_message_at DESC
                """
            ).fetchall()
        return [
            ChatSessionSummary(
                session_id=r["session_id"],
                summary=summarize(r["first_user_message"] or ""),
                last_message_at=r["last_message_at"] or "",
            )
            for r in rows
        ]
