"""Connect a dedicated Instamart.in Chrome profile; no cart edits or purchases."""

import asyncio
from pathlib import Path

from local_settings import ENV, save_settings
from playwright.async_api import async_playwright


async def main():
    directory = Path(__file__).resolve().parents[1] / ".state"
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    profile = directory / "instamart-profile"
    print("Log in on Instamart.in and select the same physical address as Blinkit.")
    print("Enter phone/OTP only in Chrome. Do not place an order.")
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(str(profile), channel="chrome", headless=False)
        try:
            page = context.pages[0]
            await page.goto("https://instamart.in/", wait_until="domcontentloaded")
            await asyncio.to_thread(input, "After login and address selection, press Enter here: ")
            await page.get_by_test_id("user-account-button").wait_for(state="visible")
            location = await page.get_by_test_id("address-bar").get_by_test_id("address-line").inner_text()
            address = (
                await asyncio.to_thread(input, "Full saved address exactly as shown in your Instamart cart: ")
            ).strip()
            if not address or not location.strip():
                raise ValueError("A verified address is required")
            save_settings(
                ENV,
                {
                    "INSTAMART_BROWSER_PROFILE": str(profile),
                    "INSTAMART_VERIFIED_LOCATION": location.strip(),
                    "INSTAMART_VERIFIED_ADDRESS": address,
                },
            )
        finally:
            await context.close()
    print("Instamart profile saved locally. Set LIVE_ADDRESS_LABEL if needed, then restart the backend.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, Exception) as error:
        print(f"Setup stopped ({type(error).__name__}). Re-run locally; no secrets were displayed.")
        raise SystemExit(1) from None
