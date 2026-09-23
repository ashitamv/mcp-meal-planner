"""Shopping totals must be exact, conservative, and traceable to every source."""

from collections import Counter
from fractions import Fraction

import pytest

from recipe_mcp.errors import AppError
from recipe_mcp.models import Ingredient, MealPlan, Recipe
from recipe_mcp.shopping import build_shopping_list


def recipe(identifier: str, *ingredients: tuple[str, str]) -> Recipe:
    return Recipe(
        id=identifier,
        name=f"Recipe {identifier}",
        cuisine="Test cuisine",
        category="Vegetarian",
        instructions="Cook the ingredients.",
        ingredients=[Ingredient(ingredient=name, measure=measure) for name, measure in ingredients],
    )


def plan_for(*recipes: Recipe) -> MealPlan:
    return MealPlan(
        id="14d1134d-1e3f-44f4-bb96-41176cda85b6",
        plan_name="Weeknight Dinners",
        created_at="2026-09-23T15:31:00+00:00",
        recipes=[item.summary() for item in recipes],
        total_recipes=len(recipes),
    )


def quantities(result) -> dict[tuple[str, str], Fraction]:
    return {(item.ingredient, item.unit): Fraction(item.quantity) for item in result.items}


def test_compatible_units_combine_with_exact_arithmetic_and_original_provenance():
    first = recipe("1", ("FLOUR", "0.25 kg"), ("Milk", "1 l"), ("Rice", "1 lb"), ("Oil", "0.1 cup"))
    second = recipe(
        "2",
        ("flour", "250 g"),
        ("Flour", "500 mg"),
        ("milk", "250 ml"),
        ("rice", "8 oz"),
        ("oil", "0.2 cups"),
    )
    plan = plan_for(first, second)

    result = build_shopping_list(plan, [first, second])

    assert quantities(result) == {
        ("flour", "g"): Fraction(1001, 2),
        ("milk", "ml"): Fraction(1250),
        ("rice", "oz"): Fraction(24),
        ("oil", "cup"): Fraction(3, 10),
    }
    assert result.plan_id == plan.id
    assert result.plan_name == plan.plan_name
    assert result.recipe_count == 2
    assert result.ingredient_source == "current_saved_recipes"
    assert result.unresolved == []
    assert result.requires_review is False
    flour = next(item for item in result.items if item.ingredient == "flour")
    assert [source.model_dump() for source in flour.sources] == [
        {"recipe_id": "1", "recipe_name": "Recipe 1", "ingredient": "FLOUR", "measure": "0.25 kg"},
        {"recipe_id": "2", "recipe_name": "Recipe 2", "ingredient": "flour", "measure": "250 g"},
        {"recipe_id": "2", "recipe_name": "Recipe 2", "ingredient": "Flour", "measure": "500 mg"},
    ]


@pytest.mark.parametrize(
    ("measure", "expected"),
    [
        ("2", Fraction(2)),
        (".5", Fraction(1, 2)),
        ("0.125", Fraction(1, 8)),
        ("1/3", Fraction(1, 3)),
        ("1 1/2", Fraction(3, 2)),
        ("½", Fraction(1, 2)),
        ("1½", Fraction(3, 2)),
        ("1 ½", Fraction(3, 2)),
        ("⅔", Fraction(2, 3)),
    ],
)
def test_complete_supported_quantity_formats_are_exact(measure, expected):
    source = recipe("1", ("Apples", measure))
    result = build_shopping_list(plan_for(source), [source])

    assert quantities(result) == {("apples", "count"): expected}
    assert result.unresolved == []
    assert result.requires_review is False


def test_nonterminating_fraction_totals_remain_exact():
    source = recipe("1", ("Rice", "1/3 cup"), ("rice", "1/3 cup"))
    result = build_shopping_list(plan_for(source), [source])

    assert quantities(result) == {("rice", "cup"): Fraction(2, 3)}


def test_aliases_and_count_units_merge_only_with_the_same_dimension():
    source = recipe(
        "1",
        ("Garlic", "2 cloves"),
        ("garlic", "1 clove"),
        ("Basil", "3 leaves"),
        ("basil", "1 leaf"),
        ("Ginger", "1 piece"),
        ("ginger", "2 pieces"),
        ("Salt", "1 teaspoon"),
        ("salt", "1 tsp"),
        ("Oil", "2 tablespoons"),
        ("oil", "1 tbsp"),
    )
    result = build_shopping_list(plan_for(source), [source])

    assert quantities(result) == {
        ("garlic", "clove"): Fraction(3),
        ("basil", "leaf"): Fraction(4),
        ("ginger", "piece"): Fraction(3),
        ("salt", "tsp"): Fraction(2),
        ("oil", "tbsp"): Fraction(3),
    }
    assert result.requires_review is False


@pytest.mark.parametrize(
    "measure",
    [
        "",
        "to taste",
        "1 tin",
        "400g tin",
        "1-2 cups",
        "about 1 cup",
        "~1 cup",
        "1 cup chopped",
        "200g drained",
        "1 scoop",
        "0 g",
        "-2 g",
        "1/0 cup",
        "1/2/3 cup",
        "1 3/2 cup",
        "grams",
        "1\nkg",
    ],
)
def test_ambiguous_or_invalid_measurements_are_preserved_for_review(measure):
    source = recipe("1", ("Beans", measure))
    result = build_shopping_list(plan_for(source), [source])

    assert result.items == []
    assert len(result.unresolved) == 1
    unresolved = result.unresolved[0]
    assert unresolved.recipe_id == "1"
    assert unresolved.recipe_name == "Recipe 1"
    assert unresolved.ingredient == "Beans"
    assert unresolved.measure == source.ingredients[0].measure
    assert unresolved.reason_code
    assert unresolved.message
    assert result.requires_review is True


