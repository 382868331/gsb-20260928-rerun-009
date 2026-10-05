from __future__ import annotations

import hashlib
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
                CREATE TABLE IF NOT EXISTS joint_plans (
                    id INTEGER PRIMARY KEY,
                    project_id INTEGER NOT NULL,
                    objective TEXT NOT NULL,
                    request TEXT NOT NULL,
                    result TEXT NOT NULL,
                    task_ids TEXT NOT NULL,
                    commitments TEXT NOT NULL,
                    map_fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    adopted_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id)
                );
                CREATE TABLE IF NOT EXISTS plans (
                    id INTEGER PRIMARY KEY,
                    project_id INTEGER NOT NULL,
                    result TEXT NOT NULL,
                    state TEXT NOT NULL,
                    map_grid TEXT,
                    map_resolution REAL,
                    map_version TEXT,
                    map_fingerprint TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(id)
                );
                """
            )
            self._migrate_plans(conn)

    def _migrate_plans(self, conn: sqlite3.Connection) -> None:
        """老库可能缺少地图快照列；按需补齐，缺列绝不被解释成新结论。"""

        existing = {row["name"] for row in conn.execute("PRAGMA table_info(plans)")}
        for column, declaration in (
            ("map_grid", "TEXT"),
            ("map_resolution", "REAL"),
            ("map_version", "TEXT"),
            ("map_fingerprint", "TEXT"),
        ):
            if column not in existing:
                conn.execute(f"ALTER TABLE plans ADD COLUMN {column} {declaration}")

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

    def save_plan(
        self,
        project_id: int,
        result: dict[str, Any],
        state: dict[str, Any],
        map_grid: list[list[bool]] | None = None,
        map_resolution: float | None = None,
        map_version: str | None = None,
        map_checksum: str | None = None,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO plans
                (project_id, result, state, map_grid, map_resolution,
                 map_version, map_fingerprint, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    json.dumps(result, ensure_ascii=False),
                    json.dumps(state, ensure_ascii=False),
                    None if map_grid is None else json.dumps(map_grid),
                    map_resolution,
                    map_version,
                    map_checksum,
                    utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def list_plans(self, project_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, result, state, map_grid, map_resolution,
                       map_version, map_fingerprint, created_at
                FROM plans WHERE project_id = ? ORDER BY id DESC LIMIT 20
                """,
                (project_id,),
            ).fetchall()
        return rows

    def get_plan(self, plan_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()

    def save_joint_candidate(
        self,
        project_id: int,
        objective: str,
        request: dict[str, Any],
        result: dict[str, Any],
        task_ids: list[str],
        commitments: dict[str, float],
        map_fingerprint: str,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO joint_plans
                (project_id, objective, request, result, task_ids,
                 commitments, map_fingerprint, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    objective,
                    json.dumps(request, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False),
                    json.dumps(task_ids, ensure_ascii=False),
                    json.dumps(commitments, ensure_ascii=False),
                    map_fingerprint,
                    utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def list_joint_candidates(self, project_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT * FROM joint_plans WHERE project_id = ?
                    ORDER BY id DESC LIMIT 20
                    """,
                    (project_id,),
                ).fetchall()
            )

    def get_joint_candidate(self, candidate_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM joint_plans WHERE id = ?", (candidate_id,)
            ).fetchone()

    def mark_joint_adopted(self, candidate_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE joint_plans SET adopted_at = ? WHERE id = ?",
                (utc_now(), candidate_id),
            )

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


def map_fingerprint(grid: list[list[bool]], resolution: float, state: dict[str, Any]) -> str:
    """结论所依据的地图修订与机器人参数指纹。"""

    payload = {
        "grid": grid,
        "resolution": resolution,
        "robot_width": state.get("robot_width"),
        "robot_length": state.get("robot_length"),
        "origin": state.get("origin"),
        "forbidden_zones": state.get("forbidden_zones", []),
        "timed_zones": state.get("timed_zones", []),
        "waiting_zones": state.get("waiting_zones", []),
        "checkpoints": state.get("checkpoints", []),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
