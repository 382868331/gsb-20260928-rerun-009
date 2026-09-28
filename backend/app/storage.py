from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    resolution REAL NOT NULL,
                    threshold INTEGER NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    original_image BLOB NOT NULL,
                    map_data TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS versions (
                    id INTEGER PRIMARY KEY,
                    project_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(id)
                );
                """
            )

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def create_project(
        self,
        name: str,
        filename: str,
        content_type: str,
        resolution: float,
        threshold: int,
        image: bytes,
        width: int,
        height: int,
        grid: list[list[bool]],
        state: dict[str, Any],
    ) -> int:
        now = utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO projects
                (name, filename, content_type, resolution, threshold, width, height,
                 original_image, map_data, state, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    filename,
                    content_type,
                    resolution,
                    threshold,
                    width,
                    height,
                    image,
                    json.dumps({"grid": grid}),
                    json.dumps(state),
                    now,
                    now,
                ),
            )
            project_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO versions (project_id, name, state, created_at) VALUES (?, ?, ?, ?)",
                (project_id, "导入原图", json.dumps(state), now),
            )
        return project_id

    def get_project(self, project_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()

    def list_projects(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute("SELECT id, name, updated_at FROM projects ORDER BY id DESC"))

    def update_state(self, project_id: int, state: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE projects SET state = ?, updated_at = ? WHERE id = ?",
                (json.dumps(state), utc_now(), project_id),
            )

    def create_version(self, project_id: int, name: str, state: dict[str, Any]) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO versions (project_id, name, state, created_at) VALUES (?, ?, ?, ?)",
                (project_id, name, json.dumps(state), utc_now()),
            )
            conn.execute(
                "UPDATE projects SET state = ?, updated_at = ? WHERE id = ?",
                (json.dumps(state), utc_now(), project_id),
            )
            return int(cursor.lastrowid)

    def list_versions(self, project_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    "SELECT id, name, created_at FROM versions WHERE project_id = ? ORDER BY id DESC",
                    (project_id,),
                )
            )

    def get_version(self, version_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM versions WHERE id = ?", (version_id,)).fetchone()

    def restore_version(self, version_id: int) -> dict[str, Any] | None:
        version = self.get_version(version_id)
        if version is None:
            return None
        state = json.loads(version["state"])
        with self.connect() as conn:
            conn.execute(
                "UPDATE projects SET state = ?, updated_at = ? WHERE id = ?",
                (version["state"], utc_now(), version["project_id"]),
            )
        return state
