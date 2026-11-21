import copy
from datetime import timedelta

import pytest

from easydial.controller import DomainError
from easydial.models import Offer, SearchResult, now
from tests.test_choice_corrections import selected
from tests.test_whatsapp import intent, make_runtime


async def expired_correction(runtime):
    state, ids = await selected(runtime)
    state["offers"][ids[1]]["retrieved_at"] = (now() - timedelta(minutes=20)).isoformat()
    original_line = state["view"]["lines"][0]["id"]

    async def interpret(*args):
        return intent("select", offer_id=ids[1], replaces_line_id=original_line)

    runtime.chat.interpret = interpret
    return state, ids[1]


async def test_expired_correction_refreshes_and_replaces_without_repeat_search(tmp_path):
    runtime = make_runtime(tmp_path)
    state, offer_id = await expired_correction(runtime)
    calls = []

    async def refresh(offer, address):
        calls.append(offer.id)
        return offer.model_copy(update={"retrieved_at": now()})

    runtime.chat.workers.refresh_offer = refresh
    state, _ = await runtime.chat.handle("second wla choose krna h , yh remove kr do", state)
    assert calls == [offer_id]
    assert len(state["view"]["lines"]) == 1
    assert state["view"]["lines"][0]["offer"]["id"] == offer_id
    assert state["view"]["lines"][0]["quantity"] == 2
    runtime.inbox.close()


async def test_changed_price_preserves_original_until_explicit_choice(tmp_path):
    runtime = make_runtime(tmp_path)
    state, offer_id = await expired_correction(runtime)
    original = copy.deepcopy(state["view"]["lines"])

    async def refresh(offer, address):
        return offer.model_copy(update={"retrieved_at": now(), "price_paise": offer.price_paise + 500})

    runtime.chat.workers.refresh_offer = refresh
    state, replies = await runtime.chat.handle("second instead", state)
    assert state["view"]["lines"] == original
    assert "was" in replies[0]["text"]["body"]
    choice = replies[-1]["interactive"]["action"]["sections"][0]["rows"][0]["id"]
    state, _ = await runtime.chat.handle("Use this option", state, action_id=choice)
    assert len(state["view"]["lines"]) == 1
    assert state["view"]["lines"][0]["offer"]["id"] == offer_id
    assert state["view"]["lines"][0]["quantity"] == 2
    runtime.inbox.close()


@pytest.mark.parametrize("failure", ["unavailable", "error", "quantity"])
async def test_failed_refresh_never_removes_original_item(tmp_path, failure):
    runtime = make_runtime(tmp_path)
    state, _ = await expired_correction(runtime)
    before = copy.deepcopy(state)

    async def refresh(offer, address):
        if failure == "error":
            raise DomainError("Could not check price")
        return offer.model_copy(
            update={
                "retrieved_at": now(),
                "available": failure != "unavailable",
                "max_quantity": 1 if failure == "quantity" else 10,
            }
        )

    runtime.chat.workers.refresh_offer = refresh
    with pytest.raises(DomainError):
        await runtime.chat.handle("second instead", state)
    assert state == before
    runtime.inbox.close()


async def test_refresh_uses_only_selected_store_and_rejects_wrong_pack(tmp_path):
    runtime = make_runtime(tmp_path)
    state, offer_id = await expired_correction(runtime)
    offer = Offer.model_validate(state["offers"][offer_id])
    calls = []

    async def search(query, address):
        calls.append(query)
        return SearchResult(
            provider=offer.provider,
            query=query,
            offers=[offer.model_copy(update={"pack_size": "wrong pack"})],
        )

    runtime.chat.workers.workers[0].search = search

    async def forbidden(*args):
        raise AssertionError("Do not query the other store")

    runtime.chat.workers.workers[1].search = forbidden
    with pytest.raises(DomainError, match="exact pack"):
        await runtime.chat.workers.refresh_offer(offer, "test home")
    assert calls == [offer.name]
    runtime.inbox.close()
