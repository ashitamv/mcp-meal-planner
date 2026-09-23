"""Conservative, exact shopping-list consolidation from stored recipe ingredients.

Ingredient names are matched literally after Unicode/case/whitespace normalization.
Only explicit, context-independent unit conversions are allowed: a cup, for example,
has no universal metric size. Unsupported measures retain their full provenance for
human review instead of being guessed or silently omitted.
"""

import re
import unicodedata
from collections import defaultdict
from fractions import Fraction
from typing import Literal

from .errors import AppError
from .models import DomainModel, MealPlan, Recipe


class IngredientSource(DomainModel):
    recipe_id: str
    recipe_name: str
    ingredient: str
    measure: str


class ShoppingListItem(DomainModel):
    ingredient: str
    quantity: str
    unit: str
    sources: list[IngredientSource]


class UnresolvedIngredient(IngredientSource):
    reason_code: str
    message: str


class ShoppingList(DomainModel):
    plan_id: str
    plan_name: str
    recipe_count: int
    ingredient_source: Literal["current_saved_recipes"] = "current_saved_recipes"
    items: list[ShoppingListItem]
    unresolved: list[UnresolvedIngredient]
    warnings: list[str]
    requires_review: bool


_MAX_MEASURE_LENGTH = 128
_MAX_NUMBER_COMPONENT = 10**12
_MAX_INPUT_QUANTITY = 10**9
_MAX_RATIONAL_COMPONENT = 10**18
_NUMBER = r"(?:[0-9]+\s+[0-9]+\s*/\s*[0-9]+|[0-9]+\s*/\s*[0-9]+|[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
_MEASURE = re.compile(rf"(?P<quantity>{_NUMBER})\s*(?P<unit>[^0-9]*)")
_VULGAR_FRACTIONS = {
    "¼": "1/4",
    "½": "1/2",
    "¾": "3/4",
    "⅐": "1/7",
    "⅑": "1/9",
    "⅒": "1/10",
    "⅓": "1/3",
    "⅔": "2/3",
    "⅕": "1/5",
    "⅖": "2/5",
    "⅗": "3/5",
    "⅘": "4/5",
    "⅙": "1/6",
    "⅚": "5/6",
    "⅛": "1/8",
    "⅜": "3/8",
    "⅝": "5/8",
    "⅞": "7/8",
}


def _unit_aliases() -> dict[str, tuple[str, Fraction]]:
    groups = (
        ("g", Fraction(1, 1000), ("mg", "milligram", "milligrams")),
        ("g", Fraction(1), ("g", "gram", "grams")),
        ("g", Fraction(1000), ("kg", "kilogram", "kilograms")),
        ("ml", Fraction(1), ("ml", "milliliter", "milliliters", "millilitre", "millilitres")),
        ("ml", Fraction(1000), ("l", "liter", "liters", "litre", "litres")),
        ("oz", Fraction(1), ("oz", "ounce", "ounces")),
        ("oz", Fraction(16), ("lb", "lbs", "pound", "pounds")),
        ("tsp", Fraction(1), ("tsp", "tsps", "teaspoon", "teaspoons")),
        ("tbsp", Fraction(1), ("tbsp", "tbsps", "tablespoon", "tablespoons")),
        ("cup", Fraction(1), ("cup", "cups")),
        ("clove", Fraction(1), ("clove", "cloves")),
        ("leaf", Fraction(1), ("leaf", "leaves")),
        ("piece", Fraction(1), ("piece", "pieces")),
    )
    aliases = {"": ("count", Fraction(1))}
    for unit, factor, names in groups:
        for name in names:
            aliases[name] = unit, factor
            aliases[f"{name}."] = unit, factor
    return aliases


_UNITS = _unit_aliases()


class _MeasureError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _normalize_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value.casefold()).split())


def _parse_quantity(value: str) -> Fraction:
    # Check components before Fraction construction, including long decimal tails.
    components = re.findall(r"[0-9]+", value)
    if any(len(part) > 12 or int(part) > _MAX_NUMBER_COMPONENT for part in components):
        raise _MeasureError("quantity_out_of_bounds", "Quantity exceeds supported numeric limits.")
    compact = re.sub(r"\s*/\s*", "/", value)
    try:
        if " " in compact:
            whole, part = compact.split()
            fraction = Fraction(part)
            if not 0 < fraction < 1:
                raise ValueError("Mixed-number fraction must be proper.")
            quantity = Fraction(whole) + fraction
        else:
            quantity = Fraction(compact)
    except (ValueError, ZeroDivisionError) as exc:
        raise _MeasureError("invalid_quantity", "Quantity is not a valid positive number.") from exc
    if quantity <= 0:
        raise _MeasureError("invalid_quantity", "Quantity must be greater than zero.")
    if (
        quantity > _MAX_INPUT_QUANTITY
        or quantity.numerator > _MAX_NUMBER_COMPONENT
        or quantity.denominator > _MAX_NUMBER_COMPONENT
    ):
        raise _MeasureError("quantity_out_of_bounds", "Quantity exceeds supported numeric limits.")
    return quantity


