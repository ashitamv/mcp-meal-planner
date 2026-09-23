# Verification evidence

## Shopping-list release 1.1.0 — September 23, 2026

Verified locally on Linux with Python 3.12.14 and MCP SDK 2.2.0. No dependency versions changed; the application version is now 1.1.0.

| Check | Result |
| --- | --- |
| Automated application tests | **183 passed** in 5.86 seconds, including 49 new shopping-list tests. |
| Coverage | **89%** combined statement/branch coverage overall; **98%** for `shopping.py`. Subprocess CLI coverage is not collected. |
| Ruff lint and formatting | Passed. |
| Strict mypy | Passed across all 12 application modules. |
| Offline MCP demo | Seven-tool discovery, existing save/replay/conflict workflow, and shopping-list generation passed. Two recipes consolidate to 3 cups chickpeas and 2 tbsp olive oil, with three original ambiguous measurements retained for review. |
| MCP transports | In-process client, authenticated HTTP client, and real stdio subprocess execute the new tool. |
| Existing database compatibility | Schema version 1 plans remain usable without migration; read-only SQL guard, one consistent read transaction, unchanged database content, restart, and zero provider calls verified. |
| Packaging | Locked dependency synchronization and wheel build passed offline; version 1.1.0 wheel includes the shopping module. |

New tests cover exact unit/fraction/decimal totals, ingredient normalization, provenance for every source line, unresolved and incompatible measurements, partial-total warnings, bounded arithmetic, deterministic output, missing data, validation, and typed MCP results. Ingredient refresh behavior is tested explicitly: shopping lists use current saved recipes, while older plan summary snapshots remain unchanged.

The upstream Starlette/AnyIO deprecation warning remains. This feature performs no network calls, so its verification uses deterministic saved ingredients. No new live API request, cloud deployment, load test, or run on the user's Mac was performed for this release. The existing deployment limitations below still apply. See [shopping-list behavior and design decisions](SHOPPING_LIST.md).

## Original release 1.0.0 — September 19, 2026

Verified September 19, 2026, using Python 3.12.14 on Linux with official MCP SDK 2.2.0. Results describe this local build and the checks actually performed.

| Check | Result |
| --- | --- |
| Automated tests | 134 passed in 4.15 seconds in the final full run. |
| Coverage | 668/748 executable statements and 156/200 branches; 86.92% combined, reported as 87%. |
| Ruff lint | Passed across source, scripts and tests. |
| Ruff format | All 25 Python files already formatted. |
| Strict mypy | Passed across all 11 application modules. |
| Offline MCP demo | Discovery, search, details, plan creation, exact replay, conflicting replay and stored statistics passed. |
| Live recipe API | Name search and ID lookup returned recipe 52771, Spicy Arrabiata Penne, with eight ingredients. |
| Real HTTP process | Started the CLI over a local TCP socket, checked readiness, connected with bearer authentication, searched and saved a plan through the SDK client. |
| Stdio subprocess | Automated protocol test starts the actual Python module and discovers/invokes tools. |
| SQLite backup | Backup API plus integrity check passed; saved plan was readable from the backup. |
| Wheel packaging | Built successfully; packaged offline recipe JSON and typing marker confirmed. |

## Reproduce the main checks

```bash
uv sync --locked
uv run pytest --cov=recipe_mcp --cov-report=term-missing
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/demo.py
uv build --wheel
```

The live check is separate because it depends on upstream availability:

```bash
uv run python scripts/live_smoke.py
```

## What the tests establish

- All four acquisition paths can provide complete details to saved meal plans.
- Unknown IDs reject a complete plan; no silently partial plan is committed.
- Duplicate recipe IDs remain unique and plan snapshots preserve their stored summaries.
- Matching persistent idempotency keys replay a result; changed payloads conflict, including concurrent requests.
- Provider retries are bounded and limited to eligible failures; cache expiry, eviction, concurrent lookup sharing, cancellation and malformed responses are exercised with deterministic HTTP mocks.
- Resources and prompts do not create records or fetch external data.
- Tool schemas, structured outputs and error flags are exercised with actual MCP clients.
- HTTP tests reject missing/wrong bearer credentials, invalid Host/Origin, oversized bodies and quota excess.
- Input bounds, unsafe remote binding and malformed credentials are rejected.

Tests use temporary databases and do not depend on a paid model or a live API. Coverage excludes activity inside the spawned CLI interpreter; its zero line-coverage entry is therefore not a claim that stdio startup was untested. Live API and real-socket smoke checks are additional evidence and are not included in the 134-test count.

## Limits of verification

Docker is unavailable in the execution environment, so the Dockerfile and Compose configuration were prepared and reviewed but the image was not built or run. No cloud account was connected, so the Render blueprint has not been deployed. Disk ownership and health behavior still need verification in that target environment.

The configured Python 3.13/3.14 CI matrix has not run here; local execution used Python 3.12.14. GitHub Actions has not run in a hosted repository. A desktop Claude/VS Code host was not launched. Protocol interoperability was exercised through the official SDK clients and HTTP JSON-RPC tests.

One upstream deprecation warning is emitted by Starlette 1.6.0's test client referencing an AnyIO alias. Application tests pass; application code does not import that deprecated alias. No dependency monkey-patch or suppressed warning was introduced.

The live smoke is a point-in-time compatibility check, not an uptime guarantee. No load test, penetration test, availability SLA, production traffic measurement, disaster recovery objective or public multi-user authorization claim is made. The supported deployment design is one private tenant, one service instance and persistent local storage.

The course analysis comes from the official public source repository and syllabus. Paid lesson videos were not accessed. The scope distinction and source links are recorded in `COURSE_SYNTHESIS.md`.
