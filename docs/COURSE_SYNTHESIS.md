# Course synthesis and implementation traceability

Course: **Model Context Protocol Unlocked — From Fundamentals to Advanced Customization**, Paulo Dichone.

This synthesis is grounded in the publisher's public source repository at commit [`12ae65cf869c1105aa102b917f19a95fbaee87d2`](https://github.com/PacktPublishing/Model-Context-Protocol-Unlocked-From-Fundamentals-to-Advanced-Customization/tree/12ae65cf869c1105aa102b917f19a95fbaee87d2). All six Python source files, the three README files, and both project manifests were inspected. The official MCP Python SDK documentation and v1-to-v2 migration guide were also read. **No course videos were watched and no private O'Reilly transcript was available.** Statements about course content below refer to examples present in that source snapshot.

The rebuild preserves the recipe capstone's purpose and its use of tools, resources, and prompts. Its reliability features and production engineering decisions are additions to the reference implementation. This document is an architecture and traceability record, not a test-results report.

## What the course source demonstrates

| Example | Pattern demonstrated | Evidence |
| --- | --- | --- |
| Minimal name server | Register a Python function as an MCP tool; derive discoverable input descriptions from types and docstrings; run a local server. | [hello_mcp.py][hello] |
| HTTP name server | Expose the same kind of tool through Streamable HTTP. Transport changes how the host connects; it does not change the business operation. | [stream_tester.py][stream] |
| Analysis prompt | Return a reusable, parameterized instruction template. The host is responsible for running the resulting workflow. | [mcp_prompt.py][prompt-example] |
| Library resources | Expose domain data under resource URIs; parameterize a category URI; derive availability, overdue, and inventory statistics. The dataset is an in-memory fixture. | [mcp_resources.py][resources-example] |
| Geographic database | Wrap relational queries with narrowly named tools; bind SQL values as parameters; map SQLite rows to structured results. | [sqlite_server.py][sqlite-example] |
| Recipe capstone | Combine external API access, persisted collections and meal plans, readable resources, reusable prompts, and HTTP deployment settings. | [recipe_server.py][capstone] |

The geographic example contains fifteen tools. Its README provides example host questions, including coordinate proximity queries for which the source does not implement a corresponding proximity tool. The library example contains eight fixed resources and one category resource template. These are useful distinctions when separating an advertised workflow from code that actually supports it. [Geographic examples][foundation-readme] [Library source][resources-example]

The reference projects require Python 3.12 or later. The foundation manifest declares `mcp[cli]>=1.12.4`; the capstone declares `mcp[cli]>=1.13.0` and `requests>=2.32.4`. These are lower bounds, not reproducible version pins or evidence of the versions used in every lesson. The capstone README is empty. [Foundation manifest][foundation-manifest] [Capstone manifest][capstone-manifest] [Capstone README][capstone-readme]

## The architectural model

The host application owns the conversation and model. An MCP client inside that host discovers and invokes server capabilities. The recipe server retrieves or persists domain data and returns results; it contains no direct model invocation. The reference application has no frontend, embedding pipeline, vector database, or retrieval-augmented generation subsystem. [Capstone source][capstone]

Three separate responsibilities are visible in the course examples:

| Primitive | Responsibility | Recipe example |
| --- | --- | --- |
| Tool | Execute an operation with validated inputs and a defined result. An operation may read external data or change local state. | Search recipes; create a meal plan. |
| Resource | Expose readable application context through an addressable URI. | Read saved collection information or statistics. |
| Prompt | Supply a reusable instruction template that a host can present and execute. | Guide a cooking lesson or meal-planning conversation. |

The original capstone follows this sequence: the host invokes a decorated Python function; the function calls TheMealDB or reads local JSON; it transforms the response into IDs, JSON text, or Markdown; the host receives that result. HTTP search and persistence logic live together in one module. Its entry point binds to `0.0.0.0`, reads `PORT` with an `8000` default, and runs Streamable HTTP. [Capstone source][capstone]

The rebuild separates protocol adapters, application services, provider access, domain models, and persistence. A provider response is normalized once into typed recipe data; services coordinate retrieval and storage; the MCP adapter exposes the resulting operation. This makes provider failure tests and database transaction tests possible without running a conversational model.

## Original capstone capability inventory

The reference exposes **seven tools: five recipe tools and two diagnostic tools**. Their actual semantics matter because tool names alone can suggest more functionality than the code supplies. [Capstone source][capstone]

