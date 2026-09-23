"""Application use cases independent of MCP and the recipe provider."""

import asyncio
from typing import Protocol
from uuid import UUID

from .errors import AppError
from .models import (
    MealPlan,
    Recipe,
    SearchResult,
    normalize_max_results,
    normalize_plan_request,
    normalize_recipe_id,
    normalize_text,
)
from .repository import Repository
from .shopping import ShoppingList, build_shopping_list


def _normalize_plan_id(plan_id: str) -> str:
    identifier = normalize_text(plan_id, "Meal plan ID", 36)
    try:
        return str(UUID(identifier))
    except ValueError as error:
        raise AppError("invalid_input", "Meal plan ID must be a valid UUID.") from error


class RecipeProvider(Protocol):
    async def search(self, dish_name: str, max_results: int) -> list[Recipe]: ...

    async def by_letter(self, letter: str, max_results: int) -> list[Recipe]: ...

    async def get(self, recipe_id: str) -> Recipe: ...

    async def random(self) -> Recipe: ...


class RecipeService:
    def __init__(self, repository: Repository, provider: RecipeProvider) -> None:
        self.repository = repository
        self.provider = provider

    async def _search_result(self, recipes: list[Recipe], maximum: int) -> SearchResult:
        unique = list({recipe.id: recipe for recipe in recipes}.values())[:maximum]
        await asyncio.to_thread(self.repository.upsert_recipes, unique)
        return SearchResult(
            recipe_ids=[recipe.id for recipe in unique],
            recipes=[recipe.summary() for recipe in unique],
            count=len(unique),
        )

    async def search_recipes(self, dish_name: str, max_results: int = 10) -> SearchResult:
        query = normalize_text(dish_name, "Dish name")
        maximum = normalize_max_results(max_results)
        recipes = await self.provider.search(query, maximum)
        return await self._search_result(recipes, maximum)

    async def search_by_first_letter(self, letter: str, max_results: int = 10) -> SearchResult:
        normalized = normalize_text(letter, "First letter", 1).lower()
        if not normalized.isascii() or not normalized.isalpha():
            raise AppError("invalid_input", "First letter must be one ASCII letter from A to Z.")
        maximum = normalize_max_results(max_results)
        recipes = await self.provider.by_letter(normalized, maximum)
        return await self._search_result(recipes, maximum)

    async def get_recipe_details(self, recipe_id: str) -> Recipe:
        identifier = normalize_recipe_id(recipe_id)
        if cached := await asyncio.to_thread(self.repository.get_recipe, identifier):
            return cached
        recipe = await self.provider.get(identifier)
        if recipe.id != identifier:
            raise AppError(
                "upstream_invalid_response", "Recipe provider returned an unexpected recipe ID."
            )
        await asyncio.to_thread(self.repository.upsert_recipes, [recipe])
        return recipe

    async def get_random_recipe(self) -> Recipe:
        recipe = await self.provider.random()
        await asyncio.to_thread(self.repository.upsert_recipes, [recipe])
        return recipe

    async def create_meal_plan(
        self, plan_name: str, recipe_ids: list[str], idempotency_key: str | None = None
    ) -> MealPlan:
        name, ids, key = normalize_plan_request(plan_name, recipe_ids, idempotency_key)
        try:
            # Resolve existing idempotency keys before making any upstream requests.
            return await asyncio.to_thread(self.repository.create_plan, name, ids, key)
        except AppError as error:
            if error.code != "recipe_not_found":
                raise
        # Fetching can enrich the cache, but the plan itself is committed all-or-nothing.
        for identifier in ids:
            await self.get_recipe_details(identifier)
        return await asyncio.to_thread(self.repository.create_plan, name, ids, key)

    async def get_meal_plan(self, plan_id: str) -> MealPlan:
        identifier = _normalize_plan_id(plan_id)
        plan = await asyncio.to_thread(self.repository.get_plan, identifier)
        if plan is None:
            raise AppError("meal_plan_not_found", "The requested meal plan was not found.")
        return plan

    async def generate_shopping_list(self, plan_id: str) -> ShoppingList:
        """Consolidate saved ingredients without network access or storage writes."""
        identifier = _normalize_plan_id(plan_id)

        def generate() -> ShoppingList:
            saved = self.repository.get_plan_with_recipes(identifier)
            if saved is None:
                raise AppError("meal_plan_not_found", "The requested meal plan was not found.")
            return build_shopping_list(*saved)

        return await asyncio.to_thread(generate)
