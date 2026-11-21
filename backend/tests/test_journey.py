from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from easydial.app import create_app
from easydial.controller import DomainError, SessionStore, exact_comparison
from easydial.models import EditRequest, Intent, IntentEdit, Provider, now
from easydial.providers.blinkit import parse_card
from easydial.providers.money import paise
from easydial.settings import Settings
from tests.fakes import FixtureProvider


@pytest.fixture
def client():
    with TestClient(
        create_app(
            Settings(_env_file=None, commerce_mode="live", live_address_label="home", openai_api_key="")
        )
    ) as client:
        client.app.state.workers.workers = [FixtureProvider(p) for p in Provider]
        yield client


def start(client):
    data = client.post("/sessions").json()
    return f"/sessions/{data['session']['id']}", {"Authorization": f"Bearer {data['token']}"}


def search(client, path, headers, query="milk", version=0):
    response = client.post(
        path + "/search", headers=headers, json={"query": query, "expected_version": version}
    )
    assert response.status_code == 200, response.text
    return response.json()


def edit(client, path, headers, state, **kwargs):
    response = client.post(
        path + "/basket", headers=headers, json={"expected_version": state["version"], **kwargs}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_provider_contract_journey_edit_review_pending(client):
    path, headers = start(client)
    state = search(client, path, headers)
    assert "less at" in state["comparison"]
    milk = state["results"][0]["offers"][0]["id"]
    state = edit(client, path, headers, state, kind="add", offer_id=milk, quantity=2)
    milk_line = state["lines"][0]["id"]
    state = search(client, path, headers, "bread", state["version"])
    state = edit(client, path, headers, state, kind="add", offer_id=state["results"][0]["offers"][0]["id"])
    state = edit(client, path, headers, state, kind="set_quantity", line_id=milk_line, quantity=3)
    assert len(state["lines"]) == 2
    review = client.post(
        path + "/review", headers=headers, json={"expected_version": state["version"]}
    ).json()
    assert review["review"]["total_paise"] == 3 * 2800 + 5000
    body = {"expected_version": state["version"], "review_id": review["review"]["id"], "confirmed": True}
    result = client.post(path + "/confirm", headers=headers, json=body)
    assert result.status_code == 200
    assert result.json()["phase"] == "checkout_pending"
    assert "no order was placed" in result.json()["messages"][-1]["text"]
    # A duplicate tap cannot submit payment or an order.
    assert client.post(path + "/confirm", headers=headers, json=body).json() == result.json()


def test_stale_review_and_other_user_rejected(client):
    path, headers = start(client)
    state = search(client, path, headers)
    state = edit(client, path, headers, state, kind="add", offer_id=state["results"][0]["offers"][0]["id"])
    review = client.post(
        path + "/review", headers=headers, json={"expected_version": state["version"]}
    ).json()["review"]
    state = edit(
        client, path, headers, state, kind="set_quantity", line_id=state["lines"][0]["id"], quantity=3
    )
    assert state["review"] is None
    assert (
        client.post(
            path + "/confirm",
            headers=headers,
            json={"expected_version": review["version"], "review_id": review["id"], "confirmed": True},
        ).status_code
        == 409
    )
    _, other_headers = start(client)
    assert client.get(path, headers=other_headers).status_code == 401
    assert client.get(path).status_code == 401


def test_unknown_offer_negative_quantity_and_mixed_store_fail_without_mutation(client):
    path, headers = start(client)
    state = search(client, path, headers)
    state = edit(client, path, headers, state, kind="add", offer_id=state["results"][0]["offers"][0]["id"])
    for offer, qty, status in [
        ("made-up", 1, 422),
        (state["results"][1]["offers"][0]["id"], 1, 409),
        (state["lines"][0]["offer"]["id"], -1, 422),
    ]:
        response = client.post(
            path + "/basket",
            headers=headers,
            json={"kind": "add", "offer_id": offer, "quantity": qty, "expected_version": state["version"]},
        )
        assert response.status_code == status
        assert client.get(path, headers=headers).json() == state


def test_replacement_preserves_bread(client):
    path, headers = start(client)
    state = search(client, path, headers)
    amul, mother = state["results"][0]["offers"]
    state = edit(client, path, headers, state, kind="add", offer_id=amul["id"])
    line_id = state["lines"][0]["id"]
    state = search(client, path, headers, "bread", state["version"])
    state = edit(client, path, headers, state, kind="add", offer_id=state["results"][0]["offers"][0]["id"])
    state = edit(
        client, path, headers, state, kind="replace", offer_id=mother["id"], line_id=line_id, quantity=2
    )
    assert len(state["lines"]) == 2
    assert {x["offer"]["product_id"] for x in state["lines"]} == {"milk-mother", "bread"}


def test_missing_key_is_explicit_not_fake_intelligence(client):
    path, headers = start(client)
    response = client.post(path + "/turn", headers=headers, json={"text": "दूध दो पैकेट", "expected_version": 0})
    assert response.status_code == 503
    assert "OpenAI" in response.json()["detail"]
    assert client.get(path, headers=headers).json()["version"] == 0


def test_live_checkout_cannot_be_enabled_with_env_flag():
    app = create_app(
        Settings(
            _env_file=None,
            commerce_mode="live",
            live_address_label="Verified test address",
            openai_api_key="",
        )
    )
    with TestClient(app) as client:
        assert client.get("/health").json()["live_checkout"] is False
        path, headers = start(client)
        current = app.state.store.get(path.split("/")[-1], headers["Authorization"][7:])
        from easydial.models import BasketLine, Offer

        current.view.lines.append(
            BasketLine(
                offer=Offer(
                    id="x",
                    provider=Provider.INSTAMART,
                    product_id="p",
                    sku_id="s",
                    name="milk",
                    pack_size="500 ml",
                    price_paise=2800,
                    available=True,
                    mode="live",
                    address_label="Verified test address",
                ),
                quantity=1,
            )
        )
        assert client.post(path + "/review", headers=headers, json={"expected_version": 0}).status_code == 503


def test_master_edits_are_atomic(client):
    path, headers = start(client)
    state = search(client, path, headers)
    state = edit(client, path, headers, state, kind="add", offer_id=state["results"][0]["offers"][0]["id"])

    async def interpreter(text, snapshot):
        return Intent(
            action="edit",
            queries=[],
            clarification="",
            edits=[
                IntentEdit(kind="set_quantity", line_id=state["lines"][0]["id"], quantity=3),
                IntentEdit(kind="remove", line_id="does-not-exist", quantity=1),
            ],
        )

    client.app.state.master.interpret = interpreter
    response = client.post(
        path + "/turn", headers=headers, json={"text": "change these", "expected_version": state["version"]}
    )
    assert response.status_code == 404
    assert client.get(path, headers=headers).json() == state


async def test_comparison_requires_equivalent_known_variants():
    a = (await FixtureProvider(Provider.BLINKIT).search("milk", "home")).offers[0]
    b = (await FixtureProvider(Provider.INSTAMART).search("milk", "home")).offers[0]
    assert exact_comparison([a, b])
    assert exact_comparison([a, b.model_copy(update={"pack_size": "1 l"})]) is None
    assert exact_comparison([a, b.model_copy(update={"variant": None})]) is None
    assert exact_comparison([a, b.model_copy(update={"address_label": "other"})]) is None


def test_money_validation():
    assert paise("NaN") is None
    assert paise(True) is None
    assert paise("-1") is None
    assert paise("₹1,234.50") == 123450


def test_blinkit_ambiguous_price_is_not_guessed():
    offer = parse_card({"id": "p", "name": "Amul milk", "text": "Amul milk\n500 ml\n₹30\n₹28\nADD"}, "home")
    assert offer.price_paise is None
    assert offer.pack_size == "500 ml"
    assert offer.brand is None
    assert parse_card({"id": None, "name": "milk"}, "home") is None


async def test_stale_offer_and_review_expire():
    session = SessionStore().create("live", "home")
    offer = (await FixtureProvider(Provider.BLINKIT).search("milk", "home")).offers[0]
    session.offers[offer.id] = offer.model_copy(update={"retrieved_at": now() - timedelta(minutes=11)})
    with pytest.raises(DomainError):
        session.edit(EditRequest(kind="add", offer_id=offer.id, expected_version=0))
    session.offers[offer.id] = offer
    session.edit(EditRequest(kind="add", offer_id=offer.id, expected_version=0))
    session.accept_review(1, await FixtureProvider(Provider.BLINKIT).review(session.view))
    session.view.review.created_at = now() - timedelta(minutes=6)
    with pytest.raises(DomainError):
        session.confirm(1, session.view.review.id, True)


def test_spoken_search_preserves_quantity_and_replacement_target(client):
    from easydial.models import RequestedProduct

    path, headers = start(client)
    state = search(client, path, headers)
    state = edit(
        client, path, headers, state, kind="add", offer_id=state["results"][0]["offers"][0]["id"], quantity=2
    )
    target = state["lines"][0]["id"]

    async def interpreter(text, snapshot):
        return Intent(
            action="search",
            edits=[],
            clarification="",
            queries=[RequestedProduct(query="Mother Dairy milk", quantity=3, replaces_line_id=target)],
        )

    client.app.state.master.interpret = interpreter
    response = client.post(
        path + "/turn",
        headers=headers,
        json={"text": "Replace that with three Mother Dairy packs", "expected_version": state["version"]},
    )
    assert response.status_code == 200
    offers = response.json()["results"][0]["offers"]
    assert all(o["suggested_quantity"] == 3 and o["replaces_line_id"] == target for o in offers)
    assert response.json()["lines"] == state["lines"]  # Choice precedes mutation.


async def test_provider_failure_isolated_without_fake_stock():
    from easydial.providers import ProviderWorkers

    workers = ProviderWorkers(
        Settings(_env_file=None, commerce_mode="live", live_address_label="home", openai_api_key="")
    )

    async def broken(*args):
        raise RuntimeError("private token must not leak")

    workers.workers = [FixtureProvider(p) for p in Provider]
    workers.workers[0].search = broken
    results = await workers.search("milk", "home")
    assert results[0].status == "error" and results[0].offers == []
    assert "private" not in results[0].message
    assert results[1].offers
