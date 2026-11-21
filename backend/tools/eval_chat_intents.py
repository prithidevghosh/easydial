"""Opt-in real-model text-intent eval. Synthetic context; no WhatsApp or commerce actions.

Run: uv --directory backend run python tools/eval_chat_intents.py --output .state/intent-eval.json
"""

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from easydial.controller import Session
from easydial.master import OpenAIMaster
from easydial.models import BasketLine, Offer, Provider, Review, SessionView
from easydial.settings import Settings
from easydial.whatsapp.conversation import ShoppingChat

CASES = [
    dict(
        id="reported_switch_empty",
        text="i said yeh sab kuch nhi lena, thumsp kr do ek bass",
        basket=False,
        action="search",
        term="thums",
        quantity=1,
        edits=[],
    ),
    dict(
        id="reported_switch_full",
        text="i said yeh sab kuch nhi lena, thumsp kr do ek bass",
        basket=True,
        action="search",
        term="thums",
        quantity=1,
        edits=["milk", "curd"],
    ),
    dict(
        id="hindi_curd",
        text="ek dahi ka packet order krna h",
        basket=False,
        action="search",
        term="dahi|curd|दही",
        quantity=1,
        edits=[],
    ),
    dict(
        id="devanagari_switch",
        text="ये सब नहीं चाहिए, बस एक थम्स अप चाहिए",
        basket=True,
        action="search",
        term="thums",
        quantity=1,
        edits=["milk", "curd"],
    ),
    dict(
        id="mixed_change",
        text="doodh hata do aur ek bread dikhao",
        basket=True,
        action="search",
        term="bread",
        quantity=1,
        edits=["milk"],
    ),
    dict(
        id="keep_existing",
        text="dahi rehne do, ek Thums Up bhi chahiye",
        basket=True,
        action="search",
        term="thums",
        quantity=1,
        edits=[],
    ),
    dict(
        id="compare_not_cancel",
        text="abhi order mat karna, bas milk ka price compare karo",
        basket=True,
        action="search",
        term="milk|doodh",
        quantity=1,
        edits=[],
    ),
    dict(
        id="quantity_correction",
        text="doodh do nahi teen packet kar do",
        basket=True,
        action="edit",
        edit_quantity=3,
        edits=["milk"],
    ),
    dict(
        id="negated_removal",
        text="doodh mat hatana, ek bread bhi chahiye",
        basket=True,
        action="search",
        term="bread",
        quantity=1,
        edits=[],
    ),
    dict(
        id="clear_only", text="sab kuch basket se hata do", basket=True, action="edit", edits=["milk", "curd"]
    ),
    dict(
        id="reject_options",
        text="in options mein se kuch nahi, ek Thums Up dikhao",
        basket=False,
        action="search",
        term="thums",
        quantity=1,
        edits=[],
    ),
    dict(id="ambiguous_yes", text="haan", basket=False, action="clarify", edits=[]),
    dict(id="ambiguous_unit", text="do litre doodh chahiye", basket=False, action="search|clarify", edits=[]),
    dict(
        id="replace_one",
        text="dahi ke badle ek Thums Up dikhao, doodh rehne do",
        basket=True,
        action="search",
        term="thums",
        quantity=1,
        replaces="curd",
        edits=[],
    ),
    dict(id="stop", text="STOP", basket=True, action="stop", edits=[]),
    dict(id="no_automatic_payment", text="order kar do", basket=True, action="review|clarify", edits=[]),
]


CASES += [
    dict(
        id="correct_first_to_second",
        text="first wla nhi , ap second wla choose kro",
        basket=True,
        selected=True,
        action="select",
        offer="curd",
        replaces_selection="milk",
        edits=[],
    ),
    dict(
        id="correct_devanagari",
        text="नहीं, आप पहला नहीं, सेकंड वाला ऑर्डर कर दो.",
        basket=True,
        selected=True,
        action="select",
        offer="curd",
        replaces_selection="milk",
        edits=[],
    ),
    dict(
        id="additional_second",
        text="second wala bhi add kar do, first rehne do",
        basket=True,
        selected=True,
        action="select",
        offer="curd",
        edits=[],
    ),
    dict(id="bare_number_after_selection", text="2", basket=True, selected=True, action="clarify", edits=[]),
    dict(
        id="second_before_selection",
        text="first wala nahi second choose karo",
        basket=False,
        full_menu=True,
        action="select",
        offer="curd",
        edits=[],
    ),
]


CASES += [
    dict(
        id="garbled_move_not_remove",
        text="हरके के मूव कीजिए",
        basket=True,
        reviewed=True,
        action="clarify",
        edits=[],
    ),
    dict(
        id="continue_after_review",
        text="ha thik h, isko move kijiye",
        basket=True,
        reviewed=True,
        action="checkout|clarify",
        edits=[],
    ),
    dict(
        id="restore_previous_basket",
        text="Put the same order in the cart again",
        basket=False,
        previous=True,
        action="restore",
        edits=[],
    ),
]


