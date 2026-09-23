# Shopping lists from saved meal plans

`generate_shopping_list(plan_id)` converts the ingredients in a saved meal plan into a consolidated shopping list. It adds quantities only when ingredient identity and units are compatible, retains the source of every contribution, and exposes measurements that need a person's review.

This is a deterministic application feature. The MCP host can present the result, but a language model does not parse, convert, or add its quantities.

## Try it in your MCP host

Save a meal plan first, then ask:

> Generate a shopping list for the meal plan I just saved. Use `generate_shopping_list` with its plan ID. Show consolidated quantities, all unresolved ingredient measurements, and any unit warnings. Do not guess missing quantities.

For an explicit MCP tool call, replace this example UUID with a saved plan's identifier:

```json
{
  "plan_id": "123e4567-e89b-42d3-a456-426614174000"
}
```

After updating the application, restart the server in your MCP host so it discovers the seventh tool. Keep the database path configured for your existing plans. No database migration or new dependency is needed.

Generating a list does not create a new plan, update recipes, store a shopping list, or call TheMealDB. It can work without network access once the plan and its recipes are saved. Quantities apply once per unique recipe at its original batch size. The tool does not multiply quantities by servings or the number of days in a plan.

## What the result means

| Field | Meaning |
| --- | --- |
| `plan_id`, `plan_name` | The saved plan being read. |
| `recipe_count` | Number of unique recipes included. |
| `ingredient_source` | Always `current_saved_recipes`; ingredients come from the saved recipe records at the time of this read. |
| `items` | Consolidated ingredient, exact quantity string, canonical unit, and source ingredient lines. |
| `unresolved` | Original recipe and ingredient details, a stable reason code, and an explanation for each measurement that could not be safely consolidated. |
| `warnings` | Review messages, including cases where one ingredient has separate, incompatible units. |
| `requires_review` | `true` whenever any unresolved entry or warning exists. |

Every original ingredient line contributes to an item's `sources` or appears in `unresolved`. A low item count can mean successful consolidation; it must not mean ambiguous ingredients were silently discarded. A missing saved recipe or a recipe with no ingredients produces `incomplete_recipe` instead of a partial success. A missing plan produces `meal_plan_not_found`.

`requires_review: false` means that these parsing and compatibility rules found no review issue. It does not verify source accuracy, dietary suitability, serving sizes, stock availability, or the safety of a recipe.

## Example: exact consolidation with a review item

Suppose a saved plan contains these ingredient lines:

| Recipe | Ingredient | Original measure |
| --- | --- | --- |
| Fruit bake (`900001`) | sugar | `100 g` |
| Berry compote (`900002`) | sugar | `0.25 kg` |
| Berry compote (`900002`) | salt | `to taste` |

The sugar combines to `350 g`. The salt remains visible for review. This illustrative result excerpt omits plan metadata and the unresolved entry's explanatory `message`:

```json
{
  "ingredient_source": "current_saved_recipes",
  "items": [
    {
      "ingredient": "sugar",
      "quantity": "350",
      "unit": "g",
      "sources": [
        {
          "recipe_id": "900001",
          "recipe_name": "Fruit bake",
          "ingredient": "sugar",
          "measure": "100 g"
        },
        {
          "recipe_id": "900002",
          "recipe_name": "Berry compote",
          "ingredient": "sugar",
          "measure": "0.25 kg"
        }
      ]
    }
  ],
  "unresolved": [
    {
      "recipe_id": "900002",
      "recipe_name": "Berry compote",
      "ingredient": "salt",
      "measure": "to taste",
      "reason_code": "unsupported_measure"
    }
  ],
  "warnings": [],
  "requires_review": true
}
```

These illustrative recipes explain the output contract; they are not additional demo fixtures.

## Matching and quantity rules

Ingredient matching normalizes Unicode NFC, letter case, and whitespace only. For example, `Sugar` and ` sugar ` match. Qualifiers stay part of the identity: `salted butter` and `unsalted butter` remain different ingredients. Singular/plural names, synonyms, preparation methods, brands, and substitute ingredients are not inferred. `tomato` and `tomatoes` can therefore remain separate.

