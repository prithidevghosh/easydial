from easydial.models import Offer, Provider, Review, SearchResult


class FixtureProvider:
    """Synthetic catalogue. Never connects to a commerce website."""

    def __init__(self, provider: Provider):
        self.provider = provider
        from easydial.providers.cart_guard import CartGuard

        self.guard = CartGuard()
        self.previous_lines = []

    async def close(self):
        pass

    async def search(self, query: str, address: str) -> SearchResult:
        catalogue = [
            (
                "milk-amul",
                "Amul Taaza Toned Milk",
                "Amul",
                "Taaza toned milk",
                "500 ml",
                2800,
                2900,
                ["milk", "doodh", "दूध", "দুধ", "பால்"],
            ),
            (
                "milk-mother",
                "Mother Dairy Toned Milk",
                "Mother Dairy",
                "Toned milk",
                "500 ml",
                2900,
                2800,
                ["milk", "doodh", "दूध", "mother"],
            ),
            (
                "bread",
                "Britannia Whole Wheat Bread",
                "Britannia",
                "Whole wheat bread",
                "400 g",
                5000,
                5200,
                ["bread", "ब्रेड", "পাউরুটি"],
            ),
            (
                "rice",
                "India Gate Rozana Rice",
                "India Gate",
                "Rozana basmati rice",
                "1 kg",
                9900,
                10500,
                ["rice", "chawal", "चावल", "চাল"],
            ),
            (
                "eggs",
                "Farm Fresh Eggs",
                "Farm Fresh",
                "White eggs",
                "6 pcs",
                6000,
                6500,
                ["egg", "eggs", "anda", "अंडे", "ডিম"],
            ),
        ]
        normalized = query.casefold().strip()
        offers = []
        for key, name, brand, variant, size, blinkit, swiggy, keywords in catalogue:
            if not any(word in normalized for word in keywords) and normalized not in name.casefold():
                continue
            offers.append(
                Offer(
                    id=f"fixture:{self.provider}:{key}",
                    provider=self.provider,
                    product_id=key,
                    sku_id=key,
                    name=name,
                    brand=brand,
                    variant=variant,
                    pack_size=size,
                    price_paise=blinkit if self.provider == Provider.BLINKIT else swiggy,
                    available=True,
                    mode="live",
                    address_label=address,
                )
            )
        return SearchResult(
            provider=self.provider,
            query=query,
            offers=offers,
            message="Synthetic catalogue. These are not live prices.",
        )

    async def review(self, session):
        total = sum(x.subtotal for x in session.lines)
        return Review(
            version=session.version,
            provider=self.provider,
            address_label=session.address_label,
            item_subtotal_paise=total,
            fees_paise=0,
            total_paise=total,
            payment_method="Pending",
            mode="live",
        )

    async def revalidate(self, session):
        return await self.review(session)


def intent_tool_responses(parse):
    """Adapt intent fixtures to the model's action tool while retaining repair-call fixtures."""
    import json
    from types import SimpleNamespace

    from easydial.whatsapp.conversation import ChatIntent

    async def create(**kwargs):
        parsed = await parse(instructions=kwargs['instructions'], input=kwargs['input'][0]['content'],
                             text_format=ChatIntent)
        return SimpleNamespace(output_text='', output=[SimpleNamespace(
            type='function_call', name='execute_shopping_action', call_id='action',
            arguments=json.dumps(parsed.output_parsed.model_dump(mode='json')))])

    return SimpleNamespace(parse=parse, create=create)
