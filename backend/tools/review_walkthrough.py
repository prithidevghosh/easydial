"""Run the no-account shopping journey with real application replies and fixture stores."""

import argparse
import asyncio
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests import test_onboarding as onboarding
from tests import test_whatsapp_journey as journey


def show_reply(payload):
    interactive = payload.get("interactive", {})
    body = (
        interactive.get("body", {}).get("text")
        or payload.get("text", {}).get("body")
        or payload.get("image", {}).get("caption")
    )
    if body:
        print("Easy Dial>", body)
    action = interactive.get("action", {})
    choices = [b["reply"]["title"] for b in action.get("buttons", [])]
    for section in action.get("sections", []):
        choices.extend(row["title"] for row in section.get("rows", []))
    if choices:
        print("Choices>", " / ".join(choices))


async def walkthrough(auto):
    original_turn = onboarding.turn

    async def display_turn(runtime, text, *args, **kwargs):
        print("\nShopper>", text)
        if not auto:
            input("Press Enter to send this step: ")
        replies = await original_turn(runtime, text, *args, **kwargs)
        for reply in replies:
            show_reply(reply)
        return replies

    print("Guided review • simulated WhatsApp, model, store, OTP and payment.")
    with (
        TemporaryDirectory(prefix="easydial-review-") as tmp,
        patch.object(onboarding, "turn", display_turn),
        patch.object(journey, "turn", display_turn),
    ):
        await journey.test_onboarding_to_confirmation_and_fresh_basket(Path(tmp), True)
    print("\nVerified: simulated payment confirmed in order history and a fresh basket started.")


if __name__ == "__main__":
    logging.disable(logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auto", action="store_true", help="Advance without pressing Enter.")
    asyncio.run(walkthrough(parser.parse_args().auto))
