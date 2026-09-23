"""Recipe providers with bounded HTTP retries, caching, and offline fixtures."""

from __future__ import annotations

import asyncio
import json
import random as random_module
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from importlib.resources import files
from typing import Literal, overload

import httpx
from pydantic import ValidationError

from .errors import AppError
from .models import Ingredient, Recipe


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 25:
        raise AppError("invalid_input", "max_results must be between 1 and 25.")
    return value


def _query(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100:
        raise AppError("invalid_input", "dish_name must contain between 1 and 100 characters.")
    return value.strip()


def _letter(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z]", value) is None:
        raise AppError("invalid_input", "letter must be one English letter.")
    return value.lower()


def _recipe_id(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,12}", value) is None:
        raise AppError("invalid_input", "recipe_id must contain between 1 and 12 digits.")
    return value


def _invalid_response() -> AppError:
    return AppError("upstream_invalid_response", "The recipe service returned an invalid response.")


@overload
def _text(meal: dict[str, object], key: str, *, required: Literal[True]) -> str: ...


@overload
def _text(meal: dict[str, object], key: str, *, required: Literal[False] = False) -> str | None: ...


def _text(meal: dict[str, object], key: str, *, required: bool = False) -> str | None:
    value = meal.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise _invalid_response()
    value = value.strip()
    if required and not value:
        raise _invalid_response()
    return value or None


def _parse_meals(payload: object) -> tuple[Recipe, ...]:
    """Validate upstream shape explicitly; never coerce unexpected JSON values."""
    if not isinstance(payload, dict) or "meals" not in payload:
        raise _invalid_response()
    meals = payload["meals"]
    if meals is None:
        return ()
    if not isinstance(meals, list):
        raise _invalid_response()
    recipes = []
    try:
        for meal in meals:
            if not isinstance(meal, dict):
                raise _invalid_response()
            ingredients = []
            for number in range(1, 21):
                name = _text(meal, f"strIngredient{number}")
                measure = _text(meal, f"strMeasure{number}")
                if name is not None:
                    ingredients.append(Ingredient(ingredient=name, measure=measure or ""))
            if not ingredients:
                raise _invalid_response()
            tags = _text(meal, "strTags")
            recipes.append(
                Recipe(
                    id=_text(meal, "idMeal", required=True),
                    name=_text(meal, "strMeal", required=True),
                    cuisine=_text(meal, "strArea") or "Unknown",
                    category=_text(meal, "strCategory") or "Unknown",
                    instructions=_text(meal, "strInstructions", required=True),
                    ingredients=ingredients,
                    image_url=_text(meal, "strMealThumb"),
                    youtube_url=_text(meal, "strYoutube"),
                    source_url=_text(meal, "strSource"),
                    tags=[tag.strip() for tag in tags.split(",") if tag.strip()] if tags else [],
                )
            )
    except ValidationError:
        raise _invalid_response() from None
    return tuple(recipes)


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    recipes: tuple[Recipe, ...]


_CacheKey = tuple[str, tuple[tuple[str, str], ...]]


class MealDBProvider:
    """TheMealDB adapter; the caller owns and closes the supplied HTTP client.

    Defaults: two retries after the first attempt, 300-second/128-entry cache,
    eight concurrent requests, 128 pending distinct cached requests, a ten-second
    per-attempt HTTP timeout, and at most five seconds between retries. Random
    requests bypass caching and single-flight but share the concurrency limit.
    Instances belong to one event loop, matching an application lifespan.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str = "1",
        retries: int = 2,
        cache_ttl: float = 300,
        cache_size: int = 128,
        *,
        concurrency: int = 8,
        request_timeout: float = 10,
        max_retry_delay: float = 5,
        max_pending: int = 128,
    ) -> None:
        if not isinstance(api_key, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", api_key) is None:
            raise ValueError("api_key must be a valid path-safe API key")
        if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 5:
            raise ValueError("retries must be an integer between 0 and 5")
        for name, value in (
            ("cache_size", cache_size),
            ("concurrency", concurrency),
            ("max_pending", max_pending),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not 0 <= cache_ttl <= 86400:
            raise ValueError("cache_ttl must be between 0 and 86400 seconds")
        if not 0 < request_timeout <= 120:
            raise ValueError("request_timeout must be between 0 and 120 seconds")
        if not 0 <= max_retry_delay <= 60:
            raise ValueError("max_retry_delay must be between 0 and 60 seconds")
        self._client = client
        self._base_url = f"https://www.themealdb.com/api/json/v1/{api_key}"
        self._retries = retries
        self._cache_ttl = cache_ttl
        self._cache_size = cache_size
        self._timeout = request_timeout
        self._max_retry_delay = max_retry_delay
        self._max_pending = max_pending
        self._semaphore = asyncio.Semaphore(concurrency)
        self._cache: OrderedDict[_CacheKey, _CacheEntry] = OrderedDict()
        self._inflight: dict[_CacheKey, asyncio.Task[tuple[Recipe, ...]]] = {}
        self._flight_lock = asyncio.Lock()
        self._closed = False

    async def aclose(self) -> None:
        """Drain owned tasks before the application closes its shared HTTP client."""
        async with self._flight_lock:
            self._closed = True
            tasks = tuple(self._inflight.values())
            for task in tasks:
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._cache.clear()

    async def search(self, dish_name: str, max_results: int = 10) -> list[Recipe]:
        limit = _limit(max_results)
        recipes = await self._get("search.php", {"s": _query(dish_name)})
        return [recipe.model_copy(deep=True) for recipe in recipes[:limit]]

    async def by_letter(self, letter: str, max_results: int = 10) -> list[Recipe]:
        limit = _limit(max_results)
        recipes = await self._get("search.php", {"f": _letter(letter)})
        return [recipe.model_copy(deep=True) for recipe in recipes[:limit]]

    async def get(self, recipe_id: str) -> Recipe:
        recipe_id = _recipe_id(recipe_id)
        recipes = await self._get("lookup.php", {"i": recipe_id})
        if not recipes:
            raise AppError("recipe_not_found", "No recipe was found for that ID.")
        if len(recipes) != 1 or recipes[0].id != recipe_id:
            raise _invalid_response()
        return recipes[0].model_copy(deep=True)

    async def random(self) -> Recipe:
        recipes = await self._request("random.php", {})
        if not recipes:
            raise AppError("no_results", "The recipe service has no random recipe available.")
        if len(recipes) != 1:
            raise _invalid_response()
        return recipes[0].model_copy(deep=True)

    async def _get(self, endpoint: str, params: dict[str, str]) -> tuple[Recipe, ...]:
        key = (endpoint, tuple(sorted(params.items())))
        async with self._flight_lock:
            if self._closed:
                raise AppError("provider_closed", "The recipe provider has been closed.")
            cached = self._cache.get(key)
            if cached is not None:
                if cached.expires_at > time.monotonic():
                    self._cache.move_to_end(key)
                    return cached.recipes
                del self._cache[key]
            task = self._inflight.get(key)
            if task is None:
                if len(self._inflight) >= self._max_pending:
                    raise AppError(
                        "upstream_busy",
                        "The recipe service is busy. Try again shortly.",
                        retryable=True,
                    )
                task = asyncio.create_task(self._fetch_and_cache(key, endpoint, params))
                self._inflight[key] = task
                task.add_done_callback(lambda finished: self._finish_flight(key, finished))
        # A disconnected caller must not cancel a request shared with other callers.
        return await asyncio.shield(task)

    def _finish_flight(self, key: _CacheKey, task: asyncio.Task[tuple[Recipe, ...]]) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        if not task.cancelled():
            task.exception()  # Retrieve exceptions even if every waiting caller disconnected.

    async def _fetch_and_cache(
        self, key: _CacheKey, endpoint: str, params: dict[str, str]
    ) -> tuple[Recipe, ...]:
        recipes = await self._request(endpoint, params)
        if self._cache_ttl > 0:
            self._cache[key] = _CacheEntry(time.monotonic() + self._cache_ttl, recipes)
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return recipes

    async def _request(self, endpoint: str, params: dict[str, str]) -> tuple[Recipe, ...]:
        if self._closed:
            raise AppError("provider_closed", "The recipe provider has been closed.")
        for attempt in range(self._retries + 1):
            retry_after = None
            try:
                status, retry_after, body = await self._http_attempt(endpoint, params)
            except (TimeoutError, httpx.TimeoutException, httpx.NetworkError):
                error = AppError(
                    "upstream_unavailable",
                    "The recipe service is temporarily unavailable.",
                    retryable=True,
                )
            except httpx.RequestError:
                raise AppError("upstream_error", "The recipe service request failed.") from None
            else:
                if status == 429:
                    error = AppError(
                        "upstream_rate_limited",
                        "The recipe service is busy. Try again shortly.",
                        retryable=True,
                    )
                elif 500 <= status <= 599:
                    error = AppError(
                        "upstream_unavailable",
                        "The recipe service is temporarily unavailable.",
                        retryable=True,
                    )
                elif status != 200:
                    raise AppError(
                        "upstream_error", "The recipe service could not complete the request."
                    )
                else:
                    try:
                        payload = json.loads(body)
                    except (ValueError, UnicodeError):
                        raise _invalid_response() from None
                    recipes = _parse_meals(payload)
                    if endpoint == "lookup.php" and recipes:
                        if len(recipes) != 1 or recipes[0].id != params["i"]:
                            raise _invalid_response()
                    if endpoint == "random.php" and len(recipes) > 1:
                        raise _invalid_response()
                    return recipes
            if attempt == self._retries:
                raise error from None
            await asyncio.sleep(self._retry_delay(attempt, retry_after))
        raise AssertionError("Retry loop must return or raise")

    async def _http_attempt(
        self, endpoint: str, params: dict[str, str]
    ) -> tuple[int, str | None, bytes]:
        # Stop reading after 4 MiB; HTTPX's eager .get() would allocate the full body.
        async with self._semaphore:
            async with asyncio.timeout(self._timeout):
                async with self._client.stream(
                    "GET",
                    f"{self._base_url}/{endpoint}",
                    params=params,
                    timeout=self._timeout,
                    follow_redirects=False,
                ) as response:
                    retry_after = response.headers.get("Retry-After")
                    if response.status_code != 200:
                        return response.status_code, retry_after, b""
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        if len(body) + len(chunk) > 4 * 1024 * 1024:
                            raise _invalid_response()
                        body.extend(chunk)
                    return response.status_code, retry_after, bytes(body)

    def _retry_delay(self, attempt: int, retry_after: str | None) -> float:
        backoff: float = min(
            self._max_retry_delay, 0.25 * 2.0**attempt + random_module.uniform(0, 0.1)
        )
        if retry_after:
            try:
                seconds = float(retry_after)
            except ValueError:
                try:
                    when = parsedate_to_datetime(retry_after)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=UTC)
                    seconds = (when - datetime.now(UTC)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    return backoff
            if seconds >= 0:
                return min(self._max_retry_delay, max(backoff, seconds))
        return backoff


class DemoProvider:
    """Six original recipes; deterministic offline behavior for demos and tests."""

    def __init__(self) -> None:
        try:
            content = files("recipe_mcp").joinpath("demo_data.json").read_text(encoding="utf-8")
            self._recipes = tuple(Recipe.model_validate(item) for item in json.loads(content))
        except (OSError, ValueError, TypeError, ValidationError):
            raise AppError(
                "demo_data_invalid", "The bundled demo recipes could not be loaded."
            ) from None
        if not self._recipes:
            raise AppError("demo_data_invalid", "The bundled demo recipes are empty.")
        self._next_random = 0

    async def search(self, dish_name: str, max_results: int = 10) -> list[Recipe]:
        query, limit = _query(dish_name).casefold(), _limit(max_results)
        return [
            recipe.model_copy(deep=True)
            for recipe in self._recipes
            if query in recipe.name.casefold()
        ][:limit]

    async def by_letter(self, letter: str, max_results: int = 10) -> list[Recipe]:
        initial, limit = _letter(letter), _limit(max_results)
        return [
            recipe.model_copy(deep=True)
            for recipe in self._recipes
            if recipe.name.casefold().startswith(initial)
        ][:limit]

    async def get(self, recipe_id: str) -> Recipe:
        recipe_id = _recipe_id(recipe_id)
        for recipe in self._recipes:
            if recipe.id == recipe_id:
                return recipe.model_copy(deep=True)
        raise AppError("recipe_not_found", "No recipe was found for that ID.")

    async def random(self) -> Recipe:
        # Round-robin makes recording and rehearsing an offline demo reproducible.
        recipe = self._recipes[self._next_random]
        self._next_random = (self._next_random + 1) % len(self._recipes)
        return recipe.model_copy(deep=True)
