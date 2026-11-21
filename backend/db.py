import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from backend.config import settings


def parse_db_path(database_url: Optional[str] = None) -> str:
    """
    Extract file path from DATABASE_URL.
    Supports:
      - sqlite:///./data/app.db -> ./data/app.db
      - sqlite:////data/app.db  -> /data/app.db
      - :memory: or direct paths
    """
    url = database_url or settings.DATABASE_URL
    prefix = "sqlite:///"
    if url.startswith(prefix):
        return url[len(prefix):]
    return url


def get_db_path(database_url: Optional[str] = None) -> str:
    path = parse_db_path(database_url)
    ensure_db_dir(path)
    return path


def ensure_db_dir(db_path: str) -> None:
    if db_path and db_path != ":memory:":
        parent_dir = os.path.dirname(os.path.abspath(db_path))
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    target_path = db_path if db_path is not None else get_db_path()
    ensure_db_dir(target_path)
    conn = sqlite3.connect(target_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """
    Automatically creates the messages table if it doesn't exist.
    """
    target_path = db_path if db_path is not None else get_db_path()
    ensure_db_dir(target_path)
    with get_connection(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.commit()


def save_message(
    session_id: str,
    role: str,
    content: str,
    metadata: Optional[Any] = None,
    created_at: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """
    Saves a message to the SQLite database with ISO-8601 UTC timestamp.
    Returns the inserted row ID.
    """
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()

    metadata_str: Optional[str] = None
    if metadata is not None:
        if isinstance(metadata, str):
            metadata_str = metadata
        else:
            metadata_str = json.dumps(metadata)

    target_path = db_path if db_path is not None else get_db_path()
    init_db(target_path)
    with get_connection(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO messages (session_id, role, content, metadata, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, role, content, metadata_str, created_at),
        )
        conn.commit()
        return cursor.lastrowid


def session_exists(session_id: str, db_path: Optional[str] = None) -> bool:
    """
    Checks if any message exists for the given session_id.
    """
    target_path = db_path if db_path is not None else get_db_path()
    with get_connection(target_path) as conn:
        cursor = conn.cursor()
        # Verify table exists first
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        )
        if not cursor.fetchone():
            return False
        cursor.execute(
            "SELECT 1 FROM messages WHERE session_id = ? LIMIT 1",
            (session_id,),
        )
        return cursor.fetchone() is not None


def get_session_system_prompt(session_id: str, db_path: Optional[str] = None) -> Optional[str]:
    """
    Retrieves the system prompt for a session if one was stored.
    """
    target_path = db_path if db_path is not None else get_db_path()
    with get_connection(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        )
        if not cursor.fetchone():
            return None
        cursor.execute(
            """
            SELECT content FROM messages
            WHERE session_id = ? AND role = 'system'
            ORDER BY id DESC LIMIT 1
            """,
            (session_id,),
        )
        row = cursor.fetchone()
        return row["content"] if row else None


def set_session_system_prompt(session_id: str, content: str, db_path: Optional[str] = None) -> None:
    """
    Sets or updates the system prompt for a session.
    """
    target_path = db_path if db_path is not None else get_db_path()
    init_db(target_path)
    with get_connection(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM messages WHERE session_id = ? AND role = 'system' LIMIT 1",
            (session_id,),
        )
        row = cursor.fetchone()
        if row:
            cursor.execute(
                "UPDATE messages SET content = ? WHERE id = ?",
                (content, row["id"]),
            )
            conn.commit()
        else:
            save_message(
                session_id=session_id,
                role="system",
                content=content,
                db_path=target_path,
            )


def get_session_messages(
    session_id: str,
    exclude_system: bool = True,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetches all messages for a session_id ordered oldest-first (by id ASC).
    Optionally excludes system prompts.
    Formats items as:
      - role and content
      - metadata (parsed JSON dict) only if present.
    """
    target_path = db_path if db_path is not None else get_db_path()
    with get_connection(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        )
        if not cursor.fetchone():
            return []
        if exclude_system:
            cursor.execute(
                """
                SELECT id, session_id, role, content, metadata, created_at
                FROM messages
                WHERE session_id = ? AND role != 'system'
                ORDER BY id ASC
                """,
                (session_id,),
            )
        else:
            cursor.execute(
                """
                SELECT id, session_id, role, content, metadata, created_at
                FROM messages
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            )
        rows = cursor.fetchall()

    results: List[Dict[str, Any]] = []
    for row in rows:
        item: Dict[str, Any] = {
            "role": row["role"],
            "content": row["content"],
        }
        if row["metadata"]:
            try:
                item["metadata"] = json.loads(row["metadata"])
            except Exception:
                item["metadata"] = row["metadata"]
        results.append(item)

    return results


def check_db_health(db_path: Optional[str] = None) -> bool:
    """
    Quickly verifies DB connection and query execution.
    """
    try:
        target_path = db_path if db_path is not None else get_db_path()
        with get_connection(target_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            return cursor.fetchone()[0] == 1
    except Exception:
        return False
