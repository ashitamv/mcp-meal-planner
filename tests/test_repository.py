import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from recipe_mcp.errors import AppError
from recipe_mcp.models import Ingredient, Recipe
from recipe_mcp.repository import Repository


def recipe(identifier: str = "1", name: str = "Bean stew", cuisine: str = "British") -> Recipe:
    return Recipe(
        id=identifier,
        name=name,
        cuisine=cuisine,
        category="Vegetarian",
        instructions="Simmer the beans until tender.",
        ingredients=[Ingredient(ingredient="Beans", measure="200 g")],
    )


@pytest.fixture
def repository(tmp_path: Path) -> Repository:
    repository = Repository(tmp_path / "nested" / "recipes.sqlite3")
    repository.initialize()
    return repository


def test_upserts_are_deduplicated_and_persistent(repository: Repository) -> None:
    repository.upsert_recipes([recipe(), recipe("2", cuisine="Italian"), recipe()])
    repository.upsert_recipes([recipe(name="New bean stew")])
    reopened = Repository(repository.path)
    reopened.initialize()
    assert reopened.get_recipe("1").name == "New bean stew"
    assert reopened.get_recipe("999") is None
    assert reopened.stats() == {"recipes": 2, "cuisines": 2, "meal_plans": 0}
    assert reopened.list_cuisines() == ["British", "Italian"]
    assert reopened.get_cuisine(" british ")[0].id == "1"
    assert reopened.get_cuisine("British' OR 1=1 --") == []


def test_schema_enables_wal_and_foreign_keys(repository: Repository) -> None:
    with repository._connection(write=True) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_unknown_recipe_rejects_the_entire_plan(repository: Repository) -> None:
    repository.upsert_recipes([recipe()])
    with pytest.raises(AppError) as captured:
        repository.create_plan("Weekly plan", ["1", "404"])
    assert captured.value.code == "recipe_not_found"
    assert repository.list_plans() == []


def test_plan_write_failure_rolls_back_header_and_items(repository: Repository) -> None:
    repository.upsert_recipes([recipe(), recipe("2")])
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """CREATE TRIGGER fail_second_item BEFORE INSERT ON meal_plan_recipes
               WHEN NEW.position = 1 BEGIN SELECT RAISE(ABORT, 'injected failure'); END"""
        )
    with pytest.raises(AppError) as captured:
        repository.create_plan("Rollback check", ["1", "2"], "rollback-key")
    assert captured.value.code == "storage_error"
    assert repository.stats()["meal_plans"] == 0
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM meal_plan_recipes").fetchone()[0] == 0


def test_plan_preserves_order_deduplicates_and_snapshots(repository: Repository) -> None:
    repository.upsert_recipes([recipe(), recipe("2", name="Tomato soup")])
    plan = repository.create_plan("  Weekly plan  ", ["2", "1", "2"])
    repository.upsert_recipes([recipe("2", name="Changed upstream name")])
    assert plan.plan_name == "Weekly plan"
    assert plan.total_recipes == 2
    assert [item.id for item in plan.recipes] == ["2", "1"]
    assert plan.created_at.endswith("+00:00")
    assert repository.get_plan(plan.id) == plan
    assert repository.get_plan(plan.id).recipes[0].name == "Tomato soup"
    assert repository.list_plans() == [plan]
    assert repository.get_plan("missing") is None


def test_same_idempotency_request_returns_original_plan(repository: Repository) -> None:
    repository.upsert_recipes([recipe()])
    original = repository.create_plan("Weekly plan", ["1"], "a-key")
    repeated = repository.create_plan(" Weekly plan ", ["1", "1"], " a-key ")
    assert repeated == original
    assert repository.stats()["meal_plans"] == 1


@pytest.mark.parametrize("plan_name,ids", [("Another name", ["1"]), ("Weekly plan", ["2"])])
def test_reusing_key_for_different_payload_conflicts(
    repository: Repository, plan_name: str, ids: list[str]
) -> None:
    repository.upsert_recipes([recipe(), recipe("2")])
    repository.create_plan("Weekly plan", ["1"], "a-key")
    with pytest.raises(AppError) as captured:
        repository.create_plan(plan_name, ids, "a-key")
    assert captured.value.code == "idempotency_conflict"
    assert repository.stats()["meal_plans"] == 1


def test_concurrent_idempotency_has_exactly_one_winner(repository: Repository) -> None:
    repository.upsert_recipes([recipe()])
    with ThreadPoolExecutor(max_workers=8) as executor:
        plans = list(
            executor.map(
                lambda _: repository.create_plan("Weekly plan", ["1"], "shared-key"), range(20)
            )
        )
    assert len({plan.id for plan in plans}) == 1
    assert repository.stats()["meal_plans"] == 1


def test_no_key_allows_distinct_plans_and_pagination(repository: Repository) -> None:
    repository.upsert_recipes([recipe()])
    first = repository.create_plan("Same name", ["1"])
    second = repository.create_plan("Same name", ["1"])
    assert first.id != second.id
    assert repository.list_plans(limit=1) == [second]
    assert repository.list_plans(limit=1, offset=1) == [first]


@pytest.mark.parametrize(
    "name,ids,key",
    [
        ("", ["1"], None),
        ("x" * 101, ["1"], None),
        ("Plan", [], None),
        ("Plan", ["1"] * 31, None),
        ("Plan", ["1 OR 1=1"], None),
        ("Plan", ["١"], None),
        ("Plan", ["1"], ""),
        ("Plan", ["1"], "x" * 101),
        ("Plan\nname", ["1"], None),
    ],
)
def test_plan_inputs_are_bounded(
    repository: Repository, name: str, ids: list[str], key: str | None
) -> None:
    with pytest.raises(AppError) as captured:
        repository.create_plan(name, ids, key)
    assert captured.value.code == "invalid_input"
    assert repository.stats()["meal_plans"] == 0


def test_future_database_schema_is_rejected(repository: Repository) -> None:
    with sqlite3.connect(repository.path) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(AppError, match="schema version"):
        repository.initialize()
