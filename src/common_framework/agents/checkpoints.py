"""Durable workflow state machine checkpointing using Python's standard library sqlite3."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence


@dataclass
class Checkpoint:
    """Snapshot of workflow state at a specific graph step."""

    thread_id: str
    checkpoint_id: str
    node: str
    step: int
    state: dict[str, Any]
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BaseCheckpointSaver(ABC):
    """Abstract base class for workflow state checkpoint persisters."""

    @abstractmethod
    def save(
        self,
        thread_id: str,
        node: str,
        step: int,
        state: dict[str, Any],
        checkpoint_id: Optional[str] = None,
    ) -> Checkpoint:
        """Persist a state snapshot and return the Checkpoint record."""
        raise NotImplementedError

    @abstractmethod
    def get(
        self,
        thread_id: str,
        checkpoint_id: Optional[str] = None,
    ) -> Optional[Checkpoint]:
        """Retrieve the latest checkpoint for a thread, or a specific checkpoint_id."""
        raise NotImplementedError

    @abstractmethod
    def list(self, thread_id: str) -> list[Checkpoint]:
        """List all historical checkpoints for a given thread in chronological order."""
        raise NotImplementedError


class SQLiteCheckpointSaver(BaseCheckpointSaver):
    """ACID-compliant SQLite checkpoint store with zero external dependencies."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        if db_path != ":memory:":
            resolved = Path(db_path).resolve()
            resolved.parent.mkdir(parents=True, exist_ok=True)
            self._path_str = str(resolved)
        else:
            self._path_str = ":memory:"

        self._conn = sqlite3.connect(self._path_str, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute("PRAGMA journal_mode = WAL;")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_checkpoints (
                thread_id TEXT NOT NULL,
                checkpoint_id TEXT NOT NULL,
                node TEXT NOT NULL,
                step INTEGER NOT NULL,
                state_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (thread_id, checkpoint_id)
            );
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_workflow_checkpoints_thread_step
            ON workflow_checkpoints (thread_id, step ASC);
            """
        )
        self._conn.commit()

    def save(
        self,
        thread_id: str,
        node: str,
        step: int,
        state: dict[str, Any],
        checkpoint_id: Optional[str] = None,
    ) -> Checkpoint:
        chk_id = checkpoint_id or str(uuid.uuid4())
        now = time.time()
        state_serialized = json.dumps(state, default=str)

        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO workflow_checkpoints
            (thread_id, checkpoint_id, node, step, state_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (thread_id, chk_id, node, step, state_serialized, now),
        )
        self._conn.commit()

        return Checkpoint(
            thread_id=thread_id,
            checkpoint_id=chk_id,
            node=node,
            step=step,
            state=json.loads(state_serialized),
            created_at=now,
        )

    def get(
        self,
        thread_id: str,
        checkpoint_id: Optional[str] = None,
    ) -> Optional[Checkpoint]:
        cursor = self._conn.cursor()
        if checkpoint_id:
            cursor.execute(
                """
                SELECT thread_id, checkpoint_id, node, step, state_json, created_at
                FROM workflow_checkpoints
                WHERE thread_id = ? AND checkpoint_id = ?;
                """,
                (thread_id, checkpoint_id),
            )
        else:
            cursor.execute(
                """
                SELECT thread_id, checkpoint_id, node, step, state_json, created_at
                FROM workflow_checkpoints
                WHERE thread_id = ?
                ORDER BY step DESC, created_at DESC
                LIMIT 1;
                """,
                (thread_id,),
            )

        row = cursor.fetchone()
        if row is None:
            return None

        return Checkpoint(
            thread_id=row["thread_id"],
            checkpoint_id=row["checkpoint_id"],
            node=row["node"],
            step=row["step"],
            state=json.loads(row["state_json"]),
            created_at=row["created_at"],
        )

    def list(self, thread_id: str) -> list[Checkpoint]:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT thread_id, checkpoint_id, node, step, state_json, created_at
            FROM workflow_checkpoints
            WHERE thread_id = ?
            ORDER BY step ASC, created_at ASC;
            """,
            (thread_id,),
        )
        rows = cursor.fetchall()
        return [
            Checkpoint(
                thread_id=r["thread_id"],
                checkpoint_id=r["checkpoint_id"],
                node=r["node"],
                step=r["step"],
                state=json.loads(r["state_json"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def close(self) -> None:
        """Close database connection."""
        try:
            self._conn.close()
        except Exception:
            pass
