from pathlib import Path
from uuid import uuid4

import pytest

from recipe_mcp.errors import AppError
from recipe_mcp.models import Ingredient, Recipe
from recipe_mcp.repository import Repository
from recipe_mcp.service import RecipeService


def recipe(identifier: str) -> Recipe:
    return Recipe(
        id=identifier,
        name=f"Recipe {identifier}",
        cuisine="British",
        category="Vegetarian",
        instructions="Simmer until tender.",
        ingredients=[Ingredient(ingredient="Beans", measure="200 g")],
    )


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def search(self, dish_name: str, max_results: int) -> list[Recipe]:
        self.calls.append(("search", (dish_name, max_results)))
        return [recipe("1"), recipe("1"), recipe("2")]

    async def by_letter(self, letter: str, max_results: int) -> list[Recipe]:
        self.calls.append(("by_letter", (letter, max_results)))
        return [recipe("3")]

    async def get(self, recipe_id: str) -> Recipe:
        self.calls.append(("get", recipe_id))
        if recipe_id == "404":
            raise AppError("recipe_not_found", "Recipe was not found.")
        return recipe(recipe_id)

    async def random(self) -> Recipe:
        self.calls.append(("random", None))
        return recipe("5")


@pytest.fixture
def service(tmp_path: Path) -> RecipeService:
    repository = Repository(tmp_path / "recipes.sqlite3")
    repository.initialize()
    return RecipeService(repository, FakeProvider())


@pytest.mark.asyncio
async def test_every_fetch_path_persists_and_search_deduplicates(service: RecipeService) -> None:
    results = await service.search_recipes(" beans ", 10)
    assert results.count == 2
    assert results.recipe_ids == ["1", "2"]
    assert [item.id for item in results.recipes] == ["1", "2"]
    assert service.provider.calls[0] == ("search", ("beans", 10))
    assert (await service.search_by_first_letter(" B ")).recipe_ids == ["3"]
    assert (await service.get_recipe_details("4")).id == "4"
    assert (await service.get_random_recipe()).id == "5"
    assert service.repository.stats()["recipes"] == 5
    assert all(service.repository.get_recipe(str(number)) is not None for number in range(1, 6))


@pytest.mark.asyncio
async def test_search_bounds_even_a_misbehaving_provider(service: RecipeService) -> None:
    results = await service.search_recipes("beans", 1)
    assert results.recipe_ids == ["1"]
    assert service.repository.stats()["recipes"] == 1


@pytest.mark.asyncio
async def test_details_use_cache_before_provider(service: RecipeService) -> None:
    first = await service.get_recipe_details("7")
    assert await service.get_recipe_details(" 7 ") == first
    assert service.provider.calls == [("get", "7")]


@pytest.mark.asyncio
async def test_missing_plan_recipes_are_fetched_and_plan_is_retrievable(
    service: RecipeService,
) -> None:
    plan = await service.create_meal_plan(" Week ", ["7", "8", "7"], "safe-key")
    assert plan.plan_name == "Week"
    assert plan.total_recipes == 2
    assert service.provider.calls == [("get", "7"), ("get", "8")]
    assert await service.get_meal_plan(plan.id) == plan
    assert await service.create_meal_plan("Week", ["7", "8"], "safe-key") == plan
    assert service.provider.calls == [("get", "7"), ("get", "8")]


@pytest.mark.asyncio
async def test_failed_fetch_never_creates_partial_plan(service: RecipeService) -> None:
    with pytest.raises(AppError) as captured:
        await service.create_meal_plan("Week", ["7", "404"], "failed-key")
    assert captured.value.code == "recipe_not_found"
    assert service.repository.list_plans() == []
    # Successful reads may populate the cache even though the plan transaction fails.
    assert service.repository.get_recipe("7") is not None


@pytest.mark.asyncio
async def test_idempotency_conflict_precedes_unnecessary_upstream_fetch(
    service: RecipeService,
) -> None:
    await service.create_meal_plan("Week", ["7"], "used-key")
    with pytest.raises(AppError) as captured:
        await service.create_meal_plan("Week", ["404"], "used-key")
    assert captured.value.code == "idempotency_conflict"
    assert service.provider.calls == [("get", "7")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,maximum", [("", 10), ("x" * 101, 10), ("x", 0), ("x", 26), ("x", True)]
)
async def test_invalid_search_never_reaches_provider(
    service: RecipeService, query: str, maximum: int
) -> None:
    with pytest.raises(AppError) as captured:
        await service.search_recipes(query, maximum)
    assert captured.value.code == "invalid_input"
    assert service.provider.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("letter", ["ab", "1", "é", "", " "])
async def test_first_letter_validation(service: RecipeService, letter: str) -> None:
    with pytest.raises(AppError) as captured:
        await service.search_by_first_letter(letter)
    assert captured.value.code == "invalid_input"
    assert service.provider.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", ["not-a-number", "1" * 13, "١", ""])
async def test_recipe_id_validation(service: RecipeService, identifier: str) -> None:
    with pytest.raises(AppError) as captured:
        await service.get_recipe_details(identifier)
    assert captured.value.code == "invalid_input"
    assert service.provider.calls == []


@pytest.mark.asyncio
async def test_missing_plan_and_invalid_plan_id_have_distinct_errors(
    service: RecipeService,
) -> None:
    with pytest.raises(AppError) as missing:
        await service.get_meal_plan(str(uuid4()))
    assert missing.value.code == "meal_plan_not_found"
    with pytest.raises(AppError) as invalid:
        await service.get_meal_plan("../../etc/passwd")
    assert invalid.value.code == "invalid_input"


@pytest.mark.asyncio
async def test_upstream_recipe_id_mismatch_is_not_cached(service: RecipeService) -> None:
    class WrongIdProvider(FakeProvider):
        async def get(self, recipe_id: str) -> Recipe:
            return recipe("123")

    service.provider = WrongIdProvider()
    with pytest.raises(AppError) as captured:
        await service.get_recipe_details("7")
    assert captured.value.code == "upstream_invalid_response"
    assert service.repository.stats()["recipes"] == 0
