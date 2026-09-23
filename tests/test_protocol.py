"""Verify portfolio workflows through the official SDK and a real subprocess."""

import json
import sys
from unittest.mock import AsyncMock

import pytest
from mcp import Client, MCPError, StdioServerParameters

from recipe_mcp.config import Settings
from recipe_mcp.models import MealPlan, Recipe, SearchResult
from recipe_mcp.provider import DemoProvider
from recipe_mcp.repository import Repository
from recipe_mcp.server import create_server
from recipe_mcp.service import RecipeService


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, mode="demo", database_path=tmp_path / "protocol.sqlite3")


@pytest.fixture
def client(settings):
    # Enter/exit the SDK AnyIO task group in the test task, not fixture tasks.
    return Client(create_server(settings), raise_exceptions=True)


async def resource_json(client, uri):
    result = await client.read_resource(uri)
    assert result.contents[0].mime_type == "application/json"
    return json.loads(result.contents[0].text)


async def test_discovery_exposes_typed_tools_resources_and_prompts(client):
    async with client as connected:
        tools = {tool.name: tool for tool in (await connected.list_tools()).tools}
        assert set(tools) == {
            "search_recipes",
            "search_by_first_letter",
            "get_recipe_details",
            "get_random_recipe",
            "create_meal_plan",
            "get_meal_plan",
            "generate_shopping_list",
        }
        search_schema = tools["search_recipes"].input_schema
        assert search_schema["required"] == ["dish_name"]
        assert search_schema["properties"]["max_results"]["maximum"] == 25
        assert search_schema["properties"]["max_results"]["minimum"] == 1
        assert tools["search_recipes"].output_schema
        assert tools["search_recipes"].annotations.read_only_hint is False
        assert tools["get_meal_plan"].annotations.read_only_hint is True
        assert tools["create_meal_plan"].annotations.destructive_hint is False

        resources = {resource.uri for resource in (await connected.list_resources()).resources}
        assert resources == {"recipes://cuisines", "recipes://meal-plans", "recipes://stats"}
        templates = {
            resource.uri_template
            for resource in (await connected.list_resource_templates()).resource_templates
        }
        assert templates == {"recipes://cuisine/{cuisine}", "recipes://recipe/{recipe_id}"}
        prompts = {prompt.name for prompt in (await connected.list_prompts()).prompts}
        assert prompts == {
            "generate_recipe_search_prompt",
            "generate_meal_planning_prompt",
            "generate_cooking_lesson_prompt",
            "generate_ingredient_exploration_prompt",
            "generate_cultural_cuisine_prompt",
        }


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("search_recipes", {"dish_name": "chickpea", "max_results": 2}),
        ("search_by_first_letter", {"letter": "C", "max_results": 2}),
        ("get_recipe_details", {"recipe_id": "900001"}),
        ("get_random_recipe", {}),
    ],
)
async def test_each_acquisition_path_can_save_and_read_a_complete_plan(client, tool, arguments):
    async with client as connected:
        acquired = await connected.call_tool(tool, arguments)
        assert acquired.is_error is False
        payload = acquired.structured_content
        assert payload is not None
        if tool.startswith("search"):
            search = SearchResult.model_validate(payload)
            assert search.count == len(search.recipe_ids) == len(search.recipes) > 0
            identifiers = search.recipe_ids
        else:
            recipe = Recipe.model_validate(payload)
            assert recipe.ingredients and recipe.instructions
            identifiers = [recipe.id]

        for recipe_id in identifiers:
            saved = Recipe.model_validate(
                await resource_json(connected, f"recipes://recipe/{recipe_id}")
            )
            assert saved.ingredients and saved.instructions
            assert saved.id == recipe_id

        created = await connected.call_tool(
            "create_meal_plan",
            {
                "recipe_ids": identifiers,
                "plan_name": "Portfolio dinner",
                "idempotency_key": "dinner",
            },
        )
        assert created.is_error is False
        plan = MealPlan.model_validate(created.structured_content)
        assert plan.total_recipes == len(identifiers)
        assert [recipe.id for recipe in plan.recipes] == identifiers
        assert all(recipe.name and recipe.cuisine and recipe.category for recipe in plan.recipes)
        fetched = await connected.call_tool("get_meal_plan", {"plan_id": plan.id})
        assert MealPlan.model_validate(fetched.structured_content) == plan
        listed = await resource_json(connected, "recipes://meal-plans")
        assert listed["meal_plans"][0]["id"] == plan.id
        stats = await resource_json(connected, "recipes://stats")
        assert stats["meal_plans"] == 1
        assert stats["recipes"] == len(identifiers)


