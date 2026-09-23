"""Repeatable end-to-end demonstration through the MCP client, with no network."""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp import Client

from recipe_mcp.config import Settings
from recipe_mcp.server import create_server


async def main() -> None:
    with TemporaryDirectory(prefix="recipe-demo-") as directory:
        server = create_server(
            Settings(mode="demo", database_path=Path(directory) / "demo.sqlite3")
        )
        async with Client(server) as client:
            tools = await client.list_tools()
            print("Tools:", ", ".join(tool.name for tool in tools.tools))
            found = await client.call_tool("search_recipes", {"dish_name": "orzo"})
            assert not found.is_error and found.structured_content
            recipe_ids = found.structured_content["recipe_ids"]
            assert recipe_ids, "Offline orzo fixture is missing"
            print("Search:", json.dumps(found.structured_content, indent=2))
            detail = await client.call_tool("get_recipe_details", {"recipe_id": recipe_ids[0]})
            assert not detail.is_error
            arguments = {
                "recipe_ids": recipe_ids,
                "plan_name": "Portfolio demo",
                "idempotency_key": "portfolio-demo-1",
            }
            first = await client.call_tool("create_meal_plan", arguments)
            replay = await client.call_tool("create_meal_plan", arguments)
            assert not first.is_error and first.structured_content == replay.structured_content
            print("Plan:", json.dumps(first.structured_content, indent=2))
            print("Idempotency: replay returned the same plan")
            conflict = await client.call_tool(
                "create_meal_plan", {**arguments, "plan_name": "Changed request"}
            )
            assert conflict.is_error
            print("Conflict: changed request correctly rejected")
            stats = await client.read_resource("recipes://stats")
            print("Stored statistics:", stats.contents[0].text)
            shopping_plan = await client.call_tool(
                "create_meal_plan",
                {
                    "recipe_ids": ["900001", "900002"],
                    "plan_name": "Shopping list demo",
                    "idempotency_key": "shopping-demo-1",
                },
            )
            assert not shopping_plan.is_error and shopping_plan.structured_content
            before = await client.read_resource("recipes://stats")
            shopping = await client.call_tool(
                "generate_shopping_list", {"plan_id": shopping_plan.structured_content["id"]}
            )
            assert not shopping.is_error and shopping.structured_content
            payload = shopping.structured_content
            chickpeas = next(
                item
                for item in payload["items"]
                if item["ingredient"] == "chickpeas, cooked and drained"
            )
            oil = next(item for item in payload["items"] if item["ingredient"] == "olive oil")
            assert (chickpeas["quantity"], chickpeas["unit"]) == ("3", "cup")
            assert (oil["quantity"], oil["unit"]) == ("2", "tbsp")
            assert len(chickpeas["sources"]) == 2
            assert payload["requires_review"] and len(payload["unresolved"]) == 3
            after = await client.read_resource("recipes://stats")
            assert before.contents[0].text == after.contents[0].text
            print("Shopping list: 3 cups chickpeas, 2 tbsp olive oil; 3 measurements need review")
            for unresolved in payload["unresolved"]:
                print(f"  Review: {unresolved['ingredient']} — {unresolved['measure']}")
            print("Shopping list passed; recipe and plan counts unchanged by generation")
            print("Demo passed; temporary database removed after exit.")


if __name__ == "__main__":
    asyncio.run(main())
