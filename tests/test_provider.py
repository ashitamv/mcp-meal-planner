"""Exercise provider behavior through its public interface and real HTTPX parsing."""

import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from recipe_mcp.errors import AppError
from recipe_mcp.provider import DemoProvider, MealDBProvider


def meal(recipe_id: str = "123", name: str = "Test Soup") -> dict:
    return {
        "idMeal": recipe_id,
        "strMeal": name,
        "strInstructions": "Simmer the lentils in water until tender.",
        "strCategory": "Vegetarian",
        "strArea": "International",
        "strIngredient1": "Lentils",
        "strMeasure1": "1 cup",
        "strIngredient2": "Water",
        "strMeasure2": "3 cups",
        "strTags": "Soup, Simple",
    }


@pytest.mark.asyncio
async def test_search_caches_full_results_and_encodes_query():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"meals": [meal("1"), meal("2"), meal("3")]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = MealDBProvider(client)
        query = "soup & i=999/?"
        assert len(await provider.search(query, 1)) == 1
        assert len(await provider.search(query, 25)) == 3
    assert len(requests) == 1
    assert dict(requests[0].url.params) == {"s": query}
    assert requests[0].url.host == "www.themealdb.com"
    assert requests[0].url.path == "/api/json/v1/1/search.php"


@pytest.mark.asyncio
async def test_no_matches_are_cached_and_get_has_clear_error():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"meals": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = MealDBProvider(client)
        assert await provider.search("missing") == []
        assert await provider.search("missing") == []
        for _ in range(2):
            with pytest.raises(AppError) as caught:
                await provider.get("987")
            assert caught.value.code == "recipe_not_found"
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_by_letter_and_get_map_fields_and_isolate_mutation():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"meals": [meal()]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = MealDBProvider(client)
        recipes = await provider.by_letter("T")
        assert recipes[0].ingredients[0].ingredient == "Lentils"
        assert recipes[0].tags == ["Soup", "Simple"]
        recipes[0].tags.append("Client mutation")
        assert (await provider.by_letter("t"))[0].tags == ["Soup", "Simple"]
        assert (await provider.get("123")).id == "123"
    assert dict(requests[0].url.params) == {"f": "t"}
    assert dict(requests[1].url.params) == {"i": "123"}


@pytest.mark.asyncio
async def test_random_does_not_cache():
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(200, json={"meals": [meal(str(count))]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = MealDBProvider(client)
        assert (await provider.random()).id == "1"
        assert (await provider.random()).id == "2"
    assert count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_transient_http_errors_retry_then_succeed(status):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        if count < 3:
            return httpx.Response(status, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"meals": [meal()]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await MealDBProvider(client, max_retry_delay=0).search("soup")
    assert count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("exception", [httpx.ReadTimeout, httpx.ConnectError])
async def test_timeout_and_connection_errors_are_bounded_and_sanitized(exception):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        raise exception("private-key-and-provider-body", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = MealDBProvider(client, api_key="private-key", max_retry_delay=0)
        with pytest.raises(AppError) as caught:
            await provider.search("soup")
    assert caught.value.code == "upstream_unavailable"
    assert caught.value.retryable is True
    assert "private" not in str(caught.value)
    assert count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 401, 403, 404])
async def test_non_transient_errors_and_redirects_are_not_retried(status):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(status, headers={"Location": "https://untrusted.example/"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        with pytest.raises(AppError) as caught:
            await MealDBProvider(client, max_retry_delay=0).search("soup")
    assert caught.value.code == "upstream_error"
    assert caught.value.retryable is False
    assert count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        {"meals": "invalid"},
        {"meals": [None]},
        {"meals": [{"idMeal": "1"}]},
        {"meals": [{**meal(), "strIngredient1": {"unexpected": "object"}}]},
        {"meals": [{**meal(), "idMeal": 123}]},
        {"meals": [{**meal(), "strInstructions": " "}]},
    ],
)
async def test_malformed_payload_is_not_retried_or_cached(payload):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = MealDBProvider(client, max_retry_delay=0)
        for _ in range(2):
            with pytest.raises(AppError) as caught:
                await provider.search("soup")
            assert caught.value.code == "upstream_invalid_response"
    assert count == 2


@pytest.mark.asyncio
async def test_invalid_json_and_mismatched_lookup_are_rejected():
    responses = [
        httpx.Response(200, content=b"private upstream HTML"),
        httpx.Response(200, json={"meals": [meal("999")]}),
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: responses.pop(0))
    ) as client:
        provider = MealDBProvider(client)
        for operation in (provider.search("soup"), provider.get("123")):
            with pytest.raises(AppError) as caught:
                await operation
            assert caught.value.code == "upstream_invalid_response"
            assert "private" not in str(caught.value)


@pytest.mark.asyncio
async def test_streaming_response_size_is_bounded_and_stream_is_closed():
    class OversizedStream(httpx.AsyncByteStream):
        chunks_read = 0
        closed = False

        async def __aiter__(self):
            for _ in range(100):
                self.chunks_read += 1
                yield b"x" * 65536

        async def aclose(self):
            self.closed = True

    stream = OversizedStream()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(AppError) as caught:
            await MealDBProvider(client).search("soup")
    assert caught.value.code == "upstream_invalid_response"
    assert stream.chunks_read == 65
    assert stream.closed


@pytest.mark.asyncio
async def test_malformed_utf8_is_sanitized():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"\xff\xfe\x00"))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(AppError) as caught:
            await MealDBProvider(client).search("soup")
    assert caught.value.code == "upstream_invalid_response"


