import asyncio
import logging
import traceback
from pathlib import Path

from ..controller import DomainError
from ..models import Provider, SearchResult, SessionView
from ..observability import emit, error_fields, traced
from ..settings import Settings
from .blinkit import BlinkitProvider, normalized
from .blinkit_location import BlinkitAddressError
from .instamart import InstamartProvider
from .proxy import configure_blinkit_proxy
from .semantic_ranking import SemanticProductRanker

log = logging.getLogger(__name__)


class ProviderWorkers:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.ranker = SemanticProductRanker(settings)
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

        for worker in self.workers:
            worker.browser.headless = (
                settings.blinkit_browser_headless
                if worker.provider == Provider.BLINKIT and settings.blinkit_browser_headless is not None
                else settings.browser_headless
            )
            worker.browser.channel = settings.browser_channel
            if worker.provider == Provider.BLINKIT:
                configure_blinkit_proxy(worker.browser, settings)

    def _worker(self, provider):
        if hasattr(self, "allowed_providers") and provider not in self.allowed_providers:
            raise DomainError("This store is not connected. Send Connect to link it first.")
        return self.workers[list(Provider).index(provider)]

    def restore_checkpoint(self, checkpoint, session_id):
        """Restore only a previously verified private checkpoint, not an unknown live cart.

        Every subsequent provider operation still compares the actual cart to this
        signature, so external changes remain blocked after process restart.
        """
        view = SessionView.model_validate(checkpoint)
        if view.id != session_id or not view.synced_provider or not view.lines:
            raise DomainError("Stored cart ownership needs attention before continuing.")
        if any(x.offer.provider != view.synced_provider for x in view.lines):
            raise DomainError("Stored cart provider does not match.")
        worker = self._worker(view.synced_provider)
        if worker.guard.owner is None:
            worker.guard.owner = view.id
            worker.guard.expected = tuple(
                sorted(
                    (normalized(x.offer.name), normalized(x.offer.pack_size), x.quantity) for x in view.lines
                )
            )
            worker.previous_lines = [x.model_copy(deep=True) for x in view.lines]

    def connection_status(self):
        return {provider.value: worker.connected() for provider, worker in zip(Provider, self.workers)}

    async def close(self):
        await self.ranker.close()
        for worker in self.workers:
            await worker.close()

    @traced('provider.search')
    async def search(self, query: str, address: str, provider: Provider | None = None) -> list[SearchResult]:
        async def run(provider, worker):
            try:
                # Blinkit may need bounded address restoration before inventory renders.
                # Preserve the existing Instamart deadline.
                timeout = 90 if provider == Provider.BLINKIT else 45
                result = await asyncio.wait_for(worker.search(query, address), timeout=timeout)
                if isinstance(result, SearchResult):
                    emit('provider.search_result', provider=provider, status=result.status, offer_count=len(result.offers))
                return result
            except DomainError as error:
                emit('provider.search_result', provider=provider, error_code='catalogue_domain_error', status='error', offer_count=0, **error_fields(error))
                return SearchResult(provider=provider, query=query, status="error", message=error.message,
                                    recovery="reconnect_address" if isinstance(error, BlinkitAddressError) else None)
            except Exception as error:
                emit('provider.search_result', provider=provider, error_code='catalogue_read_failed', status='error', offer_count=0, **error_fields(error))
                return SearchResult(
                    provider=provider,
                    query=query,
                    status="error",
                    message="Could not verify this store. Check the connection and try again; stock is unknown.",
                )

        results = list(
            await asyncio.gather(
                *(
                    run(p, w)
                    for p, w in zip(Provider, self.workers)
                    if (provider is None or p == provider)
                    and (not hasattr(self, "allowed_providers") or p in self.allowed_providers)
                )
            )
        )

        candidates = [o for r in results if r.status == "ok" for o in r.offers]
        if candidates:
            try:
                ranked = await self.ranker.rank(query, candidates)
                for result in results:
                    if result.status == "ok":
                        result.offers = [o for o in ranked if o.provider == result.provider]
            except Exception as error:
                emit('provider.ranking_failed', error_code='product_ranking_failed', status='error', **error_fields(error))
                for result in results:
                    if result.status == "ok" and result.offers:
                        result.offers = []
                        result.status = "error"
                        result.message = "Product matching is temporarily unavailable. Please retry."
        return results

    @traced('provider.explore_search')
    async def explore_search(self, query, address, provider):
        """Fresh read-only catalogue candidates for model-directed exploration."""
        from .product_details import read_catalogue

        provider = Provider(provider)
        worker = self._worker(provider)
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 250:
            raise DomainError("Please use a short product search query.")
        try:
            return await asyncio.wait_for(read_catalogue(worker, query.strip(), address),
                                          timeout=90 if provider == Provider.BLINKIT else 45)
        except DomainError:
            raise
        except Exception as error:
            emit('provider.operation_failed', stage='provider.explore_search', provider=provider, error_code='provider_operation_failed', **error_fields(error))
            raise DomainError("I couldn't read this store's current products. Prices and stock are unknown; please retry.") from None

    @traced('provider.product_details')
    async def product_details(self, offer, include_images=True):
        from .product_details import read_product_details

        worker = self._worker(offer.provider)
        try:
            return await asyncio.wait_for(read_product_details(worker, offer, include_images), timeout=90)
        except DomainError:
            raise
        except Exception as error:
            emit('provider.operation_failed', stage='provider.product_details', provider=offer.provider, error_code='provider_operation_failed', **error_fields(error))
            raise DomainError("I couldn't read this product's details just now. Your basket is unchanged.") from None

    @traced('provider.refresh_offer')
    async def refresh_offer(self, offer, address):
        """Read only the selected store; never substitute a similarly named SKU/pack."""
        worker = self._worker(offer.provider)
        try:
            result = await asyncio.wait_for(worker.search(offer.name, address), timeout=60)
        except Exception as error:
            emit('provider.operation_failed', stage='provider.refresh_offer', provider=offer.provider, error_code='provider_operation_failed', **error_fields(error))
            raise DomainError(
                "I couldn’t check the latest price just now. Your basket is unchanged. Try again shortly."
            ) from None
        if result.status != "ok":
            raise DomainError(
                "I couldn’t check the latest price just now. Your basket is unchanged. Try again shortly."
            )
        matches = [
            item
            for item in result.offers
            if (
                item.provider == offer.provider
                and item.product_id == offer.product_id
                and item.sku_id == offer.sku_id
                and normalized(item.pack_size) == normalized(offer.pack_size)
                and item.mode == offer.mode
                and item.address_label == address
            )
        ]
        if len(matches) != 1:
            raise DomainError(
                "I can’t find that exact pack right now. Your basket is unchanged. Would you like another option?"
            )
        return matches[0]

    @traced('provider.review')
    async def review(self, session: SessionView):
        if not session.lines:
            raise DomainError("Add something to your basket first.", 422)
        provider = session.lines[0].offer.provider
        worker = self._worker(provider)
        try:
            return await asyncio.wait_for(worker.review(session), timeout=150)
        except DomainError:
            raise
        except Exception as error:
            emit('provider.operation_failed', stage='provider.review', provider=provider, error_code='provider_operation_failed', **error_fields(error))
            frames = traceback.extract_tb(error.__traceback__)
            log.warning(
                "Basket review failed: provider=%s category=%s frames=%s",
                provider.value, type(error).__name__,
                [(Path(f.filename).name, f.name, f.lineno) for f in frames if "/easydial/" in f.filename],
            )
            raise DomainError(
                "Could not verify the store basket. It may have changed. Review again; no order was placed.",
                502,
            ) from None

    @traced('provider.clear')
    async def clear(self, session: SessionView):
        if session.lines or not session.synced_provider:
            raise DomainError("Remove the draft items before syncing an empty basket.")
        worker = self._worker(session.synced_provider)
        try:
            await asyncio.wait_for(worker.clear(session.id), timeout=150)
        except Exception as error:
            emit('provider.operation_failed', stage='provider.clear', provider=session.synced_provider, error_code='provider_operation_failed', **error_fields(error))
            frames = traceback.extract_tb(error.__traceback__)
            log.warning(
                "Basket clear failed: provider=%s category=%s frames=%s",
                session.synced_provider.value, type(error).__name__,
                [(Path(f.filename).name, f.name, f.lineno) for f in frames if "/easydial/" in f.filename],
            )
            if isinstance(error, DomainError):
                raise
            raise DomainError(
                "The store did not confirm an empty basket. Check it before continuing.", 502
            ) from None

    @traced('provider.revalidate')
    async def revalidate(self, session: SessionView):
        if not session.lines or not session.review:
            raise DomainError("Review the basket before approving its amount.")
        worker = self._worker(session.lines[0].offer.provider)
        try:
            return await asyncio.wait_for(worker.revalidate(session), timeout=60)
        except DomainError:
            raise
        except Exception as error:
            emit('provider.operation_failed', stage='provider.revalidate', provider=session.lines[0].offer.provider, error_code='provider_operation_failed', **error_fields(error))
            raise DomainError("Could not recheck the live bill. No payment was initiated.", 502) from None

    @traced('provider.reconcile')
    async def adopt_checkpoint(self, checkpoint, expected_address):
        view = SessionView.model_validate(checkpoint)
        if (
            not view.synced_provider
            or not view.lines
            or any(x.offer.provider != view.synced_provider for x in view.lines)
        ):
            raise DomainError("Invalid cart recovery checkpoint. Review again.")
        worker = self._worker(view.synced_provider)
        try:
            await asyncio.wait_for(worker.adopt_checkpoint(view, expected_address), timeout=60)
        except DomainError:
            raise
        except Exception as error:
            emit('provider.operation_failed', stage='provider.adopt_checkpoint', provider=view.synced_provider, error_code='provider_operation_failed', **error_fields(error))
            raise DomainError(
                "Could not verify the existing cart. Nothing was updated; send review again."
            ) from None

    @traced('provider.payment_options')
    async def payment_options(self, view, review):
        from .payments import options

        worker = self._worker(review.provider)
        try:
            timeout = 120 if review.provider == Provider.BLINKIT else 75
            return await asyncio.wait_for(options(worker, view, review), timeout=timeout)
        except DomainError:
            raise
        except Exception as error:
            emit('provider.operation_failed', stage='provider.payment_options', provider=review.provider, error_code='provider_operation_failed', **error_fields(error))
            raise DomainError("Payment options didn't load. Please try again.") from None

    @traced('provider.generate_upi')
    async def generate_upi(self, view, review):
        from .payments import generate_upi

        worker = self._worker(review.provider)
        try:
            return await asyncio.wait_for(generate_upi(worker, view, review), timeout=75)
        except DomainError:
            raise
        except Exception as error:
            emit('provider.operation_failed', stage='provider.generate_upi', provider=review.provider, error_code='provider_operation_failed', **error_fields(error))
            raise DomainError(
                "I couldn't confirm the payment request. Check the store before trying again."
            ) from None

    @traced('provider.prepare_cash')
    async def prepare_cash(self, view, review):
        from .payments import prepare_cash

        worker = self._worker(review.provider)
        return await asyncio.wait_for(prepare_cash(worker, view, review), timeout=75)

    @traced('provider.submit_cash')
    async def submit_cash(self, view, review, before_submit):
        from .payments import submit_cash

        worker = self._worker(review.provider)
        return await asyncio.wait_for(submit_cash(worker, view, review, before_submit), timeout=90)

    @traced('provider.order_history')
    async def order_history(self, view, exclude_ids=None):
        from .order_history import read_history

        worker = self._worker(view.review.provider)
        return await asyncio.wait_for(read_history(worker, view, exclude_ids), timeout=45)

    @traced('provider.past_orders')
    async def past_orders(self, start=None, end=None):
        from .past_orders import read_past_orders

        worker = self._worker(Provider.BLINKIT)
        try:
            return await asyncio.wait_for(read_past_orders(worker, start, end), timeout=240)
        except DomainError:
            raise
        except Exception as error:
            emit('provider.operation_failed', stage='provider.past_orders', provider='blinkit', error_code='provider_operation_failed', **error_fields(error))
            raise DomainError("I couldn’t read your recent Blinkit orders. Your basket is unchanged; please retry.") from None
