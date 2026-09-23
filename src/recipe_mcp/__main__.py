"""Run with `recipe-mcp --demo` or `recipe-mcp --transport http`."""

import argparse
import logging
import os
import sys
from typing import Any

import uvicorn
from pydantic import ValidationError

from recipe_mcp.config import Settings
from recipe_mcp.server import create_http_app, create_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Recipe MCP server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--demo", action="store_true", help="Use offline example recipes")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    overrides: dict[str, Any] = {}
    if args.demo:
        overrides["mode"] = "demo"
    if args.host:
        overrides["host"] = args.host
    if args.port is not None:
        overrides["port"] = args.port
    elif os.environ.get("PORT"):
        overrides["port"] = os.environ["PORT"]
    try:
        settings = Settings(**overrides)
    except ValidationError as exc:
        # Pydantic's normal str(exc) includes raw input values, possibly secrets.
        messages = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        parser.error("; ".join(messages))
    logging.basicConfig(
        level=settings.log_level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # HTTP libraries can log upstream URLs containing the configured API key.
    for name in ("httpx", "httpcore", "httpx2", "httpcore2"):
        logging.getLogger(name).setLevel(logging.WARNING)
    if args.transport == "stdio":
        create_server(settings).run(transport="stdio")
    else:
        uvicorn.run(
            create_http_app(settings),
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level.lower(),
            access_log=False,
            proxy_headers=False,
        )


if __name__ == "__main__":
    main()
