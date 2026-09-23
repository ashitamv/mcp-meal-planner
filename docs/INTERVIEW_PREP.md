# Recipe MCP Interview Preparation

This guide prepares you to explain the recipe research and meal planning server, defend its engineering choices, and discuss its limits. Practice the responses in your own words and use the project demonstrations to support each claim. The application is a portfolio implementation designed for local use or a private, single tenant HTTP deployment.

## Architecture and data flow

The application exposes recipe search, recipe details, and persistent meal plans through the Model Context Protocol (MCP). An MCP host owns the language model, user conversation, tool selection, and user approval experience. Its MCP client calls this server over standard input and output or Streamable HTTP. The server validates requests, retrieves recipe data, and commits application state. It does not run a language model or require an LLM API key.

The code separates the protocol boundary, application service, recipe provider, and SQLite repository. This keeps transport details out of business rules and makes external failures reproducible in tests. Pydantic models describe accepted inputs and normalized domain data. SQLite is the durable source for saved recipes and meal plans; the provider cache is temporary process memory.

1. The host discovers available tools, resources, and prompts through its MCP client. Prompts provide reusable instructions; they do not execute a model within the server.
2. A tool call crosses the SDK boundary and is validated. The application service coordinates domain operations and enforces limits.
3. Recipe reads use a provider interface. DemoProvider supplies deterministic local recipes. MealDBProvider calls TheMealDB with asynchronous HTTP, timeouts, bounded retries, and caching.
4. Repository calls run off the event loop. SQLite transactions save recipes and meal plans, and an idempotency key makes a retried plan creation resolve to one persisted operation.
5. The server returns structured data or a tool error with a stable application error code. The host presents or reasons over that result.

The MCP surface uses three distinct primitives. Tools perform application actions. Resources expose addressable application data. Prompts supply templates for recipe discovery and meal planning workflows. The five prompt intents preserve the course's focus on recipe discovery, meal planning, cooking lessons, ingredient exploration, and cultural cuisine exploration.

## A short explanation to practice

“I built an MCP server that lets a compatible AI host research recipes and save meal plans. I kept the model in the host and exposed a typed capability boundary on the server. Behind that boundary, a service coordinates an interchangeable recipe provider and a SQLite repository. The reliability work focuses on external API failures and atomic, repeatable writes. The same application supports local stdio clients and private HTTP clients, with automated tests that exercise real MCP protocol exchanges.”

<!-- pagebreak -->

## Why these technologies and patterns

| Choice | Reason | Tradeoff |
| --- | --- | --- |
| Official Python MCP SDK | Provides protocol handling, discovery, typed tool integration, and supported transports. | SDK changes require compatibility review and protocol tests. |
| Pydantic 2 | Validates inputs and keeps provider data in a consistent domain shape. | Schema validity does not establish recipe quality or dietary safety. |
| Service and repository separation | Keeps orchestration independent of network and storage adapters. | Adds interfaces and small modules to a relatively compact application. |
| Async HTTPX | Allows external requests without blocking other async work and supports deterministic transport mocks. | Concurrency, cancellation, and connection cleanup need explicit handling. |
| SQLite with WAL | Gives transactional persistence and a small operational footprint. | Supports a single replica with a persistent local disk; writes still serialize. |
| DemoProvider | Makes demonstrations and core tests repeatable without external availability. | Does not prove the behavior or availability of the live recipe service. |
| Stdio and stateless HTTP | Supports a local desktop host and a private remotely reachable service. | HTTP adds authentication, origin checks, TLS termination, and operational responsibilities. |

The SDK migration uses the official MCPServer API in MCP 2.2 rather than preserving the course's earlier import path. The upgrade decision is based on the dependency release and exercised APIs, not a blanket assumption that new versions are compatible. A reproducible dependency set and transport tests protect the migration.

## Two portfolio extensions

The first extension is resilience around the recipe provider. Timeouts prevent indefinitely stalled calls. Bounded retries are reserved for transient failures. A time to live or TTL cache avoids unnecessary repeated reads, a least recently used or LRU eviction policy bounds retained entries, and single flight behavior makes concurrent identical lookups share upstream work. These controls reduce duplicate requests; they do not establish a guaranteed latency or availability target.

