import asyncio
from pathlib import Path

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
            ),
            InstamartProvider(settings.swiggy_access_token, settings.swiggy_address_id),
        ]

    def connection_status(self):
        return {
            "blinkit": bool(
                self.settings.blinkit_storage_state
                and Path(self.settings.blinkit_storage_state).is_file()
                and self.settings.blinkit_verified_location
            ),
            "instamart": bool(self.settings.swiggy_access_token and self.settings.swiggy_address_id),
        }

    async def search(self, query: str, address: str) -> list[SearchResult]:
        async def run(provider, worker):
            try:
                return await asyncio.wait_for(worker.search(query, address), timeout=45)
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
