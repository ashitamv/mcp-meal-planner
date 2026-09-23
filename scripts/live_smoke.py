"""Optional read-only upstream smoke; do not include in deterministic CI."""

import asyncio

import httpx

from recipe_mcp.config import Settings
from recipe_mcp.provider import MealDBProvider


async def main() -> None:
    settings = Settings()
    async with httpx.AsyncClient() as client:
        provider = MealDBProvider(client, api_key=settings.api_key.get_secret_value())
        try:
            recipes = await provider.search("Arrabiata", 1)
            if not recipes:
                raise RuntimeError("Live provider returned no recipes for the smoke query")
            recipe = await provider.get(recipes[0].id)
            assert recipe.id == recipes[0].id and recipe.ingredients
            print(
                f"Live smoke passed: {recipe.id} {recipe.name}, "
                f"{len(recipe.ingredients)} ingredients"
            )
        finally:
            await provider.aclose()


if __name__ == "__main__":
    asyncio.run(main())