The second extension is transactional, idempotent meal planning with automated verification. A plan and its recipe relationships are committed together. Reusing a key with the same request returns the existing result; reusing it for a different request is a conflict. Tests must verify both outcomes and the behavior under failure, rather than merely assert that a function was called.

## Deployment boundary

The HTTP deployment uses a shared bearer token for one trusted tenant, along with host and origin protections. TLS belongs at the deployment edge. This access model does not provide individual user identities, public OAuth interoperability, per user authorization, or tenant isolation. SQLite requires a persistent volume and a single application replica. Public multi user use would require an identity design and a storage migration, not only more replicas.

<!-- pagebreak -->

## Technical questions and practice responses

### 1 What does MCP contribute to this application

MCP gives the host a standard way to discover and invoke capabilities and to retrieve resources and prompt templates. That lets the recipe application work with compatible hosts without embedding one vendor's model orchestration into the server. The host controls the conversation and model call; the server controls validation, recipe access, and persistence.

I would distinguish a protocol from an intelligence layer. MCP does not make a tool result correct, decide whether a user should approve a write, or guarantee safe model behavior. Those responsibilities remain explicit. In this project, typed inputs, constrained operations, domain checks, and predictable error responses support a reliable capability boundary. The server can also be exercised by a deterministic MCP client with no model involved, which makes its behavior testable.

### 2 Why use tools resources and prompts instead of only tools

Each primitive communicates a different intent to a host. Tools represent callable operations such as searching recipes or creating a plan. Resources expose data under identifiers that a client can read as context. Prompts package reusable workflow instructions and arguments. A prompt does not autonomously call tools or guarantee that the host follows its instructions.

I preserved these distinctions so application behavior is easier to discover and explain. Tool descriptions state effects and input expectations. Resource contents remain data. Prompt instructions guide the host through research and planning. The overlap is intentional where a tool returns a result that can later be read as a resource. For a smaller integration I could expose only tools, but that would lose part of the course's protocol coverage and some useful host interaction patterns.

### 3 How did you prevent blocking work from undermining async performance

External API access uses an async HTTP client so the event loop can serve other work while a request is waiting. SQLite operations use a synchronous repository and are dispatched to worker threads. The important boundary is the whole database operation, including its transaction, rather than offloading isolated statements that share transaction state across threads.

Async improves concurrency during waits; it does not make a slow database query faster or remove resource limits. I still need bounded HTTP connections, finite request timeouts, and a deliberate deployment size. SQLite WAL allows readers to coexist with a writer in many cases, but only one writer commits at a time. If write contention became material, I would measure it, then move the repository to PostgreSQL or redesign the write workload.

<!-- pagebreak -->

### 4 How do you make meal plan creation safe to retry

When the client supplies an idempotency key, it identifies one intended creation. The application binds that key to a canonical JSON representation of the normalized request. A transaction either creates the plan and its relationships together or observes an existing key. If the request matches, it returns the original plan. If the payload differs, it returns a conflict instead of silently reusing or replacing the plan. Calls without a key create independent plans.

This handles a common failure: the database commits, but the client loses the response and retries. It does not mean the system provides universal exactly once execution. The guarantee is limited to the persisted operation and key scope. Database constraints and the transaction enforce the invariant even when requests overlap. I would preserve that database backed enforcement in a multi replica version rather than replacing it with an in process lock.

### 5 How do retries caching and single flight work together

A fresh cache hit returns normalized recipe data without an external request. On a miss, concurrent callers for the same lookup share the provider's in flight request. That request uses a timeout and a finite retry budget for eligible transient failures. Successful results can populate a TTL cache, and LRU eviction keeps the number of retained entries bounded. This TTL applies to memory. Saved recipe details remain in SQLite until another acquisition updates them, so a future freshness requirement needs an explicit refresh policy.