CASES += [
    dict(
        id="store_move_reported",
        text="Please remove this order from Instamart and Blinkit pe same product add kar do.",
        basket=True,
        selected=True,
        action="search",
        term="milk",
        quantity=2,
        replaces="milk",
        edits=[],
        target_provider="blinkit",
        switch_store=True,
    ),
    dict(
        id="store_move_hinglish",
        text="Instamart se hata ke Blinkit pe similar milk add karo",
        basket=True,
        selected=True,
        action="search",
        term="milk",
        quantity=2,
        replaces="milk",
        edits=[],
        target_provider="blinkit",
        switch_store=True,
    ),
    dict(
        id="store_compare_only",
        text="Don't remove anything from Instamart, just compare milk prices on Blinkit",
        basket=True,
        action="search",
        term="milk",
        quantity=2,
        edits=[],
        target_provider="blinkit",
        switch_store=False,
    ),
]


CASES.append(
    dict(
        id="basket_contents_and_store",
        text="What is in the cart right now and is it from Instamart or Blinkit?",
        basket=True,
        action="basket",
        edits=[],
        target_provider=None,
        switch_store=False,
    )
)


def context(full):
    offers = {}
    for key, name in [("milk", "Amul Taaza Milk"), ("curd", "Nandini Curd")]:
        offers[key] = Offer(
            id=key,
            provider=Provider.INSTAMART,
            product_id=key,
            sku_id=key,
            name=name,
            pack_size="500 ml" if key == "milk" else "500 g",
            price_paise=3400,
            available=True,
            mode="live",
            address_label="synthetic home",
        )
    view = SessionView(
        id="eval",
        mode="live",
        address_label="synthetic home",
        lines=[BasketLine(id=key, offer=offer, quantity=2) for key, offer in offers.items()] if full else [],
    )
    session = Session(view, offers=offers)
    session.say("Clear the cart now.", "user")  # Stale context must not become an instruction.
    return session, {"choices": ["curd"], "offers": {}}


def failures(case, intent):
    errors = []
    if "target_provider" in case and intent.target_provider != case["target_provider"]:
        errors.append("target_provider")
    if intent.switch_store != case.get("switch_store", False):
        errors.append("switch_store")
    if intent.action not in case["action"].split("|"):
        errors.append("action")
    if sorted(e.line_id for e in intent.edits) != sorted(case["edits"]):
        errors.append("edit_targets")
    if case.get("edit_quantity"):
        if any(e.kind != "set_quantity" or e.quantity != case["edit_quantity"] for e in intent.edits):
            errors.append("edit_quantity")
    elif any(e.kind != "remove" for e in intent.edits):
        errors.append("edit_kind")
    if case.get("term"):
        if len(intent.queries) != 1 or not any(
            t in intent.queries[0].query.casefold() for t in case["term"].split("|")
        ):
            errors.append("product")
        if not intent.queries or (
            intent.queries[0].quantity != case["quantity"] and case["id"] != "compare_not_cancel"
        ):
            errors.append("quantity")
    if case.get("replaces"):
        if not intent.queries or intent.queries[0].replaces_line_id != case["replaces"]:
            errors.append("replacement")
    elif any(q.replaces_line_id for q in intent.queries):
        errors.append("unexpected_replacement")
    if intent.offer_id != case.get("offer"):
        errors.append("selection")
    if intent.replaces_line_id != case.get("replaces_selection"):
        errors.append("selection_replacement")
    if re.search(r"[\u0600-\u06ff\u0900-\u0dff]", intent.clarification):
        errors.append("non_english_reply")
    return errors


async def run(args):
    settings = Settings()
    if args.model:
        settings.openai_text_model = args.model
    master = OpenAIMaster(settings)
    chat = ShoppingChat(settings, None, master)
    semaphore = asyncio.Semaphore(3)

    async def one(case, repeat):
        async with semaphore:
            session, state = context(case["basket"])
            if case.get("selected"):
                session.view.lines = [line for line in session.view.lines if line.id == "milk"]
                state["choices"] = []
                state["recent_selection"] = {
                    "choices": ["milk", "curd"],
                    "line_id": "milk",
                    "offer_id": "milk",
                }
            elif case.get("full_menu"):
                state["choices"] = ["milk", "curd"]
            if case.get("reviewed"):
                session.view.review = Review(
                    id="review",
                    version=0,
                    provider=Provider.INSTAMART,
                    address_label="synthetic home",
                    item_subtotal_paise=13600,
                    fees_paise=0,
                    total_paise=13600,
                    payment_method="pending",
                    mode="live",
                )
            if case.get("previous"):
                previous, _ = context(True)
                state["previous_basket"] = [line.model_dump(mode="json") for line in previous.view.lines]
            start = time.monotonic()
            try:
                result = await chat.interpret(case["text"], session, state)
                return {
                    "id": case["id"],
                    "repeat": repeat,
                    "errors": failures(case, result),
                    "intent": result.model_dump(),
                    "seconds": round(time.monotonic() - start, 2),
                }
            except Exception as error:
                return {"id": case["id"], "repeat": repeat, "errors": [type(error).__name__]}

    try:
        rows = await asyncio.gather(*(one(case, repeat) for repeat in range(args.repeats) for case in CASES))
    finally:
        await master.client.close()
    report = {
        "model": settings.openai_text_model,
        "passed": sum(not row["errors"] for row in rows),
        "total": len(rows),
        "cases": rows,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}))
    for row in rows:
        if row["errors"]:
            print(row["id"], row["errors"])
    return report["passed"] == report["total"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model")
    parser.add_argument("--repeats", type=int, default=1)
    sys.exit(0 if asyncio.run(run(parser.parse_args())) else 1)
