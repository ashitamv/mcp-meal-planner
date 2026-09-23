"""Validated domain objects and request normalization shared by all adapters."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .errors import AppError

RecipeId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{1,12}$")]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Ingredient(DomainModel):
    ingredient: NonEmptyText
    measure: str


class RecipeSummary(DomainModel):
    id: RecipeId
    name: NonEmptyText
    cuisine: NonEmptyText
    category: NonEmptyText


class Recipe(RecipeSummary):
    instructions: NonEmptyText
    ingredients: list[Ingredient]
    image_url: str | None = None
    youtube_url: str | None = None
    source_url: str | None = None
    tags: list[str] = Field(default_factory=list)

    def summary(self) -> RecipeSummary:
        return RecipeSummary(
            id=self.id, name=self.name, cuisine=self.cuisine, category=self.category
        )


class SearchResult(DomainModel):
    recipe_ids: list[RecipeId]
    recipes: list[RecipeSummary]
    count: int = Field(ge=0)


class MealPlan(DomainModel):
    id: str
    plan_name: NonEmptyText
    created_at: str
    recipes: list[RecipeSummary]
    total_recipes: int = Field(ge=1)


def normalize_text(value: str, label: str, maximum: int = 100) -> str:
    """Reject unsuitable values before they reach storage or the network."""
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise AppError("invalid_input", f"{label} must contain 1–{maximum} characters.")
    normalized = value.strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise AppError("invalid_input", f"{label} must not contain control characters.")
    return normalized


def normalize_recipe_id(value: str) -> str:
    normalized = normalize_text(value, "Recipe ID", 12)
    if not normalized.isascii() or not normalized.isdigit():
        raise AppError("invalid_input", "Recipe ID must contain only ASCII digits.")
    return normalized


def normalize_plan_request(
    plan_name: str, recipe_ids: list[str], idempotency_key: str | None
) -> tuple[str, list[str], str | None]:
    name = normalize_text(plan_name, "Plan name")
    if not isinstance(recipe_ids, list) or not 1 <= len(recipe_ids) <= 30:
        raise AppError("invalid_input", "A meal plan must contain 1–30 recipe IDs.")
    # Duplicates are one recipe; preserve user-supplied order for stable snapshots.
    ids = list(dict.fromkeys(normalize_recipe_id(value) for value in recipe_ids))
    key = (
        normalize_text(idempotency_key, "Idempotency key") if idempotency_key is not None else None
    )
    return name, ids, key


def normalize_max_results(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 25:
        raise AppError("invalid_input", "max_results must be an integer from 1 to 25.")
    return value
