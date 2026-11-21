import asyncio

from ..controller import DomainError
from ..models import Provider, SearchResult, SessionView
from ..settings import Settings
from .blinkit import BlinkitProvider
from .instamart import InstamartProvider


class ProviderWorkers:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.workers = [
            BlinkitProvider(
                settings.blinkit_storage_state,
                settings.blinkit_verified_location,
                settings.blinkit_verified_address,
                settings.blinkit_browser_profile,
            ),
            InstamartProvider(
                settings.instamart_browser_profile,
                settings.instamart_verified_address,
                settings.instamart_verified_location,
            ),
        ]

    def connection_status(self):
        return {provider.value: worker.connected() for provider, worker in zip(Provider, self.workers)}

    async def close(self):
        for worker in self.workers:
            await worker.close()

    async def search(self, query: str, address: str) -> list[SearchResult]:
        async def run(provider, worker):
            try:
                return await asyncio.wait_for(worker.search(query, address), timeout=45)
            except DomainError as error:
                return SearchResult(provider=provider, query=query, status="error", message=error.message)
            except Exception:
                return SearchResult(
                    provider=provider,
                    query=query,
                    status="error",
                    message="Could not verify this store. Check the connection and try again; stock is unknown.",
                )

        return list(await asyncio.gather(*(run(p, w) for p, w in zip(Provider, self.workers))))

    async def review(self, session: SessionView):
        if not session.lines:
            raise DomainError("Add something to your basket first.", 422)
        provider = session.lines[0].offer.provider
        worker = self.workers[list(Provider).index(provider)]
        try:
            return await asyncio.wait_for(worker.review(session), timeout=150)
        except DomainError:
            raise
        except Exception:
            raise DomainError(
                "Could not verify the store basket. It may have changed. Review again; no order was placed.",
                502,
            ) from None

    async def clear(self, session: SessionView):
        if session.lines or not session.synced_provider:
            raise DomainError("Remove the draft items before syncing an empty basket.")
        worker = self.workers[list(Provider).index(session.synced_provider)]
        try:
            await asyncio.wait_for(worker.clear(session.id), timeout=150)
        except DomainError:
            raise
        except Exception:
            raise DomainError(
                "The store did not confirm an empty basket. Check it before continuing.", 502
            ) from None
