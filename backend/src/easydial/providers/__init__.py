import asyncio

from ..models import Provider, SearchResult
from ..settings import Settings
from .blinkit import BlinkitProvider
from .instamart import InstamartProvider
from .sample import SampleProvider


class ProviderWorkers:
    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.commerce_mode == "sample":
            self.workers = [SampleProvider(Provider.BLINKIT), SampleProvider(Provider.INSTAMART)]
        else:
            self.workers = [
                BlinkitProvider(settings.blinkit_storage_state, settings.blinkit_verified_location),
                InstamartProvider(settings.swiggy_access_token, settings.swiggy_address_id),
            ]

    async def search(self, query: str, address: str) -> list[SearchResult]:
        async def run(provider, worker):
            if (
                self.settings.commerce_mode == "live"
                and provider == Provider.BLINKIT
                and not self.settings.enable_live_comparison
            ):
                return SearchResult(
                    provider=provider,
                    query=query,
                    status="unavailable",
                    message="Live two-store comparison has not been enabled. Instamart search is available independently.",
                )
            try:
                return await asyncio.wait_for(worker.search(query, address), timeout=45)
            except Exception:
                # Exceptions can contain account URLs/tokens: never echo them to the client.
                return SearchResult(
                    provider=provider,
                    query=query,
                    status="error",
                    message="Could not verify this store. Please retry; stock is unknown.",
                )

        return list(
            await asyncio.gather(*(run(provider, worker) for provider, worker in zip(Provider, self.workers)))
        )
