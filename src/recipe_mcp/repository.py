"""SQLite persistence with atomic meal plans, snapshots, and idempotent writes."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .errors import AppError
from .models import (
    MealPlan,
    Recipe,
    RecipeSummary,
    normalize_plan_request,
    normalize_recipe_id,
    normalize_text,
)


class Repository:
    """One connection per operation; safe for calls from concurrent worker threads.

    A file-backed database is required. WAL permits readers during writes, and
    BEGIN IMMEDIATE serializes idempotency checks with their associated inserts.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        if str(self.path) == ":memory:":
            raise ValueError("Repository requires a file-backed SQLite database.")

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except sqlite3.Error as error:
            if connection is not None:
                connection.rollback()
            retryable = getattr(error, "sqlite_errorcode", None) in {
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
            }
            raise AppError(
                "storage_error", "Recipe storage is temporarily unavailable.", retryable
            ) from error
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Journal mode changes must run outside an active transaction.
        try:
            with sqlite3.connect(self.path, timeout=10) as connection:
                connection.execute("PRAGMA journal_mode = WAL")
        except sqlite3.Error as error:
            raise AppError("storage_error", "Unable to initialize recipe storage.") from error
        with self._connection(write=True) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
                raise AppError("storage_error", "Unsupported recipe database schema version.")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS recipes (
                    id TEXT PRIMARY KEY,
                    cuisine TEXT NOT NULL COLLATE NOCASE,
                    recipe_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute("CREATE INDEX IF NOT EXISTS recipes_cuisine_idx ON recipes(cuisine)")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS meal_plans (
                    id TEXT PRIMARY KEY,
                    plan_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE,
                    request_json TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS meal_plan_recipes (
                    plan_id TEXT NOT NULL REFERENCES meal_plans(id) ON DELETE CASCADE,
                    recipe_id TEXT NOT NULL REFERENCES recipes(id),
                    position INTEGER NOT NULL CHECK(position >= 0),
                    summary_json TEXT NOT NULL,
                    PRIMARY KEY(plan_id, recipe_id),
                    UNIQUE(plan_id, position)
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS meal_plans_created_idx "
                "ON meal_plans(created_at DESC, id DESC)"
            )
            connection.execute("PRAGMA user_version = 1")

    def upsert_recipes(self, recipes: list[Recipe]) -> None:
        timestamp = datetime.now(UTC).isoformat()
        with self._connection(write=True) as connection:
            connection.executemany(
                """INSERT INTO recipes (id, cuisine, recipe_json, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       cuisine = excluded.cuisine,
                       recipe_json = excluded.recipe_json,
                       updated_at = excluded.updated_at""",
                [
                    (recipe.id, recipe.cuisine, recipe.model_dump_json(), timestamp)
                    for recipe in recipes
                ],
            )

    def get_recipe(self, recipe_id: str) -> Recipe | None:
        identifier = normalize_recipe_id(recipe_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT recipe_json FROM recipes WHERE id = ?", (identifier,)
            ).fetchone()
        return Recipe.model_validate_json(row["recipe_json"]) if row else None

    def list_cuisines(self) -> list[str]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT DISTINCT cuisine FROM recipes ORDER BY cuisine"
            ).fetchall()
        return [row["cuisine"] for row in rows]

    def get_cuisine(self, cuisine: str) -> list[Recipe]:
        name = normalize_text(cuisine, "Cuisine")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT recipe_json FROM recipes WHERE cuisine = ? ORDER BY id LIMIT 100", (name,)
            ).fetchall()
        return [Recipe.model_validate_json(row["recipe_json"]) for row in rows]

    def stats(self) -> dict[str, int]:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT
                    (SELECT COUNT(*) FROM recipes) AS recipes,
                    (SELECT COUNT(DISTINCT cuisine) FROM recipes) AS cuisines,
                    (SELECT COUNT(*) FROM meal_plans) AS meal_plans"""
            ).fetchone()
        return dict(row)

    def create_plan(
        self, plan_name: str, recipe_ids: list[str], idempotency_key: str | None = None
    ) -> MealPlan:
        name, ids, key = normalize_plan_request(plan_name, recipe_ids, idempotency_key)
        request_json = json.dumps({"plan_name": name, "recipe_ids": ids}, sort_keys=True)
        with self._connection(write=True) as connection:
            if key is not None:
                existing = connection.execute(
                    "SELECT * FROM meal_plans WHERE idempotency_key = ?", (key,)
                ).fetchone()
                if existing is not None:
                    if existing["request_json"] != request_json:
                        raise AppError(
                            "idempotency_conflict",
                            "This idempotency key was already used for a different meal plan.",
                        )
                    return self._read_plan(connection, existing)
            placeholders = ",".join("?" for _ in ids)
            rows = connection.execute(
                f"SELECT id, recipe_json FROM recipes WHERE id IN ({placeholders})", ids
            ).fetchall()
            recipes = {row["id"]: Recipe.model_validate_json(row["recipe_json"]) for row in rows}
            if missing := [identifier for identifier in ids if identifier not in recipes]:
                raise AppError(
                    "recipe_not_found", "Recipe IDs are not available: " + ", ".join(missing)
                )
            plan = MealPlan(
                id=str(uuid4()),
                plan_name=name,
                created_at=datetime.now(UTC).isoformat(),
                recipes=[recipes[identifier].summary() for identifier in ids],
                total_recipes=len(ids),
            )
            connection.execute(
                "INSERT INTO meal_plans (id, plan_name, created_at, idempotency_key, request_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (plan.id, name, plan.created_at, key, request_json),
            )
            connection.executemany(
                "INSERT INTO meal_plan_recipes (plan_id, recipe_id, position, summary_json) "
                "VALUES (?, ?, ?, ?)",
                [
                    (plan.id, recipe.id, position, recipe.model_dump_json())
                    for position, recipe in enumerate(plan.recipes)
                ],
            )
        return plan

    @staticmethod
    def _read_plan(connection: sqlite3.Connection, row: sqlite3.Row) -> MealPlan:
        summaries = connection.execute(
            "SELECT summary_json FROM meal_plan_recipes WHERE plan_id = ? ORDER BY position",
            (row["id"],),
        ).fetchall()
        recipes = [RecipeSummary.model_validate_json(item["summary_json"]) for item in summaries]
        return MealPlan(
            id=row["id"],
            plan_name=row["plan_name"],
            created_at=row["created_at"],
            recipes=recipes,
            total_recipes=len(recipes),
        )

    def get_plan(self, plan_id: str) -> MealPlan | None:
        identifier = normalize_text(plan_id, "Meal plan ID", 36)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM meal_plans WHERE id = ?", (identifier,)
            ).fetchone()
            return self._read_plan(connection, row) if row else None

    def get_plan_with_recipes(self, plan_id: str) -> tuple[MealPlan, list[Recipe]] | None:
        """Read a plan and its current saved ingredients in one consistent snapshot.

        Schema v1 snapshots recipe summaries, not ingredients. Reading the saved
        recipe rows keeps existing plans usable without fetching or mutating data.
        """
        identifier = normalize_text(plan_id, "Meal plan ID", 36)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM meal_plans WHERE id = ?", (identifier,)
            ).fetchone()
            if row is None:
                return None
            plan = self._read_plan(connection, row)
            rows = connection.execute(
                "SELECT r.recipe_json FROM meal_plan_recipes AS p "
                "LEFT JOIN recipes AS r ON r.id = p.recipe_id "
                "WHERE p.plan_id = ? ORDER BY p.position",
                (identifier,),
            ).fetchall()
            if len(rows) != plan.total_recipes or any(row["recipe_json"] is None for row in rows):
                raise AppError(
                    "incomplete_recipe", "A recipe in this meal plan has no saved ingredient data."
                )
            recipes = [Recipe.model_validate_json(row["recipe_json"]) for row in rows]
            return plan, recipes

    def list_plans(self, limit: int = 50, offset: int = 0) -> list[MealPlan]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise AppError("invalid_input", "limit must be an integer from 1 to 100.")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise AppError("invalid_input", "offset must be a non-negative integer.")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM meal_plans ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [self._read_plan(connection, row) for row in rows]