| Original tool | Original behavior | Important constraint |
| --- | --- | --- |
| `search_recipes(dish_name, max_results=5)` | Calls TheMealDB name search, normalizes ingredients, merges full recipe data into a JSON file named after the search collection, and returns recipe IDs. | This is a dish-name search. Passing a cuisine name does not make it an area/cuisine filter. |
| `get_recipe_details(recipe_id)` | Scans saved collection files and returns the first matching recipe as JSON text. | It cannot retrieve an unsaved ID from the provider. |
| `create_meal_plan(recipe_ids, plan_name)` | Looks up saved recipes and writes a JSON plan containing recipe summaries. | Missing IDs are silently omitted; even an empty plan can report success. Reusing a sanitized name overwrites the previous file. |
| `search_by_first_letter(letter, max_results=5)` | Calls first-letter search and writes a summary of recipe IDs and names. | It does not persist full recipe records, so its results do not reliably work with the detail or planning tools. |
| `get_random_recipe()` | Fetches and returns a normalized random recipe. | The recipe is not persisted. |
| `test_filesystem()` | Writes and reads a temporary JSON file. | Development diagnostic; unrelated to the recipe domain. |
| `get_system_info()` | Returns runtime and filesystem information. | Exposes absolute paths and environment details to clients. |

The reference also registers **three fixed resources and one resource template**. They return Markdown. [Capstone source][capstone]

| Original resource | Actual data exposed |
| --- | --- |
| `recipes://cuisines` | Saved search collections. A collection can be a dish or ingredient phrase; these are not necessarily distinct cuisines. |
| `recipes://{cuisine}` | One saved collection's recipe summaries, ingredient preview, links, and truncated instructions. |
| `recipes://meal-plans` | Saved plan names, counts, dates, and filenames. |
| `recipes://stats` | Counts across saved files and cuisine/category frequencies. A recipe in multiple collections is counted multiple times. |

Five prompt intents complete the reference. None executes a tool merely by being retrieved. [Capstone source][capstone]

| Original prompt | Intent preserved in the rebuild |
| --- | --- |
| `generate_recipe_search_prompt` | Discover recipes and explain their culinary context. |
| `generate_meal_planning_prompt` | Select recipes, organize a meal, and save the chosen plan. |
| `generate_cooking_lesson_prompt` | Teach a technique with appropriate recipe examples. |
| `generate_ingredient_exploration_prompt` | Explore different uses of an ingredient. |
| `generate_cultural_cuisine_prompt` | Explore a cuisine and distinguish recipe facts from broader cultural context. |

Several original prompts request cooking times, difficulty, nutritional detail, price estimates, or dietary compliance that the provider fields do not establish. The rebuild's templates treat unsupported information as unknown or explicitly estimated. Ingredient lists can help a user review a recipe; they do not certify allergy safety, cross-contamination controls, or exact nutritional composition.

## Course implementation versus portfolio rebuild

| Concern | Reference implementation | Rebuild design and reason |
| --- | --- | --- |
| MCP SDK | Official v1 `FastMCP` import and API. | Official `mcp` 2.2 `MCPServer` API, adapted to current documented interfaces. This remains the official SDK, not the independently distributed `fastmcp` package. |
| Boundaries | One module owns protocol, external HTTP, formatting, persistence, and prompts. | Separate adapters, service logic, typed models, and repository operations so failures can be isolated and tested. |
| External HTTP | Synchronous `requests.get` with a ten-second timeout. | Asynchronous provider client, explicit timeouts, bounded transient retries, and a TTL cache to reduce repeated upstream work. |
| Persistence | Per-query JSON files, directory scans, and non-atomic writes. | Transactional SQLite; recipes keyed by stable IDs; blocking database work offloaded from the event loop. |
| Data continuity | Name searches save full recipes; first-letter and random results do not. | A common normalization and persistence path makes all acquisition methods usable by details and meal planning. |
| Meal-plan correctness | Partial results silently accepted; filenames can collide. | Atomic creation with generated plan IDs; retrying with the same optional idempotency key and normalized payload returns the existing plan. Reusing that key with different content fails. Calls without a key create separate plans. |
| Output and errors | Strings mix successful data with failure messages. | Pydantic 2 models, bounded inputs, and explicit domain errors mapped to the protocol boundary. |
| Resources | Saved search folders are presented as cuisines; statistics can duplicate recipe counts. | Replace search-folder collections with views of actual saved cuisine labels and individual saved recipes. Resources return JSON. Statistics contain deduplicated recipe, cuisine, and plan counts; category distributions are not reproduced. |
| Prompts | Five task templates can imply unsupported source facts. | Preserve all five intents while stating evidence limits and required tool use. |
| Transport | The capstone entry point runs HTTP. Foundation examples also demonstrate local transport. | Support both stdio and stateless Streamable HTTP for desktop and hosted workflows. |
| Demonstration | Successful recipe retrieval requires live upstream access. | Explicit offline demo data enables a repeatable portfolio walkthrough. Demo data is identified as such rather than presented as a live provider response. |
| Diagnostics | Two remotely callable tools expose filesystem/runtime details. | Remove those tools from the recipe surface. Operational diagnostics belong in controlled health/logging interfaces. |
| Verification | No automated tests in the inspected source snapshot. | Add meaningful tests for failed upstream requests, persistence integrity, idempotency, validation, and MCP interactions. Test outcomes are reported separately. |

