from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app.models.tables import Base

API_ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("bootstrap", ["legacy", "current"])
def test_alembic_adopts_preexisting_development_schema(
    tmp_path: Path, bootstrap: str
) -> None:
    database = tmp_path / f"{bootstrap}.db"
    if bootstrap == "legacy":
        _create_legacy_memory_schema(database)
    else:
        engine = create_engine(f"sqlite:///{database}")
        Base.metadata.create_all(engine)
        engine.dispose()

    environment = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{database}",
        "LIVEKIT_URL": "wss://example.livekit.cloud",
        "LIVEKIT_API_KEY": "test-key",
        "LIVEKIT_API_SECRET": "test-secret-test-secret-test-32",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=API_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    with sqlite3.connect(database) as connection:
        version = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        memory_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(memories)")
        }
        session_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(conversation_sessions)")
        }
        oauth_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(oauth_connections)")
        }
        artifact_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(artifacts)")
        }
        scheduled_job_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(scheduled_jobs)")
        }
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert version == ("0009",)
    assert {"memory_type", "status", "confidence", "source", "updated_at"} <= memory_columns
    assert {"topics", "outcomes", "open_threads", "importance"} <= session_columns
    assert "timezone_name" in oauth_columns
    assert {"user_id", "storage_key", "sha256", "artifact_metadata"} <= artifact_columns
    assert "locked_at" in scheduled_job_columns
    assert {
        "memory_evidence",
        "memory_events",
        "page_contexts",
        "oauth_connections",
        "oauth_states",
        "pending_actions",
        "action_audits",
        "workflow_runs",
        "scheduled_jobs",
        "artifacts",
        "user_settings",
        "notifications",
    } <= tables


def _create_legacy_memory_schema(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE conversation_sessions (
                id VARCHAR(32) PRIMARY KEY,
                user_id VARCHAR(128) NOT NULL,
                room_name VARCHAR(128),
                started_at DATETIME NOT NULL,
                ended_at DATETIME,
                summary TEXT
            );
            CREATE TABLE conversation_turns (
                id VARCHAR(32) PRIMARY KEY,
                session_id VARCHAR(32) NOT NULL,
                user_id VARCHAR(128) NOT NULL,
                role VARCHAR(16) NOT NULL,
                text TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                created_at DATETIME NOT NULL
            );
            CREATE TABLE user_profiles (
                user_id VARCHAR(128) PRIMARY KEY,
                summary TEXT,
                preferences TEXT,
                updated_at DATETIME NOT NULL
            );
            CREATE TABLE memories (
                id VARCHAR(32) PRIMARY KEY,
                user_id VARCHAR(128) NOT NULL,
                content TEXT NOT NULL,
                source_session_id VARCHAR(32),
                created_at DATETIME NOT NULL,
                last_used_at DATETIME,
                deleted_at DATETIME
            );
            CREATE INDEX ix_conversation_sessions_user_id
                ON conversation_sessions (user_id);
            CREATE INDEX ix_sessions_user_started
                ON conversation_sessions (user_id, started_at);
            CREATE INDEX ix_conversation_turns_session_id
                ON conversation_turns (session_id);
            CREATE INDEX ix_conversation_turns_user_id
                ON conversation_turns (user_id);
            CREATE INDEX ix_turns_session_seq
                ON conversation_turns (session_id, sequence);
            CREATE INDEX ix_memories_user_id ON memories (user_id);
            CREATE INDEX ix_memories_user_created
                ON memories (user_id, created_at);
            """
        )