Positive integers, decimal quantities, fractions, mixed fractions, and supported Unicode vulgar fractions use exact rational arithmetic. A terminating quantity is returned as an exact decimal string; a nonterminating quantity uses a fraction string. For example, `1/3` plus `1/3` is `"2/3"`, not a rounded floating-point approximation. Quantity strings must be displayed or parsed as exact values by downstream clients, not treated as locale-formatted prose.

| Measurement family | Consolidation rule |
| --- | --- |
| Metric mass: `mg`, `g`, `kg` | Convert exactly to `g`. |
| Metric volume: `ml`, `l` | Convert exactly to `ml`. |
| Pound and ounce mass: `lb`, `oz` | Convert exactly to `oz`, with 16 ounces per pound. |
| `tsp`, `tbsp`, `cup` and their supported aliases | Add measurements of the same unit. Keep the three units separate. |
| Unqualified counts, cloves, leaves, pieces | Add within each count unit; do not convert between them. |
| Mass versus volume, or other different families | Keep separate and request review when they occur for the same ingredient. |

The parser recognizes a defined set of unit aliases; it is not a general natural-language measurement interpreter. It does not infer a regional cup or spoon standard, ingredient density, or package contents. For example, sugar measured in both `g` and `cup` produces separate items and a warning. `requires_review` will be `true` even if both measurements parsed individually.

Ranges such as `1–2`, packaging such as `1 tin`, qualifiers such as `heaped`, instructions such as `to taste`, unknown units, blank quantities, and invalid or nonpositive numbers remain unresolved. Original wording and recipe provenance are retained so a person can resolve them. The parser also bounds inputs and arithmetic to prevent unusually large measurements from consuming unbounded work.

| Reason code | Typical meaning |
| --- | --- |
| `missing_quantity` | No measurement was provided. |
| `unsupported_measure` | The full measurement cannot be interpreted conservatively, such as a range or `to taste`. |
| `unsupported_unit` | A quantity is present, but its unit is not supported. |
| `invalid_quantity` | The number is invalid or nonpositive. |
| `quantity_out_of_bounds` | The measurement exceeds the parser's limits. |

## Architecture and storage decisions

The MCP adapter validates the tool boundary, the service reads the relevant records, and the pure [`shopping.py`](../src/recipe_mcp/shopping.py) module handles quantity parsing and consolidation. The repository reads the plan and all its recipe details in one SQLite transaction, giving the calculation a consistent database snapshot. It does not acquire missing recipes over the network.

Saved meal plans already contain immutable recipe **summary** snapshots, not ingredient snapshots. This feature preserves that contract and schema version 1. Ingredient data comes from current saved recipe records. A later recipe refresh can change shopping quantities or source recipe names even though the plan's stored summaries remain unchanged. The explicit `ingredient_source` field makes that choice visible. Historical, reproducible shopping lists would require versioned recipe ingredients or a new ingredient snapshot stored with each plan.

The conservative rules trade automatic consolidation for explainable results. They can leave two equivalent ingredient names or units separate, but avoid inventing equivalence. Shopping-list generation remains read-only and does not require an idempotency key. The existing private, single-tenant and single-replica deployment limitations still apply.

## Three interview questions

**Why use exact arithmetic and explicit units instead of asking the model to make a shopping list?**

The host can explain the list, but application code must produce repeatable quantities. Exact rational arithmetic preserves fractions without floating-point drift. Explicit unit families prevent additions such as grams plus cups without density data. Keeping original source lines lets a reviewer trace every total and investigate a questionable measurement.

**Why keep ambiguous measurements instead of guessing or skipping them?**

An apparently complete list can be misleading if the application silently omits ingredients. The result separates calculable quantities from unresolved entries while retaining every ingredient line. A review flag makes that distinction usable by a host. Missing recipe data fails the operation because the server cannot establish completeness at all; an ambiguous but present measurement can be returned transparently for review.

**Does a saved plan guarantee the same shopping list forever?**

No. The plan snapshots recipe summaries, while this operation reads current saved ingredients in one transaction. That provides a consistent read at generation time, not historical ingredient versioning. I preserved compatibility with existing plans and exposed the data source in the response. If historical reproducibility became a requirement, I would snapshot or version ingredients, define an explicit refresh workflow, and migrate storage with tests for older plans.