These mechanisms solve different problems. Retry handles a temporary upstream failure. Cache handles repeated reads over time. Single flight handles duplicate work during a concurrent miss. Their failure semantics matter: authentication and malformed data errors are not retried, failed requests do not poison later requests, and a caller's cancellation does not cancel shared work. The tests exercise these behaviors without relying on live network timing. Cache hit ratios and latency improvements would require workload measurements before I claimed them.

### 6 Why did you choose SQLite and what would trigger a migration

For a portfolio service with one trusted tenant, SQLite gives transactional persistence, uniqueness constraints, and a simple backup and deployment model without requiring a separate database server. WAL, or write ahead logging, improves reader and writer coexistence. The repository boundary keeps SQL and storage details away from the MCP layer.

The constraint is deliberate. The application needs a persistent disk, and this deployment model assumes one application replica. WAL does not make SQLite a distributed database, and putting the file on arbitrary shared network storage is not a horizontal scaling plan. Sustained write contention, multiple replicas, stronger backup objectives, or tenant isolation requirements would justify PostgreSQL. I would keep the service contract, add explicit schema migrations and connection pooling, then rerun repository contract and protocol tests against the new adapter.

<!-- pagebreak -->

### 7 How do you handle errors without exposing implementation details

Expected failures become domain errors with stable codes and useful messages. The protocol boundary translates them into MCP tool errors, so a host can distinguish an invalid request, a missing recipe, an idempotency conflict, and a temporary provider failure. Unexpected failures are logged for diagnosis and return a generic client message.

An error envelope is part of the public contract. It should help a caller decide whether to correct an argument or retry without leaking stack traces, SQL, tokens, or local paths. Logs must stay on stderr for stdio because stdout is the protocol channel. I would evolve error codes with compatibility in mind and correlate production requests through operational logging rather than expand error messages with internal details.

### 8 What is the security model and where does it stop

The private HTTP mode uses a shared bearer credential, explicit host and origin protections, and a process wide request limit. The deployment must provide TLS at its edge. Input bounds reduce accidental or abusive work, and parameterized database statements separate values from SQL. The local stdio mode relies on the host's control over which executable it starts and what environment it supplies.

A shared token grants access to one trusted tenant. It does not identify individual users or authorize rows by owner. For a public service I would implement the MCP authorization requirements with an OAuth capable identity layer, validate token audience and scopes, and add tenant ownership to every storage operation. Recipe text is external content that a host must treat as untrusted data; these server controls cannot guarantee that a model will ignore embedded instructions.

### 9 How would you demonstrate that the server works

I would start the deterministic demo provider and use an actual MCP client to initialize a connection, discover capabilities, search for a recipe, inspect its details, create a plan, and read the resulting resource. I would repeat the write with its idempotency key and show that it resolves to the same plan. Then I would change the payload and demonstrate the conflict response.

Unit and integration tests cover domain rules, storage behavior, provider failures, and the protocol boundary. HTTP tests include authentication and host or origin rejection, while a subprocess test exercises stdio framing. These checks establish reproducible local behavior. They do not prove live provider uptime, cloud deployment readiness, load capacity, or recovery objectives; those require separate environment specific validation.

### 10 How would you evolve this into a larger cloud service

I would start with requirements for users, traffic, data retention, and recovery, then change the smallest architecture needed. Multiple users would require OAuth and authorization tied to stored ownership. Multiple replicas would require shared transactional storage such as PostgreSQL and an explicit migration strategy. I would keep the MCP adapter separate from the application service.

The in memory cache is sufficient for one process. A distributed cache would only be justified by measured duplicate traffic across replicas or provider quotas. Before increasing traffic, I would add metrics for request latency, provider retries, cache effectiveness, database contention, and errors; set service objectives; and test backup restoration. A queue would be useful for genuinely long running work, while ordinary recipe lookups can remain request and response operations.

<!-- pagebreak -->

## STAR story for a portfolio interview

Use this as a practice framework after you have reviewed and run the implementation. Describe the work as a portfolio project and credit the course foundation. Adapt the first person wording to the work you can explain and demonstrate.

### Situation

“The course introduced an MCP recipe server that combined recipe API calls, local persistence, and protocol handlers. I wanted a portfolio project that demonstrated how I would turn those learning examples into an application with explicit contracts and reproducible behavior.”