async def test_plan_can_fetch_known_unsaved_ids_and_idempotency_survives_restart(settings):
    arguments = {
        "recipe_ids": ["900001", "900002", "900001"],
        "plan_name": "Weekly dinner",
        "idempotency_key": "weekly-dinner-v1",
    }
    async with Client(create_server(settings)) as client:
        first = await client.call_tool("create_meal_plan", arguments)
        assert first.is_error is False
        first_plan = MealPlan.model_validate(first.structured_content)
        assert first_plan.total_recipes == 2
        duplicate = await client.call_tool("create_meal_plan", arguments)
        assert duplicate.structured_content == first.structured_content
    async with Client(create_server(settings)) as restarted:
        retried = await restarted.call_tool("create_meal_plan", arguments)
        assert retried.structured_content == first.structured_content
        conflicting = await restarted.call_tool(
            "create_meal_plan", arguments | {"plan_name": "Different dinner"}
        )
        assert conflicting.is_error is True
        assert "idempotency_conflict" in conflicting.content[0].text
        assert (await resource_json(restarted, "recipes://stats"))["meal_plans"] == 1


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("search_recipes", {"dish_name": ""}),
        ("search_recipes", {"dish_name": "   "}),
        ("search_recipes", {"dish_name": "chickpea", "max_results": 26}),
        ("search_by_first_letter", {"letter": "CC"}),
        ("search_by_first_letter", {"letter": "é"}),
        ("get_recipe_details", {"recipe_id": "../../etc/passwd"}),
        ("create_meal_plan", {"recipe_ids": []}),
        ("create_meal_plan", {"recipe_ids": ["900001"], "plan_name": "\n"}),
        ("get_meal_plan", {"plan_id": "not-a-uuid"}),
    ],
)
async def test_invalid_arguments_produce_explicit_tool_errors(client, tool, arguments):
    async with client as connected:
        result = await connected.call_tool(tool, arguments)
        assert result.is_error is True
        assert result.structured_content is None
        assert result.content[0].text
        assert (await resource_json(connected, "recipes://stats"))["meal_plans"] == 0


async def test_unknown_recipe_aborts_entire_plan(client):
    async with client as connected:
        result = await connected.call_tool(
            "create_meal_plan",
            {"recipe_ids": ["900001", "999999999"], "plan_name": "Must not partially save"},
        )
        assert result.is_error is True
        assert "recipe_not_found" in result.content[0].text
        assert (await resource_json(connected, "recipes://meal-plans"))["meal_plans"] == []


async def test_resource_reads_use_saved_data_without_network_or_writes(tmp_path):
    repository = Repository(tmp_path / "saved.sqlite3")
    repository.initialize()
    recipe = await DemoProvider().get("900001")
    repository.upsert_recipes([recipe])
    provider = AsyncMock(spec=DemoProvider)
    service = RecipeService(repository, provider)
    settings = Settings(_env_file=None, mode="demo", database_path=repository.path)
    before = repository.stats()
    async with Client(create_server(settings, service)) as client:
        assert (await resource_json(client, "recipes://recipe/900001"))["id"] == "900001"
        assert (await resource_json(client, "recipes://cuisines"))["cuisines"] == [recipe.cuisine]
        cuisine = await resource_json(client, f"recipes://cuisine/{recipe.cuisine}")
        assert cuisine["recipes"][0]["id"] == recipe.id
        with pytest.raises(MCPError) as missing:
            await client.read_resource("recipes://recipe/999999999")
        assert missing.value.code == -32602
    assert repository.stats() == before
    assert provider.mock_calls == []


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("generate_recipe_search_prompt", {"cuisine_type": "Indian", "num_recipes": "2"}),
        ("generate_meal_planning_prompt", {"meal_type": "dinner", "people_count": "2"}),
        (
            "generate_cooking_lesson_prompt",
            {"skill_level": "beginner", "technique_focus": "sautéing"},
        ),
        ("generate_ingredient_exploration_prompt", {"main_ingredient": "chickpea"}),
        ("generate_cultural_cuisine_prompt", {"cuisine_name": "Indian"}),
    ],
)
async def test_prompts_supply_grounded_workflows_without_side_effects(client, name, arguments):
    async with client as connected:
        prompt = await connected.get_prompt(name, arguments)
        text = prompt.messages[0].content.text
        assert "Ask for confirmation before saving a plan" in text
        assert "Do not invent recipe IDs" in text
        assert "User parameters (JSON):" in text
        assert (await resource_json(connected, "recipes://stats"))["recipes"] == 0


async def test_stdio_subprocess_exposes_and_executes_the_installed_cli(tmp_path):
    process = StdioServerParameters(
        command=sys.executable,
        args=["-m", "recipe_mcp", "--demo"],
        env={"RECIPE_DATABASE_PATH": str(tmp_path / "stdio.sqlite3"), "RECIPE_LOG_LEVEL": "ERROR"},
    )
    async with Client(process) as client:
        assert len((await client.list_tools()).tools) == 7
        result = await client.call_tool("search_recipes", {"dish_name": "chickpea"})
        assert result.is_error is False
        assert result.structured_content["recipe_ids"] == ["900001"]
        plan = await client.call_tool(
            "create_meal_plan", {"recipe_ids": ["900001", "900002"], "plan_name": "Stdio shopping"}
        )
        assert not plan.is_error
        shopping = await client.call_tool(
            "generate_shopping_list", {"plan_id": plan.structured_content["id"]}
        )
        assert not shopping.is_error
        chickpeas = next(
            item
            for item in shopping.structured_content["items"]
            if item["ingredient"] == "chickpeas, cooked and drained"
        )
        assert (chickpeas["quantity"], chickpeas["unit"]) == ("3", "cup")
        assert shopping.structured_content["requires_review"] is True
