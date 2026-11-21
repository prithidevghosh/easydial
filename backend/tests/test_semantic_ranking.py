from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from easydial.controller import DomainError
from easydial.models import Offer, Provider
from easydial.providers.semantic_ranking import ProductRanking, SemanticProductRanker
from easydial.settings import Settings


def offer(id, name, **updates):
    return Offer(id=id, provider=Provider.BLINKIT, product_id=id, sku_id=id,
                 name=name, mode='live', address_label='private address',
                 price_paise=5000, available=True, **updates)


def ranker(ids):
    ranking = SemanticProductRanker(Settings(_env_file=None))
    parse = AsyncMock(return_value=SimpleNamespace(output_parsed=ProductRanking(offer_ids=ids)))
    ranking.client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    return ranking


async def test_ranker_uses_real_offers_without_lexical_prefilter_or_private_data():
    r = ranker(['b', 'a'])
    a, b = offer('a', 'Nandini Curd'), offer('b', 'Milky Mist Curd')
    result = await r.rank('ek dahi ka packet', [a, b])
    assert result[0] is b and result[1] is a
    args = r.client.responses.parse.call_args.kwargs
    assert 'Nandini Curd' in args['input']
    assert 'private address' not in args['input']
    assert args['store'] is False
    assert b.price_paise == 5000


@pytest.mark.parametrize('ids', [['invented'], ['a', 'a']])
async def test_unknown_or_duplicate_model_ids_fail_closed(ids):
    with pytest.raises(DomainError):
        await ranker(ids).rank('cookie', [offer('a', 'Butter Cookies')])


async def test_unavailable_candidates_cannot_be_selected():
    candidate = offer('a', 'Butter Cookies')
    candidate.available = False
    r = ranker(['a'])
    assert await r.rank('cookie', [candidate]) == []
    r.client.responses.parse.assert_not_awaited()


async def test_no_api_key_does_not_silently_use_literal_matching():
    r = SemanticProductRanker(Settings(_env_file=None, openai_api_key=''))
    with pytest.raises(DomainError):
        await r.rank('cookie', [offer('a', 'Cookies')])


async def test_worker_ranking_failure_never_returns_unfiltered_recommendations():
    from easydial.models import SearchResult
    from easydial.providers import ProviderWorkers

    workers = ProviderWorkers(Settings(_env_file=None, openai_api_key=''))
    workers.allowed_providers = {Provider.BLINKIT}
    workers.workers[0].search = AsyncMock(return_value=SearchResult(
        provider=Provider.BLINKIT, query='milk', offers=[offer('a', 'Milk Cookies')]))
    result = (await workers.search('milk', 'home'))[0]
    assert result.status == 'error' and not result.offers
    await workers.close()
