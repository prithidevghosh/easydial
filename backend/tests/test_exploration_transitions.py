"""Adversarial cross-turn references must not silently select stale shopping choices."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from easydial.controller import DomainError
from easydial.models import Offer, Provider
from easydial.whatsapp.conversation import ShoppingChat
from tests.test_exploration import call, response
from tests.test_whatsapp import intent, make_runtime


def use_master(chat, create):
    chat.master = SimpleNamespace(require_client=lambda: SimpleNamespace(responses=SimpleNamespace(create=create)))
    chat.interpret = ShoppingChat.interpret.__get__(chat)
    chat.workers.allowed_providers = {Provider.BLINKIT, Provider.INSTAMART}


@pytest.mark.parametrize('reference', ['the second one', 'dusra wala', 'না প্রথমটা না, দ্বিতীয়টা', 'same one', '2.'])
async def test_natural_research_reference_cannot_select_old_shopping_menu(tmp_path, reference):
    r = make_runtime(tmp_path)
    try:
        state, _ = await r.chat.handle('milk', r.chat.initial())
        old_choices = list(state['choices'])
        state['exploration'] = {'active': True, 'offers': {}, 'messages': [
            {'role': 'assistant', 'content': '1. Energy drink A.\n2. Energy drink B.'}]}
        before = deepcopy(state['view']['lines'])
        create = AsyncMock(return_value=response(call('execute_shopping_action', **intent(
            'select', offer_id=old_choices[1], quantity=1).model_dump(mode='json'))))
        use_master(r.chat, create)
        state, replies = await r.chat.handle(reference, state)
        action_tool = next(tool for tool in create.await_args.kwargs['tools'] if tool['name'] == 'execute_shopping_action')
        assert 'select' not in action_tool['parameters']['properties']['action']['enum']
        assert state['view']['lines'] == before, 'An exploratory reference selected an unrelated old shopping offer'
        assert state['choices'] == old_choices
        assert state['exploration']['active'] is True
        assert replies
    finally:
        r.inbox.close()


async def test_failed_gallery_read_does_not_consume_images_for_retry(tmp_path):
    r = make_runtime(tmp_path)
    try:
        state, _ = await r.chat.handle('milk', r.chat.initial())
        offer = Offer.model_validate(state['offers'][state['choices'][0]])
        create = AsyncMock(side_effect=[
            response(call('show_product_images', offer_id=offer.id, count=1)),
            response(text='I could not load the image. Please retry.'),
            response(call('show_product_images', offer_id=offer.id, count=1)),
            response(text='Here is the product image.'),
        ])
        use_master(r.chat, create)
        url = 'https://cdn.grofers.com/da/test-front.jpg'
        r.chat.workers.product_details = AsyncMock(side_effect=[DomainError('Image gallery unavailable.'),
            {'status': 'ok', 'images': [url], 'image_count': 1}])
        state, first = await r.chat.handle('Show the first milk image', state)
        assert not any(reply['type'] == 'image' for reply in first)
        assert state['exploration']['shown_images'] == {}
        original_lines = deepcopy(state['view']['lines'])
        state, second = await r.chat.handle('Try showing that image again', state)
        assert [reply['image']['link'] for reply in second if reply['type'] == 'image'] == [url]
        assert state['view']['lines'] == original_lines
    finally:
        r.inbox.close()


async def test_same_asset_on_another_product_is_not_mistaken_for_already_shown(tmp_path):
    r = make_runtime(tmp_path)
    try:
        state, _ = await r.chat.handle('milk', r.chat.initial())
        first, second = [Offer.model_validate(state['offers'][key]) for key in state['choices'][:2]]
        create = AsyncMock(side_effect=[
            response(call('show_product_images', offer_id=first.id, count=1)), response(text='First product image.'),
            response(call('show_product_images', offer_id=second.id, count=1)), response(text='Second product image.'),
            response(call('show_product_images', offer_id=first.id, count=1, start_over=True)),
            response(text='First image again.'),
        ])
        use_master(r.chat, create)
        url = 'https://cdn.grofers.com/da/shared-pack.jpg'
        r.chat.workers.product_details = AsyncMock(return_value={'status': 'ok', 'images': [url], 'image_count': 1})
        for text in ['Show the first milk', 'Now show the second milk', 'Repeat the first picture']:
            state, replies = await r.chat.handle(text, state)
            assert [reply['image']['link'] for reply in replies if reply['type'] == 'image'] == [url]
        assert not state['view']['lines']
    finally:
        r.inbox.close()


async def test_revoked_provider_cannot_reuse_previous_gallery_context(tmp_path):
    r = make_runtime(tmp_path)
    try:
        state, _ = await r.chat.handle('milk', r.chat.initial())
        offer = Offer.model_validate(state['offers'][state['choices'][0]])
        state['exploration'] = {'active': True, 'offers': {offer.id: offer.model_dump(mode='json')},
                                'messages': [{'role': 'assistant', 'content': 'This is Blinkit milk.'}]}
        create = AsyncMock(side_effect=[response(call('show_product_images', offer_id=offer.id, count=1)),
                                        response(text='Connect Blinkit to view more product images.')])
        use_master(r.chat, create)
        r.chat.workers.allowed_providers = {Provider.INSTAMART}
        state['connected_stores'] = ['instamart']
        r.chat.workers.product_details = AsyncMock()
        state, replies = await r.chat.handle('Show more images of that one', state)
        r.chat.workers.product_details.assert_not_awaited()
        assert not any(reply['type'] == 'image' for reply in replies)
        assert offer.id not in state['exploration']['offers']
        assert not state['view']['lines']
    finally:
        r.inbox.close()
