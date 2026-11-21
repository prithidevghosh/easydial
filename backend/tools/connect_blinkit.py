"""Save an operator's manually authenticated Blinkit browser session locally."""

import asyncio
from pathlib import Path

from local_settings import ENV, save_settings

from easydial.providers.browser import BrowserSession
from easydial.providers.proxy import configure_blinkit_proxy
from easydial.settings import Settings


async def main():
    state_dir = Path(__file__).resolve().parents[1] / ".state"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    state_dir.chmod(0o700)
    state_path = state_dir / "blinkit.json"
    print("Log in on Blinkit's own page, then select your saved full delivery address.")
    print("Do not add products or place an order during this setup step.")
    profile = state_dir / "blinkit-profile"
    browser = BrowserSession(str(profile), str(state_path))
    configure_blinkit_proxy(browser, Settings(_env_file=ENV))
    # Explicit reconnect starts a fresh journey.
    if browser.proxy:
        browser.proxy.loaded = True
    try:
        page, context = await browser.open()
        await page.goto("https://blinkit.com", wait_until="domcontentloaded")
        await asyncio.to_thread(input, "After logging in and selecting the address, press Enter here: ")
        location = await page.locator("div[class*='LocationBar__Subtitle']").first.inner_text(timeout=10000)
        print("Selected header (private):", location)
        address = (
            await asyncio.to_thread(
                input, "Enter the full saved address exactly as shown in Blinkit's basket/address selector: "
            )
        ).strip()
        if not address or not location.strip():
            raise ValueError("An explicit full address is required")
        await browser.save()
        save_settings(
            ENV,
            {
                "BLINKIT_STORAGE_STATE": str(state_path),
                "BLINKIT_BROWSER_PROFILE": str(profile),
                "LIVE_ADDRESS_LABEL": address,
                "BLINKIT_VERIFIED_LOCATION": location.strip(),
                "BLINKIT_VERIFIED_ADDRESS": address,
            },
        )
    finally:
        await browser.close()
    print("Blinkit session and address saved locally. Use the same physical address for Swiggy.")
    print("Set LIVE_ADDRESS_LABEL in .env if you have not connected Swiggy, then restart the backend.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, Exception) as error:
        print(f"Blinkit setup stopped ({type(error).__name__}). Re-run locally; no secrets were displayed.")
        raise SystemExit(1) from None