### Task

“My goal was to retain the core recipe research and meal planning experience while making failures and repeated requests predictable. I also wanted a design I could explain in a system design interview, with clear boundaries for its private, single tenant deployment.”

### Action

“I separated protocol handling from the application service, provider, and repository. I moved external requests to async HTTP with finite retries and bounded caching, and used a demo provider so the core demonstration did not depend on an external service. I replaced file based meal plan persistence with SQLite transactions and idempotency checks. I added typed validation, consistent errors, and private HTTP access controls. I verified the application through domain tests and actual MCP clients over HTTP and stdio, and reviewed the SDK migration against the exercised APIs.”

### Result

“The result was a working portfolio application with a repeatable demonstration and automated evidence for its main invariants. All 134 automated tests passed. It demonstrates recipe retrieval, durable meal plans, predictable retries, and protocol interoperability in the tested environment. Load capacity and cloud operational readiness require additional validation. The main engineering lesson was to make reliability guarantees precise and testable before adding more infrastructure.”

### How to make the story your own

Run the demonstration, inspect the idempotency transaction, and explain one failure test before using this story. Be ready to distinguish the course's recipe workflow from the reliability features in the rebuild. If asked about impact, use the verified behavior and test outcomes. Describe production adoption, performance targets, and business outcomes only after you have evidence for them.

<!-- pagebreak -->

## Evidence to keep ready

Verification recorded on September 19, 2026 establishes behavior in the development environment. Use the results below with their scope when discussing the project.

| Check | Recorded result | What the result establishes |
| --- | --- | --- |
| Automated tests | 134 passed in 4.15 seconds with one upstream warning | Domain, persistence, provider, MCP, HTTP, and stdio checks passed. Runtime is a test duration, not a service latency benchmark. |
| Coverage | 87 percent combined statement and branch coverage | Instrumented application paths were exercised. The CLI subprocess was tested but its execution was not captured in coverage. |
| Static checks | Strict mypy passed for 11 modules; Ruff lint and formatting passed | The current source satisfies the configured typing and style rules. |
| Offline demonstration | MCP demo passed | Discovery, recipe reads, plan creation, idempotent replay, conflict handling, and resource reads work without upstream access. |
| Live provider smoke | Search and lookup returned recipe 52771 with 8 ingredients | TheMealDB returned Spicy Arrabiata Penne during this check. This is a connectivity and mapping check, not an uptime guarantee. |
| HTTP and backup smoke | Authenticated socket connection and consistent SQLite backup passed | A real HTTP MCP client searched and created a plan; the backup snapshot retained that plan. |
| Deployment scope | Docker, cloud rollout, and desktop host UI were not executed | Packaging and connection instructions require validation in the intended deployment and host environment. |

Run these commands from the project directory after installing dependencies with `uv sync --locked`:

- `uv run pytest --cov=recipe_mcp --cov-report=term-missing`
- `uv run mypy`, `uv run ruff check .`, and `uv run ruff format --check .`
- `uv run python scripts/demo.py` for the repeatable offline walkthrough.
- `uv run python scripts/live_smoke.py` for the separate live provider check.

For a short interview demonstration, discover capabilities, search recipes, read details, create a plan, replay the key, and show a conflict for a changed request. Explain why a rejected plan leaves no partial plan even though already fetched recipes may remain saved. Then state the deployment boundary: one replica, a persistent SQLite volume, a private bearer token, configured hosts and origins, and TLS at the edge.

## Source and code references

The course foundation is Packt's *Model Context Protocol Unlocked From Fundamentals to Advanced Customization* by Paulo Dichone, available through the [publisher's source repository](https://github.com/PacktPublishing/Model-Context-Protocol-Unlocked-From-Fundamentals-to-Advanced-Customization/tree/12ae65cf869c1105aa102b917f19a95fbaee87d2).

Use the [MCP documentation](https://modelcontextprotocol.io/docs/getting-started/intro), the [official Python SDK](https://github.com/modelcontextprotocol/python-sdk), and the project's README and tests when explaining protocol behavior. The course synthesis distinguishes reference behavior from portfolio extensions.