def test_large_quantity_text_is_reported_without_unbounded_integer_parsing():
    source = recipe("1", ("Flour", "9" * 10_000 + " g"))
    result = build_shopping_list(plan_for(source), [source])

    assert result.items == []
    assert len(result.unresolved) == 1
    assert result.unresolved[0].measure == source.ingredients[0].measure
    assert result.requires_review is True


def test_aggregate_numeric_bound_preserves_every_source_without_a_partial_total():
    source = recipe(
        "1",
        ("Salt", "1/999999999989 g"),
        ("salt", "to taste"),
        ("SALT", "1/999999999967 g"),
        ("Salt", "1 g"),
        ("Rice", "200 g"),
    )
    result = build_shopping_list(plan_for(source), [source])

    assert quantities(result) == {("rice", "g"): Fraction(200)}
    assert [item.measure for item in result.unresolved] == [
        "1/999999999989 g",
        "to taste",
        "1/999999999967 g",
        "1 g",
    ]
    assert [item.reason_code for item in result.unresolved if item.measure != "to taste"] == [
        "quantity_out_of_bounds",
        "quantity_out_of_bounds",
        "quantity_out_of_bounds",
    ]
    assert all(item.recipe_id == "1" for item in result.unresolved)
    assert result.requires_review is True


def test_incompatible_units_stay_separate_and_require_review():
    source = recipe(
        "1",
        ("Oil", "1 tsp"),
        ("oil", "1 tbsp"),
        ("OIL", "1 cup"),
        ("oil", "30 ml"),
        ("oil", "20 g"),
    )
    result = build_shopping_list(plan_for(source), [source])

    assert quantities(result) == {
        ("oil", "tsp"): Fraction(1),
        ("oil", "tbsp"): Fraction(1),
        ("oil", "cup"): Fraction(1),
        ("oil", "ml"): Fraction(30),
        ("oil", "g"): Fraction(20),
    }
    assert result.unresolved == []
    assert result.warnings
    assert result.requires_review is True


def test_identity_normalizes_unicode_case_and_whitespace_without_guessing_synonyms():
    source = recipe(
        "1",
        ("Café   Sugar", "1 g"),
        ("CAFE\u0301 Sugar", "2 g"),
        ("Onion", "1"),
        ("Chopped onion", "2"),
        ("Onions", "3"),
    )
    result = build_shopping_list(plan_for(source), [source])

    assert quantities(result) == {
        ("café sugar", "g"): Fraction(3),
        ("onion", "count"): Fraction(1),
        ("chopped onion", "count"): Fraction(2),
        ("onions", "count"): Fraction(3),
    }


def test_every_source_is_retained_when_known_and_unknown_amounts_are_mixed():
    first = recipe("1", ("Salt", "1 tsp"), ("salt", "to taste"), ("Beans", "1 tin"))
    second = recipe("2", ("SALT", "1/2 tsp"), ("Beans", "200 g"), ("Beans", "200 g"))
    result = build_shopping_list(plan_for(first, second), [first, second])

    assert quantities(result) == {("salt", "tsp"): Fraction(3, 2), ("beans", "g"): Fraction(400)}
    expected_sources = Counter(
        (item.id, item.name, ingredient.ingredient, ingredient.measure)
        for item in [first, second]
        for ingredient in item.ingredients
    )
    actual_sources = Counter(
        (source.recipe_id, source.recipe_name, source.ingredient, source.measure)
        for item in result.items
        for source in item.sources
    )
    actual_sources.update(
        (source.recipe_id, source.recipe_name, source.ingredient, source.measure)
        for source in result.unresolved
    )
    assert actual_sources == expected_sources
    assert result.warnings  # Numeric subtotals must not look like complete quantities.
    assert result.requires_review is True


def test_output_is_deterministic_and_does_not_mutate_inputs():
    first = recipe("1", ("Zucchini", "1"), ("Oil", "1 cup"))
    second = recipe("2", ("Beans", "2 g"), ("oil", "1 tbsp"), ("Salt", "to taste"))
    plan = plan_for(first, second)
    before = [plan.model_dump(), first.model_dump(), second.model_dump()]

    result = build_shopping_list(plan, [first, second])

    assert result == build_shopping_list(plan, [second, first])
    assert result == build_shopping_list(plan, [first, second])
    assert [(item.ingredient, item.unit) for item in result.items] == sorted(
        (item.ingredient, item.unit) for item in result.items
    )
    assert [plan.model_dump(), first.model_dump(), second.model_dump()] == before


@pytest.mark.parametrize("missing_kind", ["absent", "empty_ingredients"])
def test_missing_recipe_data_fails_instead_of_producing_a_partial_list(missing_kind):
    first = recipe("1", ("Beans", "200 g"))
    second = recipe("2", ("Rice", "100 g"))
    plan = plan_for(first, second)
    available = [first]
    if missing_kind == "empty_ingredients":
        available.append(second.model_copy(update={"ingredients": []}))

    with pytest.raises(AppError) as captured:
        build_shopping_list(plan, available)

    assert captured.value.code == "incomplete_recipe"
