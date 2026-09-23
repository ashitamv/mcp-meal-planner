"""Private-deployment HTTP boundary. This is shared-token auth, not OAuth."""

import secrets
import time
from collections import deque

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class PrivateAccessMiddleware:
    """Protect protocol requests and cap process-wide request admission.

    No client-supplied IP headers are trusted. The quota is deliberately shared
    by all callers of this single-tenant service and resets on process restart.
    """

    def __init__(self, app: ASGIApp, token: str | None, requests_per_minute: int = 120) -> None:
        self.app = app
        self.token = token
        self.limit = requests_per_minute
        self.requests: deque[float] = deque()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in ("/healthz", "/readyz"):
            await self.app(scope, receive, send)
            return
        if self.token:
            headers = dict(scope.get("headers", []))
            supplied = headers.get(b"authorization", b"").decode("latin-1")
            expected = f"Bearer {self.token}"
            if not secrets.compare_digest(supplied.encode(), expected.encode()):
                await JSONResponse(
                    {"error": "unauthorized"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )(scope, receive, send)
                return
        now = time.monotonic()
        while self.requests and self.requests[0] <= now - 60:
            self.requests.popleft()
        if len(self.requests) >= self.limit:
            await JSONResponse(
                {"error": "rate_limited"}, status_code=429, headers={"Retry-After": "60"}
            )(scope, receive, send)
            return
        self.requests.append(now)
        await self.app(scope, receive, send)