The implemented MCP surface contains **six tools**: the five original recipe operations plus `get_meal_plan`. Both original diagnostic tools are removed. All five prompt names are retained. The rebuild exposes the following **three fixed resources and two resource templates**; it does not maintain per-query saved collections:

| Implemented resource | Behavior |
| --- | --- |
| `recipes://cuisines` | List distinct cuisine labels from saved recipes. |
| `recipes://cuisine/{cuisine}` | Read up to 100 saved recipes matching a cuisine label. This replaces the original search-folder template `recipes://{cuisine}`. |
| `recipes://recipe/{recipe_id}` | Read one saved recipe without network calls or writes; report a missing resource if it is not saved. |
| `recipes://meal-plans` | Read the latest 50 saved meal plans. |
| `recipes://stats` | Return counts of unique saved recipes, distinct cuisines, and saved plans. |

The provider response cache has a TTL. Persisted recipe records are durable and do not automatically expire; `get_recipe_details` returns an existing saved record before consulting the provider. A new search or random retrieval can update that record.

The two principal portfolio additions are **resilient provider access** and **transactionally correct, repeatable workflows backed by automated tests**. Offline demonstration and deployment packaging support those features. The goal is to show explicit failure behavior and trustworthy state transitions, beyond a successful API call.

## SDK migration notes

The official migration guide identifies breaking changes relevant to this rebuild: `FastMCP` becomes `MCPServer`; Python protocol-model fields use snake_case; transport options move from the server constructor to run/app methods; and `call_tool()` returns a protocol result object. The implementation must use the installed version's signatures rather than changing the package pin alone. [Official v1-to-v2 migration guide][migration]

The SDK's own transport dependency also changes from `httpx` to `httpx2`. The recipe provider's independent async HTTP client is a separate application dependency; SDK transport or OAuth objects should not be passed interchangeably into it. The protocol decorators remain recognizable, so the course's tools/resources/prompts concepts survive the API migration. [Official v1-to-v2 migration guide][migration]

The project targets official SDK 2.2.0 based on package/version verification during the rebuild. Version-sensitive API details should be checked against the dependency lock and installed SDK, not copied from the older course import paths. [Official SDK documentation][sdk] [Published MCP package][pypi]

## Practices to carry into system design interviews

1. **Keep orchestration and execution distinct.** The host reasons about the user's goal; the server validates and executes narrow operations. A prompt template helps the host plan but cannot guarantee that plan is executed correctly.
2. **Treat schemas as a contract.** Explicit recipe IDs, bounded result counts, and structured errors let clients respond programmatically. A successful response containing the word “Error” is an ambiguous contract.
3. **Make writes atomic and retries intentional.** Network uncertainty can cause repeated tool calls. A durable idempotency rule and transaction boundary are more reliable than asking a model not to repeat itself.
4. **Normalize data once.** Name, first-letter, random, and ID retrieval should produce the same domain model. One storage path prevents different discovery methods from producing incompatible downstream behavior.
5. **Keep slow work away from the event loop.** Async HTTP permits other requests to progress during network waits. Thread offload allows a synchronous SQLite library to coexist with that model; it does not make SQLite a horizontally scalable distributed database.
6. **Bound and observe failures.** Retry transient failures within a budget, expire cached data deliberately, and expose actionable errors. Retrying every exception indefinitely can amplify an outage.
7. **Separate source facts from generated interpretation.** Recipe instructions and ingredients are provider data. Prices, cultural analysis, dietary guarantees, and exact nutrition may require additional sources or human review.
8. **Tie deployment claims to evidence.** Stateless MCP transport does not remove durable application state. SQLite requires persistent storage and a deliberate deployment topology; public access also needs an appropriate authentication boundary. Local tests alone do not prove live-provider availability or production readiness.

