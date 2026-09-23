"""Exercise the deployed HTTP boundary, including authorization and admission."""

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from recipe_mcp.config import Settings
from recipe_mcp.server import create_http_app

TOKEN = "portfolio-http-test-" + "b" * 32
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-11-25",
}


@pytest.fixture
def http_client(tmp_path):
    settings = Settings(
        _env_file=None,
        mode="demo",
        database_path=tmp_path / "http.sqlite3",
        auth_token=TOKEN,
    )
    with TestClient(create_http_app(settings), base_url="http://localhost:8000") as client:
        yield client


def request(client, method, params=None, headers=None, request_id=1):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return client.post("/mcp", json=message, headers=HEADERS | (headers or {}))


def test_http_initialization_discovery_and_tool_call(http_client):
    initialized = request(
        http_client,
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "portfolio-http-tests", "version": "1.0.0"},
        },
    )
    assert initialized.status_code == 200, initialized.text
    assert initialized.json()["result"]["protocolVersion"] == "2025-11-25"
    assert "mcp-session-id" not in initialized.headers

    discovered = request(http_client, "tools/list")
    assert discovered.status_code == 200, discovered.text
    assert "create_meal_plan" in {t["name"] for t in discovered.json()["result"]["tools"]}

    recipe = request(http_client, "tools/call", {"name": "get_random_recipe", "arguments": {}})
    assert recipe.status_code == 200, recipe.text
    result = recipe.json()["result"]
    assert result.get("isError", False) is False
    assert result["structuredContent"]["ingredients"]


async def test_current_sdk_connects_over_http_and_persists_a_plan(tmp_path):
    settings = Settings(
        _env_file=None,
        mode="demo",
        database_path=tmp_path / "sdk-http.sqlite3",
        auth_token=TOKEN,
    )
    app = create_http_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            headers={"Authorization": f"Bearer {TOKEN}"},
            trust_env=False,
        ) as http:
            transport = streamable_http_client("http://localhost:8000/mcp", http_client=http)
            async with Client(transport) as client:
                acquired = await client.call_tool("get_recipe_details", {"recipe_id": "900001"})
                assert acquired.is_error is False
                planned = await client.call_tool(
                    "create_meal_plan",
                    {
                        "recipe_ids": ["900001"],
                        "plan_name": "SDK HTTP dinner",
                        "idempotency_key": "http-dinner",
                    },
                )
                assert planned.is_error is False
                restored = await client.call_tool(
                    "get_meal_plan", {"plan_id": planned.structured_content["id"]}
                )
                assert restored.structured_content == planned.structured_content
                shopping = await client.call_tool(
                    "generate_shopping_list", {"plan_id": planned.structured_content["id"]}
                )
                assert shopping.is_error is False
                assert shopping.structured_content["plan_id"] == planned.structured_content["id"]
                assert shopping.structured_content["items"]
                assert shopping.structured_content["requires_review"] is True


@pytest.mark.parametrize("authorization", [None, "Bearer invalid", TOKEN, f"Basic {TOKEN}"])
def test_http_rejects_missing_or_wrong_credentials(http_client, authorization):
    headers = {"Accept": "application/json, text/event-stream"}
    if authorization is not None:
        headers["Authorization"] = authorization
    response = http_client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert TOKEN not in response.text


def test_http_rejects_untrusted_host_and_origin(http_client):
    host = request(http_client, "tools/list", headers={"Host": "attacker.invalid"})
    assert host.status_code == 421
    origin = request(http_client, "tools/list", headers={"Origin": "https://attacker.invalid"})
    assert origin.status_code == 403
    allowed = request(http_client, "tools/list", headers={"Origin": "http://localhost:8000"})
    assert allowed.status_code == 200


def test_http_rejects_oversized_request_body(http_client):
    response = request(http_client, "tools/list", {"padding": "x" * 65536})
    assert response.status_code == 413


def test_health_and_readiness_are_available_without_credentials(http_client):
    health = http_client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["mode"] == "demo"
    assert http_client.get("/readyz").json() == {"status": "ready"}


def test_rate_limit_is_shared_and_cannot_be_bypassed_with_forwarded_ip(http_client):
    for index in range(120):
        response = request(
            http_client,
            "tools/list",
            headers={"X-Forwarded-For": f"192.0.2.{index + 1}"},
            request_id=index,
        )
        assert response.status_code == 200, response.text
    blocked = request(http_client, "tools/list", headers={"X-Forwarded-For": "198.51.100.1"})
    assert blocked.status_code == 429
    assert blocked.json() == {"error": "rate_limited"}
    assert blocked.headers["retry-after"] == "60"
    assert http_client.get("/healthz").status_code == 200
    assert http_client.get("/readyz").status_code == 200
