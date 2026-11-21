"""Operator-only read-only discovery check: uv run python -m easydial.probe milk."""

import argparse
import asyncio
import json

from .providers import ProviderWorkers
from .settings import Settings


async def main():
    parser = argparse.ArgumentParser(
        description="Read-only EasyDial provider discovery; never modifies carts"
    )
    parser.add_argument("query", nargs="?", default="milk")
    args = parser.parse_args()
    settings = Settings()
    address = settings.live_address_label
    if not address:
        raise SystemExit("Set a verified delivery address before live discovery.")
    results = await ProviderWorkers(settings).search(args.query, address)
    print(
        json.dumps(
            {"mode": settings.commerce_mode, "results": [r.model_dump(mode="json") for r in results]},
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