## Source index

Every course-code link below is pinned to the inspected commit rather than a moving branch. Current SDK documentation is linked separately because it supports modernization decisions, not claims about what the original lessons taught.

- [Publisher repository at the inspected commit][repository]
- [Original recipe capstone][capstone]
- [Original capstone manifest][capstone-manifest]
- [Original foundation examples][foundations]
- [Official MCP SDK documentation][sdk]
- [Official SDK migration guide][migration]

[repository]: https://github.com/PacktPublishing/Model-Context-Protocol-Unlocked-From-Fundamentals-to-Advanced-Customization/tree/12ae65cf869c1105aa102b917f19a95fbaee87d2
[foundations]: https://github.com/PacktPublishing/Model-Context-Protocol-Unlocked-From-Fundamentals-to-Advanced-Customization/tree/12ae65cf869c1105aa102b917f19a95fbaee87d2/mcp-course-code-main
[hello]: https://github.com/PacktPublishing/Model-Context-Protocol-Unlocked-From-Fundamentals-to-Advanced-Customization/blob/12ae65cf869c1105aa102b917f19a95fbaee87d2/mcp-course-code-main/hello_mcp.py
[stream]: https://github.com/PacktPublishing/Model-Context-Protocol-Unlocked-From-Fundamentals-to-Advanced-Customization/blob/12ae65cf869c1105aa102b917f19a95fbaee87d2/mcp-course-code-main/stream_tester.py
[prompt-example]: https://github.com/PacktPublishing/Model-Context-Protocol-Unlocked-From-Fundamentals-to-Advanced-Customization/blob/12ae65cf869c1105aa102b917f19a95fbaee87d2/mcp-course-code-main/mcp_prompt.py
[resources-example]: https://github.com/PacktPublishing/Model-Context-Protocol-Unlocked-From-Fundamentals-to-Advanced-Customization/blob/12ae65cf869c1105aa102b917f19a95fbaee87d2/mcp-course-code-main/mcp_resources.py
[sqlite-example]: https://github.com/PacktPublishing/Model-Context-Protocol-Unlocked-From-Fundamentals-to-Advanced-Customization/blob/12ae65cf869c1105aa102b917f19a95fbaee87d2/mcp-course-code-main/sqlite_server.py
[foundation-readme]: https://github.com/PacktPublishing/Model-Context-Protocol-Unlocked-From-Fundamentals-to-Advanced-Customization/blob/12ae65cf869c1105aa102b917f19a95fbaee87d2/mcp-course-code-main/README.md
[foundation-manifest]: https://github.com/PacktPublishing/Model-Context-Protocol-Unlocked-From-Fundamentals-to-Advanced-Customization/blob/12ae65cf869c1105aa102b917f19a95fbaee87d2/mcp-course-code-main/pyproject.toml
[capstone]: https://github.com/PacktPublishing/Model-Context-Protocol-Unlocked-From-Fundamentals-to-Advanced-Customization/blob/12ae65cf869c1105aa102b917f19a95fbaee87d2/mcp-recipe-final-main/recipe_server.py
[capstone-manifest]: https://github.com/PacktPublishing/Model-Context-Protocol-Unlocked-From-Fundamentals-to-Advanced-Customization/blob/12ae65cf869c1105aa102b917f19a95fbaee87d2/mcp-recipe-final-main/pyproject.toml
[capstone-readme]: https://github.com/PacktPublishing/Model-Context-Protocol-Unlocked-From-Fundamentals-to-Advanced-Customization/blob/12ae65cf869c1105aa102b917f19a95fbaee87d2/mcp-recipe-final-main/README.md
[migration]: https://py.sdk.modelcontextprotocol.io/migration/
[sdk]: https://py.sdk.modelcontextprotocol.io/
[pypi]: https://pypi.org/project/mcp/