def _parse_measure(measure: str) -> tuple[Fraction, str]:
    if len(measure) > _MAX_MEASURE_LENGTH:
        raise _MeasureError("quantity_out_of_bounds", "Measure exceeds the 128-character limit.")
    if any(unicodedata.category(char).startswith("C") for char in measure):
        raise _MeasureError(
            "unsupported_measure", "Measure contains unsupported control characters."
        )
    if not measure.strip():
        raise _MeasureError(
            "missing_quantity", "No quantity was supplied; review the original recipe."
        )
    normalized = unicodedata.normalize("NFC", measure).casefold().replace("⁄", "/")
    for char, fraction in _VULGAR_FRACTIONS.items():
        normalized = normalized.replace(char, f" {fraction}")
    normalized = " ".join(normalized.split())
    match = _MEASURE.fullmatch(normalized)
    if match is None:
        raise _MeasureError(
            "unsupported_measure",
            "Use an exact positive quantity and supported unit; ranges and qualifiers need review.",
        )
    quantity = _parse_quantity(match["quantity"])
    unit_text = match["unit"].strip()
    if unit_text not in _UNITS:
        raise _MeasureError(
            "unsupported_unit",
            "Unit or qualifier is unsupported; package sizes and preparation details need review.",
        )
    unit, factor = _UNITS[unit_text]
    return quantity * factor, unit


def _format_quantity(quantity: Fraction) -> str:
    """Render terminating decimals exactly; keep other rational totals as fractions."""
    denominator = quantity.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        return str(quantity)
    places = max(twos, fives)
    if places == 0:
        return str(quantity.numerator)
    scaled = quantity.numerator * 2 ** (places - twos) * 5 ** (places - fives)
    digits = str(scaled).zfill(places + 1)
    return f"{digits[:-places]}.{digits[-places:]}".rstrip("0").rstrip(".")


def _unresolved(source: IngredientSource, code: str, message: str) -> UnresolvedIngredient:
    return UnresolvedIngredient(**source.model_dump(), reason_code=code, message=message)


def build_shopping_list(plan: MealPlan, recipes: list[Recipe]) -> ShoppingList:
    """Consolidate each unique plan recipe once, without network access or mutation.

    Callers must provide the complete set of currently stored recipe details. A
    missing recipe or ingredient list fails the operation, rather than returning
    an apparently complete shopping list with missing purchases.
    """
    plan_ids = list(dict.fromkeys(recipe.id for recipe in plan.recipes))
    by_id = {recipe.id: recipe for recipe in recipes}
    if (
        not plan_ids
        or len(by_id) != len(recipes)
        or set(by_id) != set(plan_ids)
        or any(not recipe.ingredients for recipe in recipes)
    ):
        raise AppError(
            "incomplete_recipe",
            "Every saved plan recipe must have complete stored ingredient details.",
        )

    groups: dict[tuple[str, str], list[tuple[Fraction, IngredientSource, int]]] = defaultdict(list)
    unresolved_with_order: list[tuple[int, UnresolvedIngredient]] = []
    source_order = 0
    for recipe_id in plan_ids:
        recipe = by_id[recipe_id]
        for ingredient in recipe.ingredients:
            source = IngredientSource(
                recipe_id=recipe.id,
                recipe_name=recipe.name,
                ingredient=ingredient.ingredient,
                measure=ingredient.measure,
            )
            try:
                quantity, unit = _parse_measure(ingredient.measure)
            except _MeasureError as exc:
                unresolved_with_order.append(
                    (source_order, _unresolved(source, exc.code, exc.message))
                )
            else:
                groups[_normalize_name(ingredient.ingredient), unit].append(
                    (quantity, source, source_order)
                )
            source_order += 1

    items: list[ShoppingListItem] = []
    units_by_name: dict[str, set[str]] = defaultdict(set)
    for (name, unit), entries in sorted(groups.items()):
        total = Fraction(0)
        for quantity, _source, _order in entries:
            total += quantity
            if max(total.numerator, total.denominator) > _MAX_RATIONAL_COMPONENT:
                unresolved_with_order.extend(
                    (
                        order,
                        _unresolved(
                            source,
                            "quantity_out_of_bounds",
                            "Combined quantity exceeds numeric limits; review these measures.",
                        ),
                    )
                    for _, source, order in entries
                )
                break
        else:
            items.append(
                ShoppingListItem(
                    ingredient=name,
                    quantity=_format_quantity(total),
                    unit=unit,
                    sources=[source for _, source, _ in entries],
                )
            )
            units_by_name[name].add(unit)

    warnings = [
        f"'{name}' has separate totals in {', '.join(sorted(units))}; no conversion was assumed."
        for name, units in sorted(units_by_name.items())
        if len(units) > 1
    ]
    unresolved = [entry for _, entry in sorted(unresolved_with_order, key=lambda item: item[0])]
    unresolved_names = {_normalize_name(entry.ingredient) for entry in unresolved}
    warnings.extend(
        f"Known totals for '{name}' exclude unresolved measurements; review before shopping."
        for name in sorted(unresolved_names.intersection(units_by_name))
    )
    return ShoppingList(
        plan_id=plan.id,
        plan_name=plan.plan_name,
        recipe_count=len(plan_ids),
        items=items,
        unresolved=unresolved,
        warnings=warnings,
        requires_review=bool(unresolved or warnings),
    )
