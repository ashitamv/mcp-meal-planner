"""Exercise shopping lists against persisted plans and the real MCP adapter."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from mcp import Client

from recipe_mcp.config import Settings
from recipe_mcp.errors import AppError
from recipe_mcp.models import Ingredient, Recipe
from recipe_mcp.provider import DemoProvider
from recipe_mcp.repository import Repository
from recipe_mcp.server import create_server
from recipe_mcp.service import RecipeService


def recipe(identifier: str, *ingredients: tuple[str, str]) -> Recipe:
    return Recipe(
        id=identifier,
        name=f"Recipe {identifier}",
        cuisine="Test cuisine",
        category="Vegetarian",
        instructions="Cook the ingredients.",
        ingredients=[Ingredient(ingredient=name, measure=measure) for name, measure in ingredients],
    )


def database_snapshot(path: Path) -> tuple[int, str]:
    with sqlite3.connect(path) as connection:
        return (
            connection.execute("PRAGMA user_version").fetchone()[0],
            "\n".join(connection.iterdump()),
        )


@pytest.fixture
def saved_service(tmp_path):
    repository = Repository(tmp_path / "existing.sqlite3")
    repository.initialize()
    repository.upsert_recipes(
        [
            recipe("1", ("Milk", "500 ml"), ("Salt", "1 tsp")),
            recipe("2", ("milk", "0.5 l"), ("Salt", "to taste")),
        ]
    )
    plan = repository.create_plan("Existing dinners", ["1", "2"], "existing-key")
    provider = AsyncMock(spec=DemoProvider)
    for method in (provider.search, provider.by_letter, provider.get, provider.random):
        method.side_effect = AssertionError("Shopping lists must not call the recipe provider")
    return RecipeService(repository, provider), plan


async def test_existing_v1_plan_is_read_in_one_transaction_without_writes_or_network(
    saved_service, monkeypatch
):
    service, plan = saved_service
    before = database_snapshot(service.repository.path)
    assert before[0] == 1
    connections = []
    original_connection = service.repository._connection

    @contextmanager
    def read_only_connection(*, write=False):
        assert write is False
        with original_connection() as connection:
            connection.execute("PRAGMA query_only = ON")
            connections.append(connection)
            yield connection

    monkeypatch.setattr(service.repository, "_connection", read_only_connection)

    result = await service.generate_shopping_list(f" {plan.id.upper()} ")

    assert len(connections) == 1
    assert result.plan_id == plan.id
    assert result.ingredient_source == "current_saved_recipes"
    assert result.recipe_count == 2
    assert result.requires_review is True
    assert next(item for item in result.items if item.ingredient == "milk").quantity == "1000"
    assert service.provider.mock_calls == []
    assert database_snapshot(service.repository.path) == before

    # A newly constructed repository can read the old plan without a migration
    # or an initialize call; the feature requires no new tables or saved list.
    restarted = RecipeService(Repository(service.repository.path), service.provider)
    assert await restarted.generate_shopping_list(plan.id) == result
    assert database_snapshot(service.repository.path) == before
    assert service.provider.mock_calls == []


async def test_current_recipe_details_are_explicit_even_when_plan_summary_is_older(saved_service):
    service, plan = saved_service
    updated = recipe("1", ("Milk", "750 ml"))
    updated.name = "Updated recipe name"
    service.repository.upsert_recipes([updated])

    result = await service.generate_shopping_list(plan.id)

    assert result.ingredient_source == "current_saved_recipes"
    milk = next(item for item in result.items if item.ingredient == "milk")
    assert milk.quantity == "1250"
    assert milk.sources[0].recipe_name == "Updated recipe name"
    assert milk.sources[0].measure == "750 ml"
    assert service.repository.get_plan(plan.id).recipes[0].name == "Recipe 1"
    assert service.provider.mock_calls == []


@pytest.mark.parametrize("missing_kind", ["missing_row", "empty_ingredients"])
async def test_incomplete_stored_recipe_fails_without_refetch_or_partial_result(
    saved_service, missing_kind
):
    service, plan = saved_service
    if missing_kind == "missing_row":
        # Simulate an externally damaged/legacy database. Preserve the plan's
        # summary so the service must notice the missing full recipe itself.
        with sqlite3.connect(service.repository.path) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("DELETE FROM recipes WHERE id = '1'")
    else:
        service.repository.upsert_recipes([recipe("1")])
    before = database_snapshot(service.repository.path)

    with pytest.raises(AppError) as captured:
        await service.generate_shopping_list(plan.id)

    assert captured.value.code == "incomplete_recipe"
    assert service.provider.mock_calls == []
    assert database_snapshot(service.repository.path) == before


@pytest.mark.parametrize("identifier", ["", "not-a-uuid", "../live.sqlite3", "x" * 37])
async def test_invalid_plan_id_is_rejected_before_reading_storage(
    saved_service, monkeypatch, identifier
):
    service, _ = saved_service

    def unexpected_read(*args, **kwargs):
        pytest.fail("Invalid input reached the repository")

    monkeypatch.setattr(service.repository, "get_plan_with_recipes", unexpected_read)

    with pytest.raises(AppError) as captured:
        await service.generate_shopping_list(identifier)

    assert captured.value.code == "invalid_input"
    assert service.provider.mock_calls == []


async def test_unknown_valid_uuid_has_distinct_not_found_error(saved_service):
    service, _ = saved_service
    with pytest.raises(AppError) as captured:
        await service.generate_shopping_list(str(uuid4()))

    assert captured.value.code == "meal_plan_not_found"
    assert service.provider.mock_calls == []


async def test_mcp_discovers_and_returns_typed_read_only_shopping_list(saved_service):
    service, plan = saved_service
    settings = Settings(_env_file=None, mode="demo", database_path=service.repository.path)
    before = database_snapshot(service.repository.path)

    async with Client(create_server(settings, service)) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        tool = tools["generate_shopping_list"]
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is False
        assert tool.input_schema["required"] == ["plan_id"]
        assert {
            "plan_id",
            "plan_name",
            "recipe_count",
            "ingredient_source",
            "items",
            "unresolved",
            "warnings",
            "requires_review",
        } <= tool.output_schema["properties"].keys()

        response = await client.call_tool("generate_shopping_list", {"plan_id": plan.id})
        assert response.is_error is False
        payload = response.structured_content
        assert payload is not None
        assert payload["plan_id"] == plan.id
        assert payload["plan_name"] == "Existing dinners"
        assert payload["ingredient_source"] == "current_saved_recipes"
        assert payload["recipe_count"] == 2
        assert payload["requires_review"] is True
        assert len(payload["unresolved"]) == 1
        assert payload["unresolved"][0]["measure"] == "to taste"
        assert payload["unresolved"][0]["reason_code"]
        assert payload["warnings"]
        milk = next(item for item in payload["items"] if item["ingredient"] == "milk")
        assert milk["quantity"] == "1000"
        assert milk["unit"] == "ml"
        assert [source["recipe_id"] for source in milk["sources"]] == ["1", "2"]
        repeated = await client.call_tool("generate_shopping_list", {"plan_id": plan.id})
        assert repeated.structured_content == payload

    assert database_snapshot(service.repository.path) == before
    assert service.provider.mock_calls == []


@pytest.mark.parametrize(
    ("identifier", "error_code"),
    [("not-a-uuid", "invalid_input"), (str(uuid4()), "meal_plan_not_found")],
)
async def test_mcp_invalid_and_missing_plans_return_structured_domain_errors(
    saved_service, identifier, error_code
):
    service, _ = saved_service
    settings = Settings(_env_file=None, mode="demo", database_path=service.repository.path)

    async with Client(create_server(settings, service)) as client:
        response = await client.call_tool("generate_shopping_list", {"plan_id": identifier})

    assert response.is_error is True
    assert response.structured_content is None
    assert f'"code": "{error_code}"' in response.content[0].text
    assert '"retryable": false' in response.content[0].text
    assert service.provider.mock_calls == []