@pytest.mark.asyncio
async def test_ttl_expiry_and_lru_eviction(monkeypatch):
    from recipe_mcp import provider as module

    current = 100.0
    monkeypatch.setattr(module.time, "monotonic", lambda: current)
    requests = []

    def handler(request):
        requests.append(request.url.params["s"])
        return httpx.Response(200, json={"meals": [meal()]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = MealDBProvider(client, cache_size=2, cache_ttl=10)
        for query in ("a", "b", "a", "c", "b"):
            await provider.search(query)
        assert requests == ["a", "b", "c", "b"]
        current = 111.0
        await provider.search("b")
        assert requests == ["a", "b", "c", "b", "b"]


@pytest.mark.asyncio
async def test_expired_cache_is_not_served_on_upstream_failure(monkeypatch):
    from recipe_mcp import provider as module

    current = 100.0
    monkeypatch.setattr(module.time, "monotonic", lambda: current)
    responses = [httpx.Response(200, json={"meals": [meal()]}), httpx.Response(503)]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: responses.pop(0))
    ) as client:
        provider = MealDBProvider(client, retries=0, cache_ttl=10)
        await provider.search("soup")
        current = 111.0
        with pytest.raises(AppError) as caught:
            await provider.search("soup")
        assert caught.value.code == "upstream_unavailable"


@pytest.mark.asyncio
async def test_simultaneous_identical_requests_share_one_fetch():
    count = 0
    started, finish = asyncio.Event(), asyncio.Event()

    async def handler(request):
        nonlocal count
        count += 1
        started.set()
        await finish.wait()
        return httpx.Response(200, json={"meals": [meal("1"), meal("2")]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = MealDBProvider(client)
        tasks = [asyncio.create_task(provider.search("soup", limit)) for limit in (1, 2, 2)]
        await started.wait()
        finish.set()
        result = await asyncio.gather(*tasks)
    assert [len(recipes) for recipes in result] == [1, 2, 2]
    assert count == 1


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_fetch():
    started, finish = asyncio.Event(), asyncio.Event()
    count = 0

    async def handler(request):
        nonlocal count
        count += 1
        started.set()
        await finish.wait()
        return httpx.Response(200, json={"meals": [meal()]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = MealDBProvider(client)
        first = asyncio.create_task(provider.search("soup"))
        await started.wait()
        second = asyncio.create_task(provider.search("soup"))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        finish.set()
        assert (await second)[0].id == "123"
    assert count == 1


@pytest.mark.asyncio
async def test_shutdown_cancels_orphaned_fetch_before_client_close():
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def handler(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = MealDBProvider(client)
        caller = asyncio.create_task(provider.search("soup"))
        await started.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        await provider.aclose()
        assert cancelled.is_set()
        assert not client.is_closed
        with pytest.raises(AppError) as caught:
            await provider.search("soup")
        assert caught.value.code == "provider_closed"
        await provider.aclose()


@pytest.mark.asyncio
async def test_request_concurrency_is_bounded():
    active = peak = 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return httpx.Response(200, json={"meals": [meal()]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = MealDBProvider(client, concurrency=2)
        await asyncio.gather(*(provider.search(f"soup{i}") for i in range(12)))
    assert peak == 2


@pytest.mark.asyncio
async def test_pending_requests_are_bounded_and_recover():
    started, finish = asyncio.Event(), asyncio.Event()

    async def handler(request):
        started.set()
        await finish.wait()
        return httpx.Response(200, json={"meals": [meal()]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = MealDBProvider(client, max_pending=1)
        first = asyncio.create_task(provider.search("first"))
        await started.wait()
        with pytest.raises(AppError) as caught:
            await provider.search("second")
        assert caught.value.code == "upstream_busy"
        assert caught.value.retryable
        finish.set()
        await first
        assert await provider.search("second")


@pytest.mark.asyncio
async def test_retry_after_delay_accepts_seconds_and_http_dates_and_is_bounded():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    ) as client:
        provider = MealDBProvider(client, max_retry_delay=5)
        assert provider._retry_delay(0, "3") == 3
        assert provider._retry_delay(0, "999999") == 5
        future = format_datetime(datetime.now(UTC) + timedelta(hours=1), usegmt=True)
        assert provider._retry_delay(0, future) == 5
        assert 0 <= provider._retry_delay(0, "malformed") <= 0.35
        assert 0 <= provider._retry_delay(0, "-3") <= 0.35


@pytest.mark.asyncio
async def test_demo_provider_complete_workflow_and_round_robin():
    provider = DemoProvider()
    found = await provider.search("  CHICKPEA  ")
    assert found[0].name == "Coconut Chickpea Skillet"
    assert (await provider.get(found[0].id)).id == found[0].id
    assert (await provider.by_letter("A"))[0].name == "Apple Cinnamon Oats"
    assert await provider.search("no such dish") == []
    ids = [(await provider.random()).id for _ in range(7)]
    assert len(set(ids[:6])) == 6
    assert ids[0] == ids[6]
    with pytest.raises(AppError) as caught:
        await provider.get("999999")
    assert caught.value.code == "recipe_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [MealDBProvider, DemoProvider])
async def test_input_validation_prevents_network_requests(provider_type):
    def handler(request):
        pytest.fail("Invalid input must not trigger HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = provider_type(client) if provider_type is MealDBProvider else provider_type()
        operations = [
            provider.search(" "),
            provider.search("x" * 101),
            provider.search("soup", 0),
            provider.search("soup", 26),
            provider.by_letter("ab"),
            provider.by_letter("é"),
            provider.get("1/../2"),
            provider.get("１２３"),
        ]
        for operation in operations:
            with pytest.raises(AppError) as caught:
                await operation
            assert caught.value.code == "invalid_input"
